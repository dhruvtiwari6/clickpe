# Workflow B — User-Loan Matching (README & Guide)

> **Goal:** When a new batch of users has been ingested, match each of those users against the active loan-product catalog and persist every eligible `(user, product)` pair into the `matches` table, cheaply and quickly, so that Workflow C can email users their matches.
> **Runs locally via Docker Compose:** Postgres, n8n, and the `crawler-helper` Python service. No AWS required.

---

## 1. System Architecture

```
Webhook: {batch_id}  (or "Run Now" demo trigger)
        │
        ▼
       n8n  ──HTTP──►  crawler-helper (FastAPI)   /match/rules  /match/apply
        │                     │
        │                     └── Pure deterministic rules, testable & local
        │
        ├──SQL (pre-filter)──► PostgreSQL (users, loan_products)
        ├──HTTP──────────────► LLM API (Gemini free tier) — residual prose check only
        ├──SQL (upsert)──────► PostgreSQL (matches, match_runs)
        └──HTTP POST─────────► Workflow C (webhook trigger with batch_id)
```

### Responsibility Split
- **n8n:** Owns webhook intake, validation, branch control, SQL pushdowns, LLM orchestration, and triggering Workflow C.
- **PostgreSQL:** Executes Stage 1 SQL set-based pre-filter on indexed columns, collapses $N \times M$ grid to survivors, and stores idempotent match records.
- **crawler-helper (Python):** Runs Stage 2 deterministic multi-bound validation, qualitative prose gate detection, scoring, and verdict mapping.
- **LLM (Gemini):** Stage 3 residual insurance only — evaluates qualitative prose conditions when present and numbers alone cannot settle. Graceful offline fallback when no key is set.

---

## 2. The Optimization Treasure Hunt — The 3-Stage Funnel

```
                  ┌────────────────────────────────────────┐
                  │ N users in batch × M active products   │
                  │       (up to N·M candidate pairs)      │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ Stage 1: Indexed SQL Pre-filter (JOIN) │
                  │ Eliminates ~90-99% of pairs in DB      │
                  └───────────────────┬────────────────────┘
                                      │ survivors
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ Stage 2: Deterministic Python Engine   │
                  │ /match/rules verifies explicit bounds  │
                  └─────────┬────────────────────┬─────────┘
                            │                    │
              clear matches │                    │ ambiguous (qualitative prose)
                            ▼                    ▼
     ┌────────────────────────────┐    ┌───────────────────────────┐
     │ Upsert (match_stage='rule')│    │ Stage 3: Batched LLM/     │
     └────────────────────────────┘    │ Conservative Offline Mode │
                                       └─────────────┬─────────────┘
                                                     │ confirmed
                                                     ▼
                                       ┌───────────────────────────┐
                                       │ Upsert (match_stage='llm')│
                                       └───────────────────────────┘
```

1. **Stage 1 (SQL Pre-filter):** Set-based indexed JOIN inside Postgres. Evaluates numeric thresholds (income floor, credit score floor/ceiling, age floor/ceiling, employment status). Operates at zero network transfer cost for failed pairs.
2. **Stage 2 (Deterministic Rules):** Python helper checks full rule set and inspects `eligibility_text` for qualitative trigger phrases (e.g., tenure, stability, relationship). Clear matches are confirmed; only genuinely prose-gated pairs pass to Stage 3.
3. **Stage 3 (LLM Qualitative Check / Fallback):** Residual ambiguous pairs are sent in a single batched prompt with JSON Schema enforcement. In offline mode (or if the LLM call fails), conservative fallback accepts ambiguous pairs with `llm_skipped=true`.

---

## 3. Database Schema

### Tables & Views (`db/003_workflow_b_schema.sql`)

- **`users`**: Natural key `user_id`, email, `monthly_income`, `credit_score`, normalized `employment_status` (`{salaried, self_employed}`), `age`, and `batch_id`.
- **`matches`**: Composite unique `(user_id, product_id)`. Tracks `match_stage` (`rule` or `llm`), `score` ($[0, 1]$), `reasons` (jsonb explainability), `is_active`, and `notified` (default `false`).
- **`match_runs`**: Funnel metrics per run: `users_in_batch`, `active_products`, `pairs_considered`, `after_stage1`, `after_stage2`, `llm_pairs`, `matches_written`.
- **`v_last_match_run`**: Summary view displaying duration and the funnel drop-off of the latest execution.

### Seed Batch (`db/004_seed_users.sql`)
Pre-populates a test batch:
- Batch ID: `00000000-0000-0000-0000-000000000001`
- `u1`: Salaried, 85k income, 780 credit score, age 31
- `u2`: Self-employed, 28k income, 690 credit score, age 44
- `u3`: Salaried, 15k income, 640 credit score, age 23
- `u4`: Self-employed, 52k income, NULL credit score, age 59

---

## 4. Setup & Running

### 1. Apply Schema and Seeds
If Postgres has already initialized from Workflow A, apply the new SQL files:
```bash
docker compose exec -T postgres psql -U loan -d loanengine < db/003_workflow_b_schema.sql
docker compose exec -T postgres psql -U loan -d loanengine < db/004_seed_users.sql
```

### 2. Import Workflows in n8n
1. Open n8n UI at `http://localhost:5678`.
2. Workflows -> **Import from File**:
   - `n8n/workflow_b_user_matching.json`
   - `n8n/workflow_b_error_handler.json`
3. In `B - User-Loan Matching`, open Settings -> Set **Error Workflow** to `B - Error Handler`.
4. Ensure the `Postgres - LoanDB` credential is attached to all Postgres nodes.

### 3. Trigger the Workflow
- **Via Webhook (curl):**
  ```bash
  curl -X POST http://localhost:5678/webhook/match \
       -H "Content-Type: application/json" \
       -d '{"batch_id": "00000000-0000-0000-0000-000000000001"}'
  ```
- **Via UI:** Open the workflow and click **Execute Workflow** (the `Run Now (demo)` node triggers with the demo batch ID).

---

## 5. Verification & Observability Queries

### Inspect the Funnel
```sql
SELECT * FROM v_last_match_run;
```
Expected output showcases the funnel reduction:
`pairs_considered` -> `after_stage1` -> `after_stage2` -> `llm_pairs` -> `matches_written`.

### Inspect Generated Matches
```sql
SELECT m.user_id, lp.product_name, lp.lender_name, m.match_stage, m.score, m.reasons
FROM matches m
JOIN loan_products lp USING (product_id)
WHERE m.batch_id = '00000000-0000-0000-0000-000000000001' AND m.is_active
ORDER BY m.user_id, m.score DESC;
```

### Verify Idempotency & Handoff Integrity
- Re-running the webhook with the same `batch_id` performs atomic upserts: zero duplicate pairs are created.
- Existing `notified=true` flags are strictly preserved so Workflow C never re-notifies users.

---

## 6. Node-by-Node Pipeline Reference

| # | Node | Type | Purpose |
|---|---|---|---|
| 1 | Webhook | Webhook (POST) | Listens at `/webhook/match` for `{batch_id}` |
| 2 | Run Now (demo) | Manual Trigger | Single-click manual test from n8n canvas |
| 3 | Set Demo Batch | Set | Injects demo UUID `00000000-0000-0000-0000-000000000001` |
| 4 | Validate Input | IF | Ensures `batch_id` is present; else routes to Respond 400 |
| 5 | Respond 400 | Respond to Webhook | Returns HTTP 400 for invalid/missing payload |
| 6 | Start Run | Postgres | Inserts running entry in `match_runs` |
| 7 | Stage 1 Counts | Postgres | Fetches `users_in_batch` and `active_products` |
| 8 | Has Users? | IF | Checks if batch contains users; else routes to Finish Empty |
| 9 | Finish Empty | Postgres | Closes run with 0 matches if batch is empty |
| 10 | Stage 1 Pre-filter | Postgres | Set-based indexed SQL JOIN for hard eligibility bounds |
| 11 | Aggregate Pairs | Code | Collapses survivor records into a single array `{pairs}` |
| 12 | Stage 2 Rules | HTTP POST | Calls `/match/rules` on `crawler-helper` |
| 13 | Upsert Rule Matches | Postgres | Idempotent upsert of clear rule matches into `matches` |
| 14 | Any Ambiguous? | IF | Checks if any pairs need qualitative evaluation |
| 15 | LLM Key Present? | IF | Verifies LLM credentials/environment flag |
| 16 | Build LLM Request | Code | Constructs batched Gemini request with JSON schema |
| 17 | LLM Check | HTTP POST | Calls Gemini `generateContent` (on error -> Offline Fallback) |
| 18 | Parse + Apply LLM | HTTP POST | Calls `/match/apply` with extracted verdicts |
| 19 | Offline Fallback | HTTP POST | Calls `/match/apply` with `verdicts: null` |
| 20 | Upsert LLM Matches | Postgres | Idempotent upsert of LLM / fallback matches into `matches` |
| 21 | Aggregate Counts | Code | Aggregates all stage metrics for the run summary |
| 22 | Finish Run | Postgres | Updates `match_runs` with status `success` and funnel metrics |
| 23 | Trigger Workflow C | HTTP POST | Signals Workflow C webhook with `batch_id` |
| 24 | Respond 200 | Respond to Webhook | Returns HTTP 200 `{status: 'ok', run_id, matches_written}` |

