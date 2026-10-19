"""Workflow B Stage 2: deterministic eligibility rules over Stage-1 survivor pairs.
Pure functions, no I/O, unit-testable. Mirrors the SQL pre-filter plus the
qualitative-gate detection that decides what must go to the LLM (Stage 3).
"""
import re

EMPLOYMENT_VOCAB = {"salaried", "self_employed"}

# Phrases in eligibility_text that numbers alone cannot settle -> defer to LLM.
QUALITATIVE_PATTERNS = [
    r"\bstable\b",
    r"\bexisting\b",
    r"\brelationship\b",
    r"\btenure\b",
    r"\bno .*default",
    r"\bclean .*(history|record)\b",
    r"\bminimum .*(experience|vintage)\b",
    r"\bresident\b",
    r"\bresidence\b",
    r"\bdocument",
    r"\byears? (of|in) (service|business|employment)\b",
]
_QUAL_RE = re.compile("|".join(QUALITATIVE_PATTERNS), re.IGNORECASE)


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _normalize_emp_types(emp_types) -> set[str] | None:
    if emp_types is None:
        return None
    if isinstance(emp_types, str):
        s = emp_types.strip()
        if s.startswith("{") and s.endswith("}"):
            items = [x.strip().strip('"\'') for x in s[1:-1].split(",") if x.strip()]
            return set(items) if items else None
        if s:
            return {s}
        return None
    if isinstance(emp_types, (list, tuple, set)):
        items = [str(x).strip() for x in emp_types if str(x).strip()]
        return set(items) if items else None
    return None


def _passes_structured(u: dict, p: dict) -> tuple[bool, list[str], list[str]]:
    """Returns (ok, passed_reasons, fail_reasons). Mirrors Stage 1; defensive double-check."""
    passed, failed = [], []

    def bound(cond: bool, msg_ok: str, msg_bad: str):
        if cond:
            passed.append(msg_ok)
        else:
            failed.append(msg_bad)

    inc_req = _to_float(p.get("min_monthly_income"))
    u_inc = _to_float(u.get("monthly_income"))
    if inc_req is None:
        passed.append("income:no-requirement")
    else:
        bound(u_inc is not None and u_inc >= inc_req,
              f"income>={inc_req}", f"income<{inc_req}")

    cmin = _to_int(p.get("min_credit_score"))
    cmax = _to_int(p.get("max_credit_score"))
    cs = _to_int(u.get("credit_score"))
    if cmin is None and cmax is None:
        passed.append("credit:no-bound")
    else:
        if cs is None:
            failed.append("credit:missing")
        else:
            if cmin is not None:
                bound(cs >= cmin, f"credit>={cmin}", f"credit<{cmin}")
            if cmax is not None:
                bound(cs <= cmax, f"credit<={cmax}", f"credit>{cmax}")

    amin = _to_int(p.get("min_age"))
    amax = _to_int(p.get("max_age"))
    age = _to_int(u.get("age"))
    if amin is None and amax is None:
        passed.append("age:no-bound")
    else:
        if age is None:
            failed.append("age:missing")
        else:
            if amin is not None:
                bound(age >= amin, f"age>={amin}", f"age<{amin}")
            if amax is not None:
                bound(age <= amax, f"age<={amax}", f"age>{amax}")

    emp_set = _normalize_emp_types(p.get("employment_types"))
    if not emp_set:  # None or empty set = any
        passed.append("employment:any")
    else:
        status = u.get("employment_status")
        bound(status in emp_set,
              f"employment in {sorted(list(emp_set))}",
              f"employment '{status}' not in {sorted(list(emp_set))}")

    return (len(failed) == 0), passed, failed


def _score(u: dict, p: dict, llm_used: bool) -> float:
    """Simple explainable ranking in [0,1]. Margin above income/credit thresholds,
    minus a penalty when the match needed the LLM. For C's ordering only."""
    parts = []
    inc_req = _to_float(p.get("min_monthly_income"))
    u_inc = _to_float(u.get("monthly_income"))
    if inc_req and u_inc:
        parts.append(min(1.0, (u_inc - inc_req) / max(inc_req, 1.0)))

    cmin = _to_float(p.get("min_credit_score"))
    u_cs = _to_float(u.get("credit_score"))
    if cmin and u_cs:
        parts.append(min(1.0, max(0.0, (u_cs - cmin) / 100.0)))

    base = sum(parts) / len(parts) if parts else 0.6  # neutral prior when nothing to score
    if llm_used:
        base -= 0.1
    return round(max(0.0, min(1.0, base)), 3)


def classify_pair(u: dict, p: dict) -> dict:
    """Classify one survivor pair.
    verdict: 'match' (confirmed by rules), 'ambiguous' (needs LLM), 'reject'."""
    ok, passed, failed = _passes_structured(u, p)
    if not ok:
        return {"verdict": "reject", "reasons": {"failed": failed}}
    prose = (p.get("eligibility_text") or "")
    if _QUAL_RE.search(prose):
        return {"verdict": "ambiguous",
                "reasons": {"passed": passed, "qualitative_gate": True}}
    return {"verdict": "match",
            "stage": "rule",
            "score": _score(u, p, llm_used=False),
            "reasons": {"passed": passed}}


def classify_many(pairs: list[dict]) -> dict:
    """pairs: each has flattened user + product fields from Stage 1.
    Returns clear matches, ambiguous (for LLM), and rejects."""
    matches, ambiguous, rejects = [], [], []
    for row in pairs:
        u = {k: row.get(k) for k in
             ("user_id", "email", "monthly_income", "credit_score", "employment_status", "age")}
        p = {k: row.get(k) for k in
             ("product_id", "lender_name", "product_name", "min_monthly_income",
              "min_credit_score", "max_credit_score", "min_age", "max_age",
              "employment_types", "eligibility_text", "source_url")}
        res = classify_pair(u, p)
        rec = {"user_id": u["user_id"], "product_id": p["product_id"]}
        if res["verdict"] == "match":
            rec.update(stage="rule", score=res["score"], reasons=res["reasons"])
            matches.append(rec)
        elif res["verdict"] == "ambiguous":
            rec.update(reasons=res["reasons"],
                       eligibility_text=p["eligibility_text"],
                       user=u, product={"product_name": p["product_name"],
                                        "lender_name": p["lender_name"]})
            ambiguous.append(rec)
        else:
            rec.update(reasons=res["reasons"])
            rejects.append(rec)
    return {"matches": matches, "ambiguous": ambiguous, "rejects": rejects,
            "counts": {"matches": len(matches), "ambiguous": len(ambiguous),
                       "rejects": len(rejects)}}


def apply_llm_verdicts(ambiguous: list[dict], verdicts: dict) -> list[dict]:
    """verdicts: {f"{user_id}|{product_id}": {"eligible": bool, "clause": str}}.
    Turns LLM-confirmed ambiguous pairs into match records (stage='llm')."""
    out = []
    for a in ambiguous:
        key = f'{a["user_id"]}|{a["product_id"]}'
        v = verdicts.get(key)
        if v and v.get("eligible"):
            out.append({"user_id": a["user_id"], "product_id": a["product_id"],
                        "stage": "llm", "score": 0.5,
                        "reasons": {"llm": True, "clause": v.get("clause")}})
    return out


def conservative_fallback(ambiguous: list[dict]) -> list[dict]:
    """When no LLM key is configured (fully offline run): accept ambiguous pairs
    on the structured pass alone, flagged so it is auditable."""
    return [{"user_id": a["user_id"], "product_id": a["product_id"],
             "stage": "rule", "score": 0.4,
             "reasons": {**a.get("reasons", {}), "llm_skipped": True}}
            for a in ambiguous]

