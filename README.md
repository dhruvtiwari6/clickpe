# Loan Eligibility Engine

An automated pipeline that **ingests users**, **discovers personal-loan products**
by crawling websites, **matches** users to the products they qualify for, and
**notifies** them by email. Core business logic is orchestrated by a self-hosted
**n8n** instance; a small, stateless **Python (FastAPI)** helper does the pure
CPU work (parsing, normalization, deterministic matching). Everything runs
locally via **Docker Compose**; an AWS **serverless.yml** captures the equivalent
cloud architecture.

---

## 1. Architecture

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                Docker Compose (local)                     │
                         │                                                           │
  Minimal UI             │   ┌─────────┐        ┌───────────────────────────────┐   │
  (ui/index.html) ───────┼──►│  n8n    │◄──────►│  crawler-helper (FastAPI)     │   │
   upload users.csv      │   │         │  HTTP  │  /robots /render /parse        │   │
                         │   │         │        │  /normalize /match/* /ingest   │   │
                         │   └────┬────┘        └───────────────────────────────┘   │
                         │        │  SQL                                             │
                         │        ▼                                                  │
                         │   ┌──────────────┐     ┌────────────┐   ┌─────────────┐   │
                         │   │ PostgreSQL   │     │ mock-sites │   │  LLM API    │   │
                         │   │ users        │     │ (nginx)    │   │  Gemini     │   │
                         │   │ loan_products│     └────────────┘   │ (optional)  │   │
                         │   │ matches      │                      └─────────────┘   │
                         │   │ *_runs/logs  │                                        │
                         │   └──────────────┘                                        │
                         └──────────────────────────────────────────────────────────┘

  Pipeline (left → right):

   Ingest ──webhook(batch_id)──► Workflow B ──trigger──► Workflow C
   (CSV → users)                 (matching funnel)       (SES emails)

   Workflow A (scheduled, independent) ──► loan_products  ◄── read by B
```

**Workflows**
- **Ingest — User CSV Upload** (`workflow_ingest_users.json`): UI uploads a CSV →
  parse+normalize → bulk-upsert `users` with a `batch_id` → trigger Workflow B.
- **A — Loan Product Discovery** (`workflow_a_loan_discovery.json`): scheduled
  crawler. CSS-selector extraction with a **Gemini LLM fallback** when a layout
  changes; writes `loan_products`.
- **B — User-Loan Matching** (`workflow_b_user_matching.json`): the 3-stage
  **Optimization Treasure Hunt** funnel; writes `matches`; triggers Workflow C.
- **C — User Notification** (`workflow_c_user_notification.json`): emails each
  matched user via **AWS SES**; flips `notified`.

Each workflow has a paired `*_error_handler.json`.

**Design principle (consistent across all workflows):** n8n owns orchestration,
branching, and *all* DB writes; the Python helper is **stateless and
credential-free** (render/parse/normalize/match/ingest as pure functions). This
keeps logic unit-testable and the workflows demoable.

---

## 2. Repository layout

```
loan-eligibility-engine/
├── docker-compose.yml          # postgres + n8n + crawler-helper + mock-sites
├── .env.example                # PG creds, N8N_ENCRYPTION_KEY, CONTACT_EMAIL
├── data/
│   └── users.csv               # sample dataset (10,000 users)
├── db/                         # applied in filename order on first boot
│   ├── 001_workflow_a_schema.sql   # crawl_sources, loan_products, crawl_runs…
│   ├── 002_seed_sources.sql
│   ├── 003_workflow_b_schema.sql   # users, matches, match_runs (+ name col)
│   ├── 004_seed_users.sql          # demo batch so B/C run without ingestion
│   └── 005_add_user_name.sql       # idempotent ALTER for existing volumes
├── crawler-helper/             # FastAPI helper (stateless, 83 unit tests)
│   ├── app/{main,normalize,match,ingest,models}.py
│   └── tests/{test_parse,test_normalize,test_match,test_ingest}.py
├── mock-sites/                 # nginx fixtures for the crawler (v1/v1_broken/v2)
├── n8n/                        # the five workflows + four error handlers (JSON)
├── ui/
│   └── index.html              # minimal CSV upload UI
├── serverless/                 # AWS IaC (deploy-ready, undeployed)
│   ├── serverless.yml
│   └── src/{upload_url_handler,ingest_handler}.py + shared/ingest.py
└── docs/                       # ingestion, workflow-a, -b, -c, cloud-architecture
```

---

## 3. Setup & deployment (local)

### Prerequisites
Docker + Docker Compose. (An LLM API key is optional; everything degrades
gracefully offline.)

### 1. Configure env
```bash
cp .env.example .env
# set PG_PASSWORD and a 32+ char N8N_ENCRYPTION_KEY
```

### 2. Launch the stack
```bash
docker compose up -d --build
```
- Postgres runs `db/*.sql` on first boot (schema + seeds).
- `crawler-helper` builds from the Playwright base image.
- `mock-sites` serves fixtures at `http://localhost:8081`.
- n8n is at `http://localhost:5678`.

> Inside the Docker network use service names, not `localhost`:
> helper = `http://crawler-helper:8000`, mock sites = `http://mock-sites`,
> DB host = `postgres`.

### 3. Create n8n credentials
- **Postgres - LoanDB**: host `postgres`, port `5432`, db `loanengine`, user/password from `.env`.
- **LLM API Key** (optional): Header Auth, header `x-goog-api-key`, your Gemini free-tier key. Attach to `LLM Extract` (A) and `LLM Check` (B).
- **AWS** (for Workflow C): access key with `ses:SendEmail`. Attach to `Send via SES` (C).

### 4. Import workflows
Workflows → **Import from File**, then set each workflow's Settings → **Error
Workflow** to its paired handler, and attach the credentials above:

| Import | Error Workflow |
|---|---|
| `n8n/workflow_ingest_users.json` | `Ingest - Error Handler` |
| `n8n/workflow_a_loan_discovery.json` | `A - Error Handler` |
| `n8n/workflow_b_user_matching.json` | `B - Error Handler` |
| `n8n/workflow_c_user_notification.json` | `C - Error Handler` |

**Activate** the ingest, B, and C workflows so their webhooks are live.

### 5. Email safety (Workflow C)
Set n8n env vars `DEMO_INBOX` (your `you+alias@gmail.com`) and `SES_SENDER`
(a verified SES address). By default **all** mail is redirected to the test
inbox — the sample CSV addresses are never emailed. See `docs/workflow-c.md`.

---

## 4. Run the pipeline end-to-end

1. Open `ui/index.html`, confirm the webhook URL (`http://localhost:5678/webhook/ingest`),
   choose `data/users.csv`, click **Upload & Start Pipeline**. This stores the
   users and auto-triggers Workflow B, which in turn triggers Workflow C.
2. Or drive each stage manually:
   ```bash
   # ingest
   curl -X POST http://localhost:5678/webhook/ingest -F "file=@data/users.csv"
   # (B and C fire automatically; or trigger them directly:)
   curl -X POST http://localhost:5678/webhook/match  -H 'Content-Type: application/json' -d '{"batch_id":"<id>"}'
   curl -X POST http://localhost:5678/webhook/notify -H 'Content-Type: application/json' -d '{"batch_id":"<id>"}'
   ```
3. Run Workflow A (crawler) manually or let its daily schedule populate `loan_products`.

Inspect results:
```sql
SELECT * FROM v_last_run;          -- latest crawl (Workflow A)
SELECT * FROM v_last_match_run;    -- the matching funnel (Workflow B)
SELECT m.user_id, lp.product_name, m.match_stage, m.score
FROM matches m JOIN loan_products lp USING (product_id)
WHERE m.is_active ORDER BY m.user_id, m.score DESC;
```

---

## 5. Design decisions

### Web-crawling strategy (Workflow A)
Selector-first, LLM-fallback. n8n loops per source, honors `robots.txt`, fetches
(static or Playwright-rendered), and the helper parses with CSS selectors. If a
source yields too few candidates (layout changed), the **Gemini LLM fallback**
re-extracts from the page's clean text — and a **grounding guard** nulls any
field the model invents that isn't present verbatim on the page. A **sanity
gate** prevents an empty/degraded crawl from wiping the catalog. Steady state
makes **zero** LLM calls.

### The Optimization Treasure Hunt (Workflow B) — 3-stage funnel
1. **SQL pre-filter** — one set-based indexed JOIN in Postgres applies all hard
   numeric/categorical bounds, eliminating ~90–99% of `N×M` pairs at near-zero cost.
2. **Deterministic Python rules** — confirm clear matches and detect *qualitative*
   prose (e.g. "stable income", "existing relationship") that numbers can't settle.
3. **LLM only on the residual** — the few prose-ambiguous pairs go to Gemini in a
   single batched, schema-constrained call. No key → conservative offline fallback.

Per-run funnel metrics (`pairs_considered → after_stage1 → after_stage2 →
llm_pairs → matches_written`) are recorded in `match_runs` and exposed via
`v_last_match_run`.

### Scalable ingestion
The upload is decoupled from processing. Locally: UI → n8n webhook → stateless
parse/normalize → one set-based `users` upsert → webhook to B. In AWS
(`serverless/`): browser → presigned URL → **direct S3 PUT** → S3 event →
**Lambda** (the *same* `ingest.py` logic) → RDS → webhook to n8n. This avoids the
API-Gateway/Lambda request-size and timeout limits of a direct upload.
`employment_status` is normalized to the canonical `{salaried, self_employed}`
vocabulary in exactly one place, shared by local and cloud.

### Cloud architecture (IaC, not deployed)
`serverless/serverless.yml` declares the S3 bucket, the presigned-URL and
S3-event Lambdas, scoped IAM, SES identity, and an optional (cost-gated) RDS
instance. It is **deploy-ready but intentionally undeployed** — the assignment
accepts local deployment and expects no cloud cost. See `docs/cloud-architecture.md`.

---

## 6. Tests & verification

**Python helper — 83 unit tests, all passing:**
```bash
cd crawler-helper
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
```
Coverage: parsers, LLM grounding guard, sanity gate, deterministic matching, and
full CSV ingestion (employment mapping, numeric coercion, range checks).

**What has been verified in this repo**
- Helper logic: 83 passing tests.
- CSV ingestion against the real `data/users.csv`: 10,000 rows → 10,000 valid,
  0 rejected, vocabulary normalized.
- Workflow A LLM branch: candidate→`/normalize` round-trip, grounding guard drops
  hallucinated products.
- Workflow C SQL: run against a real Postgres 16 (schema + seed) — correct
  per-user aggregation and idempotent `notified` flips (no double-emails).
- `serverless package`: CloudFormation synthesized successfully.
- All workflow JSONs parse with no dangling node references.

**Not verified (environment limits, stated honestly)**
- No live n8n import/execution against the running runtime (JSON structure, SQL,
  and node logic were validated out-of-band; `typeVersion`s mirror the working
  Workflow B).
- No live AWS deploy or SES send (no AWS account; by design cost-free).
- Workflow A ships a selector spine + the LLM fallback branch; the optional
  JS-render branch is added in the n8n UI only if you register a `requires_js` source.

---

## 7. Further documentation
- `docs/ingestion.md` — CSV ingestion (local + cloud mapping)
- `docs/workflow-a.md` — crawler
- `docs/workflow-b.md` — matching funnel
- `docs/workflow-c.md` — SES notifications + demo-safety
- `docs/cloud-architecture.md` — AWS `serverless.yml` design
