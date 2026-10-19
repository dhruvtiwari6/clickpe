# Workflow A — Loan Product Discovery (README section)

Autonomous daily discovery of personal-loan products from public finance sites,
normalized and stored in `loan_products` for Workflow B. Everything runs locally
via Docker Compose; no AWS is required.

## Architecture (local)

```
Schedule / Manual trigger
        │
        ▼
      n8n  ──HTTP──►  crawler-helper (FastAPI)   /robots /render /parse /normalize
        │                     │
        │                     └── BeautifulSoup/lxml + Playwright, pure functions
        │
        ├──HTTP GET──► mock-sites (nginx: v1.html, v2.html)  + optional real sites
        ├──HTTP──────► LLM API (Gemini free tier) — fallback only, 0 calls in steady state
        └──SQL───────► PostgreSQL (crawl_sources, loan_products, crawl_runs, crawl_source_log)
```

Design: n8n owns scheduling, looping, branching, retries, and all DB writes. The
Python helper is stateless and credential-free — it only renders, parses, and
normalizes. See `workflow-a-architecture.md` and `workflow-a-implementation.md`
for the full rationale and reference code.

## Setup & deployment (local)

1. Copy env and fill values:
   ```bash
   cp .env.example .env
   # set PG_PASSWORD and a 32+ char N8N_ENCRYPTION_KEY
   ```
2. Start the stack:
   ```bash
   docker compose up -d --build
   ```
   - Postgres runs `db/*.sql` on first boot (schema + seed sources).
   - `crawler-helper` builds from the Playwright base image.
   - `mock-sites` serves the fixtures on `http://localhost:8081`.
   - n8n is at `http://localhost:5678`.
3. In n8n, create two credentials:
   - **Postgres - LoanDB**: host `postgres`, port `5432`, db `loanengine`, user/password from `.env`.
   - **LLM API Key** (optional): Header Auth, header `x-goog-api-key` with your free-tier key.
4. Import the workflows (Workflows → Import from File):
   - `n8n/workflow_a_loan_discovery.json`
   - `n8n/workflow_a_error_handler.json`
   Then open the loan-discovery workflow → Settings → set **Error Workflow** to `A - Error Handler`.
5. Attach the Postgres credential to every Postgres node and the LLM credential to the `LLM Extract` node (import leaves these unset on purpose — JSON is credential-free).

> Inside the Docker network use service names, never `localhost`:
> helper = `http://crawler-helper:8000`, mock sites = `http://mock-sites`, DB host = `postgres`.

## Run it

- Manual: open `A - Loan Product Discovery`, click **Execute Workflow** (uses `force_refresh=true`).
- Scheduled: the Schedule Trigger fires daily at 02:00 IST.

Inspect results:
```sql
SELECT * FROM v_last_run;
SELECT product_name, interest_rate_min, interest_rate_max,
       min_monthly_income, min_credit_score, employment_types
FROM loan_products WHERE is_active ORDER BY source_id, product_name;
```

## Node-by-node (built in UI, exported to JSON)

| # | Node | Type | Purpose |
|---|---|---|---|
| 1 | Daily 02:00 IST | Schedule Trigger | Cron `0 2 * * *`, Asia/Kolkata |
| 2 | Run Now (demo) | Manual Trigger | demo entry |
| 3 | Set Trigger Context | Set | `force_refresh`, `trigger_type` |
| 4 | Start Run | Postgres | INSERT crawl_runs RETURNING run_id |
| 5 | Load Active Sources | Postgres | sources + prev_active_count (SQL §7.1) |
| 6 | Per Source | Loop Over Items (batch 1) | isolate each source |
| 7 | Prep Context | Set | carry source_id/run_id/selector_config |
| 8 | Robots Check | HTTP POST | `/robots` |
| 9 | Allowed? | IF | else → Set Fail: blocked_robots |
| 10 | Needs JS? | IF | requires_js |
| 11a | Fetch Static Page | HTTP GET | retry 3×; on error → fetch_failed |
| 11b | Render JS Page | HTTP POST | `/render` |
| 12 | Normalize Fetch Output | Set | unify `html` |
| 13 | Parse Page | HTTP POST | `/parse` |
| 14 | Changed? | IF | fingerprint != last_hash OR force |
| 15 | Touch Unchanged | Postgres | SQL §7.2 |
| 16 | Enough Candidates? | IF | count ≥ expected AND mode≠llm |
| 17 | LLM Extract | HTTP POST | Gemini fallback (§8) |
| 18 | Parse LLM JSON | Code | parse model output |
| 19 | Use Selector Candidates | Set | method=selectors |
| 20 | Normalize & Validate | HTTP POST | `/normalize` |
| 21 | Gate Passed? | IF | else → gate_failed (DB untouched) |
| 22 | Upsert Products | Postgres | SQL §7.3 |
| 23 | Deactivate Stale | Postgres | SQL §7.4 |
| 24 | Set OK | Set | build log row |
| 25 | Log Source Result | Postgres | SQL §7.5 (all branches converge) |
| 26 | Update Source State | Postgres | SQL §7.6 |
| 26b | Politeness Wait | Wait | 3 s |
| 27 | Finish Run | Postgres | SQL §7.7 |
| 28 | Build Summary | Code | human-readable summary |

All SQL statements are in `workflow-a-implementation.md` §7. The imported JSON
carries these as node parameters; verify the Postgres node's query-parameter
syntax against your pinned n8n version.

### What the provided JSON contains

`n8n/workflow_a_loan_discovery.json` imports a **working selector-tier spine**
(20 nodes): both triggers → Start Run → Load Sources → per-source loop → robots →
static fetch (retry + error output) → parse → changed? → enough? → use selectors →
normalize → gate → upsert → deactivate → loop → finish run. This covers test
cases T1–T4 and T7 on the mock sources out of the box.

Finish the following in the n8n UI, then re-export over the file (see §6 of the
implementation spec for exact params):
- **LLM fallback branch**: `LLM Extract` (HTTP → Gemini), `Parse LLM JSON` (Code),
  wired from the false output of `Enough Candidates?` into `Normalize & Validate`
  with `method='llm'`. Needed for the layout-break demo (T5).
- **JS render branch**: `Needs JS?` IF + `Render JS Page` (HTTP → `/render`),
  only if you add a `requires_js=true` real source.
- **Logging convergence**: `Set OK` / `Set Fail: *` nodes → `Log Source Result`
  (SQL §7.5) → `Update Source State` (SQL §7.6) → `Politeness Wait` (3 s) → loop.
  The spine currently loops straight back after `Deactivate Stale`; add these so
  every branch logs to `crawl_source_log` and advances source state.
- **Build Summary** Code node after `Finish Run`.

This split is intentional: the implementation spec (§6, §14) warns that full
node JSON and the Postgres/LLM node param shapes vary by n8n version, so the
reliable path is to import the spine, finish the version-specific nodes in the
UI, test, and export. The Python helper — which holds all the extraction and
normalization logic — is fully implemented and tested (33 passing tests).

## Demo script (~2–3 min)

1. `docker compose ps` — all four services up.
2. Walk the canvas left→right.
3. Execute manually; open `Parse Page` output (raw strings) vs `Normalize & Validate` (clean numbers).
4. `SELECT * FROM v_last_run;` then the `loan_products` query above.
5. Run again → all `unchanged`, 0 LLM calls (the optimization).
6. Replace `mock-sites/v1.html` with `v1_broken.html`, run → LLM fallback fires, rows still valid (self-healing).
7. Note the gate: an empty crawl cannot wipe the catalog.

## Helper tests

```bash
cd crawler-helper
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt            # or the test subset
python -m pytest tests/ -v
```
33 tests cover every parser, the LLM grounding guard, the sanity gate, and
`/parse` against all three fixtures (v1=3, v1_broken=0, v2=3 candidates).
