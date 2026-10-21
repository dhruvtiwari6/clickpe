# Ingestion — User CSV Upload (README & Guide)

> **Goal:** Accept a bulk user CSV, parse + normalize it into clean `users` rows,
> store them under a single `batch_id`, and fire Workflow B (matching) for that
> batch. This is the **primary entry point** for the whole pipeline.
> **Runs locally via Docker Compose:** Postgres, n8n, and the `crawler-helper`
> Python service. No AWS required.

---

## 1. System Architecture

```
  Minimal UI (ui/index.html)
        │  multipart/form-data  (field: file)
        ▼
       n8n  ──Code──►  Extract CSV text from the upload
        │
        ├──HTTP──►  crawler-helper (FastAPI)  POST /ingest/parse
        │                     │
        │                     └── pure parse + normalize (stateless, credential-free)
        │
        ├──SQL (upsert)──►  PostgreSQL (users)      ← n8n owns the DB write
        └──HTTP POST─────►  Workflow B  (/webhook/match with {batch_id})
```

**Responsibility split** (same principle as Workflows A and B):
- **UI:** picks a file, POSTs it to the n8n ingestion webhook.
- **n8n:** owns the webhook, extracts the file text, calls the helper, performs the
  single set-based DB upsert, and triggers Workflow B. All stateful work lives here.
- **crawler-helper (Python):** stateless `POST /ingest/parse` — validates and
  normalizes rows, assigns a `batch_id`, reports rejects. No DB, no credentials.

### Why event-driven (and not a direct Lambda/API-Gateway upload)
The assignment explicitly warns that a direct API-Gateway→Lambda upload hits
request-size and timeout limits. Here the **upload is decoupled from processing**:
the browser hands the file to a webhook, the helper does pure CPU-bound
normalization, and the DB write is one batched set-based statement. The same
shape maps cleanly onto the cloud variant (S3 upload → S3 `ObjectCreated` event →
Lambda parse → RDS insert → webhook), so the local design is the production design
minus the managed services.

---

## 2. Data Contract

### Input CSV
Expected header (the sample `data/users.csv`, 10,000 rows):
```
user_id,name,email,monthly_income,credit_score,employment_status,age
```
- `user_id`, `email`, `employment_status` are **required** (rows missing them are rejected).
- `name` is stored for Workflow C's personalized emails.
- `monthly_income`, `credit_score`, `age` are nullable but range-checked when present.

### Normalization rules (`crawler-helper/app/ingest.py`)
| Field | Rule |
|---|---|
| `employment_status` | Mapped onto the canonical **`{salaried, self_employed}`** vocab (shared with Workflow B via `match.EMPLOYMENT_VOCAB`). Handles `Salaried`, `Self-Employed`, `self employed`, `SE`, `business`, `freelancer`, etc. Unmappable → row rejected. |
| `monthly_income` | Currency symbols, commas and spaces stripped (`₹ 85,000` → `85000`). Negative → rejected. |
| `credit_score` | Coerced to int; must be in **300–900** (CIBIL) or the row is rejected. Blank → `NULL`. |
| `age` | Coerced to int; must be in **18–100** or rejected. Blank → `NULL`. |
| `email` | Basic shape validation; invalid → rejected. |
| `batch_id` | One UUID per upload, attached to every row (so Workflow B matches exactly this batch). |

> **Vocabulary alignment (critical):** this is the single place the `employment_status`
> vocabulary is normalized, satisfying the Workflow A/B requirement that
> `users.employment_status` and `loan_products.employment_types` share the same
> `{salaried, self_employed}` vocabulary.

Rejected rows are **counted and sampled** (first 10) in the response rather than
silently dropped — visible in the UI and the webhook reply.

---

## 3. Setup & Running

### 1. Apply schema
`name` is included in `db/003_workflow_b_schema.sql` for a fresh boot. If your
Postgres volume already initialized before `name` existed, apply the idempotent
migration:
```bash
docker compose exec -T postgres psql -U loan -d loanengine < db/005_add_user_name.sql
```

### 2. Import the workflows in n8n
1. Open n8n at `http://localhost:5678`.
2. Workflows → **Import from File**:
   - `n8n/workflow_ingest_users.json`
   - `n8n/workflow_ingest_error_handler.json`
3. In `Ingest - User CSV Upload`, open Settings → set **Error Workflow** to `Ingest - Error Handler`.
4. Attach the **`Postgres - LoanDB`** credential to the `Bulk Insert Users` node
   (JSON is credential-free by convention).
5. **Activate** the workflow so the production webhook `/webhook/ingest` is live.

### 3. Open the UI
`ui/index.html` is a single static file — open it directly in a browser (or serve it):
```bash
# option A: just open the file
xdg-open ui/index.html
# option B: serve it
python3 -m http.server 8080 --directory ui   # then visit http://localhost:8080
```
Confirm the webhook URL field (`http://localhost:5678/webhook/ingest`), choose
`data/users.csv`, and click **Upload & Start Pipeline**.

### Or drive it with curl
```bash
# multipart file upload (production webhook)
curl -X POST http://localhost:5678/webhook/ingest -F "file=@data/users.csv"

# or send raw CSV text as JSON (handy for scripting)
curl -X POST http://localhost:5678/webhook/ingest \
     -H "Content-Type: application/json" \
     -d "{\"csv_text\": \"$(sed 's/\"/\\\"/g' data/users.csv)\"}"
```

### Test the helper endpoint directly
```bash
curl -X POST http://localhost:8000/ingest/parse \
     -H "Content-Type: application/json" \
     -d '{"csv_text": "user_id,name,email,monthly_income,credit_score,employment_status,age\nu1,Alice,a@b.com,85000,780,Self-Employed,31\n"}'
```

---

## 4. End-to-end flow

1. UI POSTs the CSV (multipart, field `file`) to `/webhook/ingest`.
2. **Extract CSV Text** (Code) decodes the uploaded binary to text.
3. **Parse + Normalize** (HTTP → `/ingest/parse`) returns `{batch_id, valid[], rejected_sample, ...}`.
4. **Has Valid Rows?** — if 0 valid, respond `400` with the rejection sample.
5. **Bulk Insert Users** — one `jsonb_to_recordset` set-based upsert into `users`,
   idempotent on `user_id` (re-uploading updates in place).
6. **Trigger Workflow B** — POST `{batch_id}` to `/webhook/match`.
7. **Respond 200** — `{status, batch_id, inserted, updated, valid_count, rejected_count}`.

---

## 5. Node-by-Node Pipeline Reference

| # | Node | Type | Purpose |
|---|---|---|---|
| 1 | Webhook Upload | Webhook (POST) | `/webhook/ingest`, multipart binary field `file` |
| 2 | Run Now (demo) | Manual Trigger | Single-click test from the canvas (expects `csv_text` JSON) |
| 3 | Extract CSV Text | Code | Decode the uploaded binary (or read `csv_text`) to text |
| 4 | Parse + Normalize | HTTP POST | Calls `crawler-helper` `/ingest/parse` |
| 5 | Has Valid Rows? | IF | `valid_count > 0` else route to Respond 400 |
| 6 | Respond 400 | Respond to Webhook | Returns `400` with `missing_headers` + `rejected_sample` |
| 7 | Bulk Insert Users | Postgres | Idempotent set-based upsert of the whole batch into `users` |
| 8 | Trigger Workflow B | HTTP POST | Fires `/webhook/match` with `{batch_id}` (the handoff) |
| 9 | Respond 200 | Respond to Webhook | Returns `{status, batch_id, inserted, updated, counts}` |

---

## 6. Verification

```sql
-- the batch landed, vocabulary is aligned, name is present
SELECT employment_status, count(*) FROM users GROUP BY 1;     -- only salaried / self_employed
SELECT user_id, name, email, monthly_income, credit_score, age, batch_id
FROM users ORDER BY created_at DESC LIMIT 5;
```
Re-uploading the same CSV performs in-place updates (no duplicate `user_id`s),
and the returned `batch_id` is what Workflow B uses to scope matching.

Helper unit tests (35 cases covering the employment mapping, numeric coercion,
range checks, row validation and full-CSV parsing):
```bash
cd crawler-helper && . .venv/bin/activate && python -m pytest tests/test_ingest.py -v
```
