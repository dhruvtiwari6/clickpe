"""CSV user ingestion: parse + normalize into clean `users` rows.

Pure functions, no I/O or DB access (mirrors the rest of crawler-helper, which
is stateless and credential-free -- n8n owns the DB write). The one job here is
to turn messy CSV text into rows that satisfy the `users` table contract and the
Workflow B matching vocabulary.

Key responsibilities:
  * map free-form employment_status onto the canonical {salaried, self_employed}
  * coerce numeric columns (monthly_income, credit_score, age) safely
  * validate required fields (user_id, email) and plausible ranges
  * attach a single batch_id to every row of a batch
  * report rejected rows (with reasons) instead of silently dropping them
"""
import csv
import io
import re
import uuid

# Single source of truth for the employment vocabulary. Inside the FastAPI
# package this comes from match.py; when this file is packaged standalone into
# the AWS Lambda (where match.py is not shipped) we fall back to the same
# literal. Keeping both paths means local helper and Lambda share one module.
try:
    from .match import EMPLOYMENT_VOCAB            # {"salaried", "self_employed"}
except ImportError:                                 # standalone (e.g. Lambda)
    EMPLOYMENT_VOCAB = {"salaried", "self_employed"}

# ---- employment_status normalization -------------------------------------
# Accept the many ways a CSV might spell the two canonical values.
_EMPLOYMENT_MAP = {
    "salaried": "salaried",
    "salary": "salaried",
    "employed": "salaried",
    "full time": "salaried",
    "fulltime": "salaried",
    "full_time": "salaried",
    "permanent": "salaried",
    "self employed": "self_employed",
    "self-employed": "self_employed",
    "self_employed": "self_employed",
    "selfemployed": "self_employed",
    "self": "self_employed",
    "se": "self_employed",
    "business": "self_employed",
    "freelance": "self_employed",
    "freelancer": "self_employed",
    "entrepreneur": "self_employed",
}

# Credit scores in India use the 300-900 CIBIL range; guard obviously bad data.
_CREDIT_MIN, _CREDIT_MAX = 300, 900
_AGE_MIN, _AGE_MAX = 18, 100
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

EXPECTED_HEADERS = {
    "user_id", "name", "email", "monthly_income",
    "credit_score", "employment_status", "age",
}


def normalize_employment(raw) -> str | None:
    """Map a raw employment string onto the canonical vocabulary, or None if it
    cannot be confidently mapped."""
    if raw is None:
        return None
    key = re.sub(r"\s+", " ", str(raw).strip().lower())
    if not key:
        return None
    if key in EMPLOYMENT_VOCAB:          # already canonical
        return key
    mapped = _EMPLOYMENT_MAP.get(key)
    if mapped:
        return mapped
    # last resort: collapse separators then retry (e.g. "Self Employed")
    collapsed = re.sub(r"[\s\-]+", "_", key)
    if collapsed in EMPLOYMENT_VOCAB:
        return collapsed
    return _EMPLOYMENT_MAP.get(collapsed.replace("_", " "))


def _to_number(v):
    """Parse a numeric cell, tolerating currency symbols, commas and spaces.
    Returns a float or None."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in {"na", "n/a", "null", "none", "-"}:
        return None
    s = re.sub(r"[,\s₹$]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _to_int_in_range(v, lo, hi):
    """Parse an int and bound-check it. Returns (value_or_None, ok)."""
    n = _to_number(v)
    if n is None:
        return None, True            # missing is allowed (nullable column)
    iv = int(round(n))
    if lo <= iv <= hi:
        return iv, True
    return None, False               # present but out of range -> invalid


def normalize_row(row: dict, batch_id: str) -> tuple[dict | None, dict | None]:
    """Normalize one CSV row dict.

    Returns (clean_row, None) on success or (None, rejection) on failure.
    `clean_row` matches the `users` table columns.
    """
    r = {(k or "").strip().lower(): (v.strip() if isinstance(v, str) else v)
         for k, v in row.items()}

    user_id = r.get("user_id")
    email = r.get("email")

    if not user_id:
        return None, {"row": row, "reason": "missing user_id"}
    if not email or not _EMAIL_RE.match(email):
        return None, {"row": row, "reason": f"invalid email: {email!r}"}

    employment = normalize_employment(r.get("employment_status"))
    if employment is None:
        return None, {"row": row,
                      "reason": f"unmappable employment_status: {r.get('employment_status')!r}"}

    income = _to_number(r.get("monthly_income"))
    if income is not None and income < 0:
        return None, {"row": row, "reason": f"negative monthly_income: {income}"}

    credit, credit_ok = _to_int_in_range(r.get("credit_score"), _CREDIT_MIN, _CREDIT_MAX)
    if not credit_ok:
        return None, {"row": row,
                      "reason": f"credit_score out of {_CREDIT_MIN}-{_CREDIT_MAX}: {r.get('credit_score')!r}"}

    age, age_ok = _to_int_in_range(r.get("age"), _AGE_MIN, _AGE_MAX)
    if not age_ok:
        return None, {"row": row,
                      "reason": f"age out of {_AGE_MIN}-{_AGE_MAX}: {r.get('age')!r}"}

    clean = {
        "user_id": str(user_id),
        "name": (r.get("name") or None),
        "email": email,
        "monthly_income": income,
        "credit_score": credit,
        "employment_status": employment,
        "age": age,
        "batch_id": batch_id,
    }
    return clean, None


def parse_csv(csv_text: str, batch_id: str | None = None) -> dict:
    """Parse + normalize a full CSV document.

    Returns a summary dict:
      batch_id, headers, total_rows, valid (list of clean rows),
      valid_count, rejected_count, rejected_sample, missing_headers.
    """
    batch = batch_id or str(uuid.uuid4())
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = sorted(h for h in ("user_id", "email", "employment_status") if h not in headers)

    valid, rejected = [], []
    total = 0
    if not missing:
        for row in reader:
            total += 1
            clean, rej = normalize_row(row, batch)
            if clean is not None:
                valid.append(clean)
            else:
                rejected.append(rej)

    return {
        "batch_id": batch,
        "headers": headers,
        "missing_headers": missing,
        "total_rows": total,
        "valid": valid,
        "valid_count": len(valid),
        "rejected_count": len(rejected),
        "rejected_sample": rejected[:10],
    }
