import pytest
from fastapi.testclient import TestClient

from app import match as M
from app.main import app

client = TestClient(app)


def test_classify_pair_income_pass():
    u = {"user_id": "u1", "monthly_income": 85000, "credit_score": 780, "employment_status": "salaried", "age": 31}
    p = {"product_id": 1, "product_name": "Prime Loan", "min_monthly_income": 50000, "eligibility_text": None}
    res = M.classify_pair(u, p)
    assert res["verdict"] == "match"
    assert res["stage"] == "rule"
    assert "income>=50000.0" in res["reasons"]["passed"]


def test_classify_pair_income_fail():
    u = {"user_id": "u3", "monthly_income": 15000, "credit_score": 640, "employment_status": "salaried", "age": 23}
    p = {"product_id": 2, "product_name": "High Income Loan", "min_monthly_income": 25000}
    res = M.classify_pair(u, p)
    assert res["verdict"] == "reject"
    assert any("income<25000" in f for f in res["reasons"]["failed"])


def test_classify_pair_missing_credit():
    u = {"user_id": "u4", "monthly_income": 52000, "credit_score": None, "employment_status": "self_employed", "age": 59}
    p = {"product_id": 3, "product_name": "Credit Check Loan", "min_credit_score": 700}
    res = M.classify_pair(u, p)
    assert res["verdict"] == "reject"
    assert "credit:missing" in res["reasons"]["failed"]


def test_classify_pair_all_bounds_null():
    u = {"user_id": "u4", "monthly_income": 52000, "credit_score": None, "employment_status": "self_employed", "age": 59}
    p = {"product_id": 4, "product_name": "Open Loan", "min_monthly_income": None, "min_credit_score": None,
         "max_credit_score": None, "min_age": None, "max_age": None, "employment_types": None}
    res = M.classify_pair(u, p)
    assert res["verdict"] == "match"
    assert "income:no-requirement" in res["reasons"]["passed"]
    assert "credit:no-bound" in res["reasons"]["passed"]
    assert "employment:any" in res["reasons"]["passed"]


def test_classify_pair_ambiguous_qualitative_prose():
    u = {"user_id": "u1", "monthly_income": 85000, "credit_score": 780, "employment_status": "salaried", "age": 31}
    p = {"product_id": 5, "product_name": "Relationship Loan", "min_monthly_income": 30000,
         "eligibility_text": "Applicants must have a stable income, clean history, and existing relationship."}
    res = M.classify_pair(u, p)
    assert res["verdict"] == "ambiguous"
    assert res["reasons"].get("qualitative_gate") is True


def test_classify_pair_employment_type_mismatch():
    u = {"user_id": "u2", "monthly_income": 28000, "credit_score": 690, "employment_status": "self_employed", "age": 44}
    p = {"product_id": 6, "product_name": "Salaried Only", "employment_types": ["salaried"]}
    res = M.classify_pair(u, p)
    assert res["verdict"] == "reject"
    assert any("employment 'self_employed' not in" in f for f in res["reasons"]["failed"])


def test_classify_pair_employment_type_match():
    u = {"user_id": "u1", "monthly_income": 85000, "credit_score": 780, "employment_status": "salaried", "age": 31}
    p = {"product_id": 7, "product_name": "Salaried Only", "employment_types": ["salaried"]}
    res = M.classify_pair(u, p)
    assert res["verdict"] == "match"
    assert any("employment in" in p_str for p_str in res["reasons"]["passed"])


def test_classify_pair_age_bounds():
    u_young = {"user_id": "u_y", "age": 19}
    u_old = {"user_id": "u_o", "age": 65}
    u_mid = {"user_id": "u_m", "age": 30}
    p = {"product_id": 8, "min_age": 21, "max_age": 60}

    assert M.classify_pair(u_young, p)["verdict"] == "reject"
    assert M.classify_pair(u_old, p)["verdict"] == "reject"
    assert M.classify_pair(u_mid, p)["verdict"] == "match"


def test_score_ranking():
    u_high = {"monthly_income": 100000, "credit_score": 800}
    u_low = {"monthly_income": 26000, "credit_score": 705}
    p = {"min_monthly_income": 25000, "min_credit_score": 700}

    s_high = M._score(u_high, p, llm_used=False)
    s_low = M._score(u_low, p, llm_used=False)
    s_llm = M._score(u_high, p, llm_used=True)

    assert s_high > s_low
    assert s_high > s_llm


def test_apply_llm_verdicts():
    ambiguous = [
        {"user_id": "u2", "product_id": 10, "reasons": {"qualitative_gate": True}},
        {"user_id": "u3", "product_id": 11, "reasons": {"qualitative_gate": True}},
    ]
    verdicts = {
        "u2|10": {"eligible": True, "clause": "stable income documented"},
        "u3|11": {"eligible": False, "clause": "unmet tenure requirement"},
    }
    matches = M.apply_llm_verdicts(ambiguous, verdicts)
    assert len(matches) == 1
    assert matches[0]["user_id"] == "u2"
    assert matches[0]["product_id"] == 10
    assert matches[0]["stage"] == "llm"
    assert matches[0]["reasons"]["clause"] == "stable income documented"


def test_conservative_fallback():
    ambiguous = [
        {"user_id": "u2", "product_id": 10, "reasons": {"passed": ["income>=25000"]}}
    ]
    fallback = M.conservative_fallback(ambiguous)
    assert len(fallback) == 1
    assert fallback[0]["user_id"] == "u2"
    assert fallback[0]["product_id"] == 10
    assert fallback[0]["stage"] == "rule"
    assert fallback[0]["reasons"]["llm_skipped"] is True


def test_classify_many():
    pairs = [
        {"user_id": "u1", "monthly_income": 85000, "credit_score": 780, "employment_status": "salaried", "age": 31,
         "product_id": 1, "product_name": "Clear Match", "min_monthly_income": 50000},
        {"user_id": "u2", "monthly_income": 28000, "credit_score": 690, "employment_status": "self_employed", "age": 44,
         "product_id": 2, "product_name": "Prose Match", "min_monthly_income": 20000,
         "eligibility_text": "Requires stable income for 2+ years."},
        {"user_id": "u3", "monthly_income": 15000, "credit_score": 640, "employment_status": "salaried", "age": 23,
         "product_id": 3, "product_name": "Rejected", "min_monthly_income": 50000},
    ]
    res = M.classify_many(pairs)
    assert len(res["matches"]) == 1
    assert res["matches"][0]["user_id"] == "u1"
    assert len(res["ambiguous"]) == 1
    assert res["ambiguous"][0]["user_id"] == "u2"
    assert len(res["rejects"]) == 1
    assert res["rejects"][0]["user_id"] == "u3"
    assert res["counts"] == {"matches": 1, "ambiguous": 1, "rejects": 1}


def test_api_match_rules():
    payload = {
        "pairs": [
            {"user_id": "u1", "monthly_income": 85000, "credit_score": 780, "employment_status": "salaried", "age": 31,
             "product_id": 1, "product_name": "Clear Match", "min_monthly_income": 50000}
        ]
    }
    resp = client.post("/match/rules", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["matches"]) == 1
    assert data["matches"][0]["user_id"] == "u1"


def test_api_match_apply_with_verdicts():
    payload = {
        "ambiguous": [
            {"user_id": "u2", "product_id": 10, "reasons": {"qualitative_gate": True}}
        ],
        "verdicts": {
            "u2|10": {"eligible": True, "clause": "stable income verified"}
        }
    }
    resp = client.post("/match/apply", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["llm_matches"]) == 1
    assert data["llm_matches"][0]["stage"] == "llm"


def test_api_match_apply_offline_fallback():
    payload = {
        "ambiguous": [
            {"user_id": "u2", "product_id": 10, "reasons": {"passed": ["income>=25000"]}}
        ],
        "verdicts": None
    }
    resp = client.post("/match/apply", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["llm_matches"]) == 1
    assert data["llm_matches"][0]["reasons"]["llm_skipped"] is True

