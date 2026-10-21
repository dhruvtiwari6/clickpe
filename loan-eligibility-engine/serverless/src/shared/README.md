# Shared ingestion module (single source of truth)

`shared/ingest.py` is the **same** pure CSV normalization module used by the
local FastAPI helper at `crawler-helper/app/ingest.py`. Both the local n8n
pipeline and the AWS Lambda use identical parsing/normalization logic, so a user
CSV is normalized the exact same way whether it comes in via the local
`/webhook/ingest` or via an S3 upload in the cloud.

The module imports its employment vocabulary defensively:

```python
try:
    from .match import EMPLOYMENT_VOCAB      # inside the FastAPI package
except ImportError:
    EMPLOYMENT_VOCAB = {"salaried", "self_employed"}   # standalone (Lambda)
```

so the identical file works in both environments.

## Keeping the copy in sync

`shared/ingest.py` is a committed copy (kept in-repo so `serverless package`
works without reaching outside the service dir). Re-sync it after any change to
the canonical file:

```bash
# from loan-eligibility-engine/
cp crawler-helper/app/ingest.py serverless/src/shared/ingest.py
```

The unit tests for this logic live with the canonical copy
(`crawler-helper/tests/test_ingest.py`, 35 cases) and cover both environments
since the code is identical.
