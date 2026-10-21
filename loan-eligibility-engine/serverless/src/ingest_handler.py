"""S3-event ingest Lambda.

Cloud equivalent of the local n8n "Ingest - User CSV Upload" workflow:

    S3 ObjectCreated (uploads/*.csv)
        -> this handler
           -> ingest.parse_csv()   (the SAME normalization as the local helper)
           -> bulk upsert into RDS Postgres `users`
           -> POST {batch_id} to the n8n /webhook/match (triggers Workflow B)

Design notes
------------
* The upload path is S3, not API Gateway -> no request-size / timeout limits on
  the upload itself (the assignment's "professional, event-driven pattern").
* Normalization logic is shared with the local FastAPI helper via
  `shared/ingest.py` (one source of truth; see shared/README).
* psycopg (v3) talks to RDS; the DB write is one set-based statement, mirroring
  the local n8n Postgres upsert node.
"""
import json
import os
import urllib.parse
import urllib.request

import boto3

from shared import ingest as ING

try:
    import psycopg  # psycopg v3
except ImportError:  # pragma: no cover - only at runtime in Lambda
    psycopg = None

s3 = boto3.client("s3")

DB_DSN = os.environ.get("DB_DSN", "")
N8N_MATCH_WEBHOOK = os.environ.get("N8N_MATCH_WEBHOOK", "")

UPSERT_SQL = """
WITH incoming AS (
  SELECT * FROM jsonb_to_recordset(%s::jsonb)
    AS x(user_id text, name text, email text, monthly_income numeric,
          credit_score int, employment_status text, age int, batch_id uuid)
),
up AS (
  INSERT INTO users (user_id, name, email, monthly_income, credit_score,
                     employment_status, age, batch_id)
  SELECT user_id, name, email, monthly_income, credit_score,
         employment_status, age, batch_id
  FROM incoming
  ON CONFLICT (user_id) DO UPDATE SET
    name = EXCLUDED.name, email = EXCLUDED.email,
    monthly_income = EXCLUDED.monthly_income, credit_score = EXCLUDED.credit_score,
    employment_status = EXCLUDED.employment_status, age = EXCLUDED.age,
    batch_id = EXCLUDED.batch_id
  RETURNING (xmax = 0) AS inserted
)
SELECT count(*) FILTER (WHERE inserted) AS inserted,
       count(*) FILTER (WHERE NOT inserted) AS updated
FROM up;
"""


def _store(valid_rows):
    """Bulk upsert the batch into RDS. Returns (inserted, updated)."""
    if psycopg is None:
        raise RuntimeError("psycopg not available in runtime")
    payload = json.dumps(valid_rows)
    with psycopg.connect(DB_DSN, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(UPSERT_SQL, (payload,))
            row = cur.fetchone()
        conn.commit()
    return (row[0], row[1]) if row else (0, 0)


def _trigger_matching(batch_id):
    """Fire the n8n matching webhook for the batch just stored (best-effort)."""
    if not N8N_MATCH_WEBHOOK:
        return {"triggered": False, "reason": "N8N_MATCH_WEBHOOK not set"}
    data = json.dumps({"batch_id": batch_id}).encode("utf-8")
    req = urllib.request.Request(
        N8N_MATCH_WEBHOOK, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"triggered": True, "status": resp.status}
    except Exception as e:  # don't fail the whole ingest if the webhook is down
        return {"triggered": False, "error": str(e)}


def handler(event, context):
    """S3 ObjectCreated handler. One invocation per uploaded object."""
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])

        obj = s3.get_object(Bucket=bucket, Key=key)
        csv_text = obj["Body"].read().decode("utf-8")

        parsed = ING.parse_csv(csv_text)              # SAME logic as local helper
        batch_id = parsed["batch_id"]

        if parsed["valid_count"] == 0:
            results.append({
                "key": key, "batch_id": batch_id, "stored": 0,
                "rejected": parsed["rejected_count"],
                "missing_headers": parsed["missing_headers"],
                "status": "no_valid_rows",
            })
            continue

        inserted, updated = _store(parsed["valid"])
        trig = _trigger_matching(batch_id)
        results.append({
            "key": key, "batch_id": batch_id,
            "inserted": inserted, "updated": updated,
            "valid_count": parsed["valid_count"],
            "rejected_count": parsed["rejected_count"],
            "matching": trig, "status": "ok",
        })

    return {"statusCode": 200, "body": json.dumps({"results": results})}
