# Cloud Architecture (AWS) — `serverless/`

> **Status: deploy-ready but intentionally undeployed.** The assignment accepts
> local deployment and states you are *not* expected to incur cloud cost. This
> `serverless.yml` is the Infrastructure-as-Code expression of the **cloud
> version** of the same pipeline that runs locally via Docker Compose. It was
> validated with `serverless package` (CloudFormation synthesized successfully);
> it has **not** been `serverless deploy`-ed to a live AWS account.

---

## 1. Cloud topology

```
  Browser UI
     │  (1) GET /upload-url?filename=users.csv
     ▼
  API Gateway (HTTP API) ──► uploadUrl Lambda ──► returns presigned S3 PUT URL
     │
     │  (2) HTTP PUT file bytes  (straight to S3 — bypasses API GW size limits)
     ▼
  S3 bucket  (…-uploads / uploads/*.csv)
     │
     │  (3) s3:ObjectCreated event
     ▼
  ingest Lambda
     ├── shared/ingest.parse_csv()   ← SAME normalization as the local helper
     ├── psycopg bulk upsert ─────────► RDS for PostgreSQL (users)
     └── POST {batch_id} ─────────────► n8n /webhook/match  (triggers Workflow B)
                                              │
                                              ▼
                                         Workflow C ──► Amazon SES (emails)
```

**Why this shape (the assignment's "professional, event-driven pattern"):** the
large file bytes go **directly to S3** via a presigned URL, never through API
Gateway/Lambda (which cap synchronous request bodies at ~6 MB and 30 s). Parsing
happens asynchronously in an S3-triggered Lambda, so upload size/time is bounded
only by S3. This is the scalable alternative to a direct Lambda-backed upload.

---

## 2. What the stack declares

| Resource | Purpose |
|---|---|
| `AWS::S3::Bucket` (uploads) | Receives user CSVs under `uploads/`; emits the ingest event |
| `uploadUrl` Lambda + HTTP API routes | Issues short-lived presigned PUT URLs (`GET`/`OPTIONS /upload-url`) |
| `ingest` Lambda + S3 notification | Parse → normalize → RDS upsert → trigger n8n matching |
| `AWS::IAM::Role` | Scoped: `s3:GetObject/PutObject` on the bucket, `ses:SendEmail` |
| `AWS::SES::EmailIdentity` | Verified sender for Workflow C (gated; see below) |
| `AWS::RDS::DBInstance` + `SecurityGroup` | Postgres `users`/`matches`/… store (gated, optional) |
| Outputs | `UploadUrlEndpoint`, `UploadBucketName`, `RdsEndpoint` |

### Cost-aware gating
RDS and SES are wrapped in a CloudFormation **`CreateRds` condition** driven by
`PROVISION_RDS` (default `false`). With the default, a deploy creates only the
S3 bucket, the two Lambdas, the HTTP API, and the IAM role — and you point
`DB_DSN` at a DB you already have. Set `PROVISION_RDS=true` to let the stack
stand up a `db.t4g.micro` Postgres (free-tier-eligible) and a verified SES sender.

---

## 3. One source of truth for normalization

`serverless/src/shared/ingest.py` is the **same module** as
`crawler-helper/app/ingest.py`. The local n8n pipeline (via the FastAPI helper)
and the cloud ingest Lambda therefore normalize a CSV **identically** — same
`employment_status` → `{salaried, self_employed}` mapping, same numeric coercion
and range checks, same `batch_id` assignment. Its 35 unit tests
(`crawler-helper/tests/test_ingest.py`) cover both because the code is identical.
Re-sync after edits: `npm run sync-ingest` (from `serverless/`).

---

## 4. Local ⇄ Cloud mapping

| Concern | Local (Docker Compose) | Cloud (`serverless.yml`) |
|---|---|---|
| Upload entry point | UI → n8n `/webhook/ingest` | UI → presigned URL → **S3** |
| File transport | multipart through n8n | **direct S3 PUT** (no API GW body) |
| Trigger | webhook | **S3 `ObjectCreated` event** |
| Parse + normalize | FastAPI helper `/ingest/parse` | **ingest Lambda** (`shared/ingest.py`) |
| DB write | n8n Postgres upsert node | **psycopg upsert** in the Lambda |
| Users DB | Postgres container | **RDS for PostgreSQL** |
| Matching handoff | POST `/webhook/match` | POST `/webhook/match` (same) |
| Notifications | n8n SES node (Workflow C) | same n8n, SES identity from this stack |

The business logic (normalization, the `users` upsert SQL, the matching handoff)
is deliberately identical across both — only the *transport and triggers* differ.

---

## 5. How to validate / deploy

```bash
cd serverless
npm install                 # serverless + serverless-python-requirements

# validate config (no AWS, no cost):
npx serverless print
npx serverless package      # synthesizes CloudFormation into .serverless/

# deploy (requires an AWS account + credentials; incurs cost only if you do this):
export DB_DSN='postgresql://user:pass@host:5432/loanengine'
export N8N_MATCH_WEBHOOK='https://<your-n8n-host>/webhook/match'
export SES_SENDER='you+sender@example.com'
# optional managed DB + SES identity:
export PROVISION_RDS=true DB_PASSWORD='...'
npx serverless deploy --stage dev --region ap-south-1
```

> **Packaging note:** `serverless-python-requirements` builds the `psycopg`
> binary wheel for the Lambda runtime; with `dockerizePip: true` it needs Docker
> (or a matching local Python) at package time. `serverless package` was verified
> here; a full build additionally requires Docker on the packaging host.

---

## 6. Verification performed (this repo)

- `serverless print` resolves the full config with no warnings/errors.
- `serverless package` **successfully synthesized** the CloudFormation template:
  2 Lambda functions, the uploads S3 bucket + policy + event notification, the
  scoped IAM role, API Gateway v2 HTTP API with the two routes, the SES identity,
  the security group, the RDS instance, and all Outputs.
- `shared/ingest.py` imports and normalizes correctly **standalone** (no
  `match.py`), proving the Lambda uses the same logic as the local helper.
- **Not** performed: `serverless deploy` (no AWS account; by design cost-free).
