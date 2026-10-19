"""Normalization & validation for Workflow A.

All parsers take raw scraped strings and return clean, typed values. Pure
functions, unit-tested. The LLM/selector tiers both feed normalize_many().
"""
import hashlib
import json
import math
import re

MULT = {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
        "cr": 1e7, "crore": 1e7, "crores": 1e7}


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip() if s is not None else ""


def _money_values(text) -> list[float]:
    t = _clean(text).lower().replace(",", "")
    out = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(thousand|lakhs?|lacs?|crores?|cr|k)?\b", t):
        out.append(float(m.group(1)) * MULT.get(m.group(2), 1))
    return out


def parse_rate(text):
    t = _clean(text).replace(",", "")
    nums = re.findall(r"(\d+(?:\.\d+)?)\s*%", t) or re.findall(r"(\d+(?:\.\d+)?)", t)
    vals = sorted(float(n) for n in nums)
    if not vals:
        return (None, None)
    if len(vals) == 1:
        return (vals[0], None)
    return (vals[0], vals[-1])


def parse_income(text):
    v = _money_values(text)
    return v[0] if v else None                      # a minimum-income field: first number


def parse_amount_range(text):
    v = _money_values(text)
    if not v:
        return (None, None)
    if re.search(r"\b(up ?to|upto|max)", _clean(text).lower()) and len(v) == 1:
        return (None, v[0])
    return (min(v), max(v)) if len(v) > 1 else (v[0], None)


def parse_credit(text):
    nums = [int(n) for n in re.findall(r"\b(\d{3})\b", _clean(text)) if 300 <= int(n) <= 900]
    if not nums:
        return (None, None)
    return (min(nums), max(nums)) if len(nums) > 1 else (nums[0], None)


def parse_age(text):
    nums = [int(n) for n in re.findall(r"\b(\d{2})\b", _clean(text)) if 18 <= int(n) <= 100]
    if not nums:
        return (None, None)
    return (min(nums), max(nums)) if len(nums) > 1 else (nums[0], None)


def parse_tenure_months(text):
    t = _clean(text).lower()
    nums = [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)", t)]
    if not nums:
        return (None, None)
    f = 12 if re.search(r"\byears?|\byrs?\b", t) else 1
    nums = [int(round(n * f)) for n in nums]
    return (min(nums), max(nums)) if len(nums) > 1 else (nums[0], None)


def parse_employment(text):
    t = _clean(text).lower()
    out = []
    if "salaried" in t:
        out.append("salaried")
    if re.search(r"self[\s-]?employed|business|professional", t):
        out.append("self_employed")
    return out or None


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", _clean(s).lower()).strip("-")


def _grounded(raw, page_text_norm):
    return _clean(raw).lower() in page_text_norm


def _ordered(lo, hi):
    return lo is None or hi is None or lo <= hi


def normalize_candidate(c: dict, source_id, source_url, method, page_text=None):
    errs, warns = [], []
    page_norm = _clean(page_text).lower() if (method == "llm" and page_text) else None

    def field(name):
        raw = c.get(name)
        if raw is None or _clean(raw) == "":
            return None
        if page_norm is not None and not _grounded(raw, page_norm):
            warns.append(f"ungrounded:{name}")
            return None
        return raw

    name = _clean(c.get("product_name"))
    if not name:
        return None, ["missing_product_name"]
    if page_norm is not None and not _grounded(name, page_norm):
        return None, ["ungrounded:product_name"]

    lender = _clean(c.get("lender_name")) or None
    rate_min, rate_max = parse_rate(field("interest_rate"))
    amt_min, amt_max = parse_amount_range(field("loan_amount"))
    ten_min, ten_max = parse_tenure_months(field("tenure"))
    income = parse_income(field("min_monthly_income"))
    cs_min, cs_max = parse_credit(field("credit_score"))
    age_min, age_max = parse_age(field("age"))
    emp = parse_employment(field("employment"))

    if (rate_min is not None and not (0 < rate_min <= 60)) or \
       (rate_max is not None and not (0 < rate_max <= 60)):
        errs.append("rate_out_of_range")
    if income is not None and not (0 <= income <= 10_000_000):
        errs.append("income_out_of_range")
    for lo, hi, label in [(rate_min, rate_max, "rate"), (amt_min, amt_max, "amount"),
                          (ten_min, ten_max, "tenure"), (cs_min, cs_max, "credit"), (age_min, age_max, "age")]:
        if not _ordered(lo, hi):
            errs.append(f"range_inverted:{label}")
    if rate_min is None and all(x is None for x in (income, cs_min, age_min, emp)):
        errs.append("insufficient_data")
    if errs:
        return None, errs

    key_fields = [rate_min, income, cs_min, amt_min, ten_min, emp]
    confidence = max(0.0, round(sum(x is not None for x in key_fields) / 6 - 0.1 * len(warns), 2))

    rec = {
        "source_id": source_id,
        "product_key": slugify(f"{lender or ''} {name}"),
        "lender_name": lender, "product_name": name,
        "interest_rate_min": rate_min, "interest_rate_max": rate_max,
        "min_loan_amount": amt_min, "max_loan_amount": amt_max,
        "min_tenure_months": ten_min, "max_tenure_months": ten_max,
        "min_monthly_income": income,
        "min_credit_score": cs_min, "max_credit_score": cs_max,
        "min_age": age_min, "max_age": age_max,
        "employment_types": emp,
        "processing_fee_text": _clean(c.get("processing_fee")) or None,
        "eligibility_text": _clean(c.get("eligibility_text"))[:1500] or None,
        "source_url": c.get("detail_url") or source_url,
        "extraction_method": method,
        "confidence": confidence,
        "raw_extract": c,
    }
    business = {k: rec[k] for k in rec if k not in ("raw_extract", "confidence", "source_url", "extraction_method")}
    rec["content_hash"] = hashlib.sha256(json.dumps(business, sort_keys=True, default=str).encode()).hexdigest()
    return rec, warns


def normalize_many(cands, source_id, source_url, method, page_text=None):
    best, rejected = {}, []
    for c in cands:
        rec, info = normalize_candidate(c, source_id, source_url, method, page_text)
        if rec is None:
            rejected.append({"candidate": {k: c.get(k) for k in ("product_name", "interest_rate")}, "reasons": info})
            continue
        k = rec["product_key"]
        if k not in best or rec["confidence"] > best[k]["confidence"]:
            best[k] = rec                                   # dedupe by product_key
    return list(best.values()), rejected


def gate(valid_count: int, prev_active: int, min_ratio: float = 0.5):
    if valid_count == 0:
        return False, "no_valid_products"
    if prev_active > 0 and valid_count < math.ceil(prev_active * min_ratio):
        return False, f"product_count_dropped ({valid_count} vs {prev_active} previously)"
    return True, "ok"
