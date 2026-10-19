import pytest

from app import normalize as N


@pytest.mark.parametrize("text,expected", [
    ("10.49% - 24% p.a.", (10.49, 24.0)),
    ("Starting at 11.25%", (11.25, None)),
    ("12.99%", (12.99, None)),
    ("", (None, None)),
])
def test_parse_rate(text, expected):
    assert N.parse_rate(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("\u20b925,000 per month", 25000.0),
    ("Rs. 1.5 Lakh", 150000.0),
    ("30k", 30000.0),
    ("INR 30,000/month", 30000.0),
])
def test_parse_income(text, expected):
    assert N.parse_income(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("CIBIL 750 and above", (750, None)),
    ("650 - 750", (650, 750)),
    ("700+", (700, None)),
])
def test_parse_credit(text, expected):
    assert N.parse_credit(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("21 to 60 years", (21, 60)),
    ("23-58", (23, 58)),
])
def test_parse_age(text, expected):
    assert N.parse_age(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("\u20b950,000 to \u20b940 Lakh", (50000.0, 4000000.0)),
    ("up to 25 lakh", (None, 2500000.0)),
])
def test_parse_amount_range(text, expected):
    assert N.parse_amount_range(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("12 to 60 months", (12, 60)),
    ("1-5 years", (12, 60)),
])
def test_parse_tenure_months(text, expected):
    assert N.parse_tenure_months(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Salaried", ["salaried"]),
    ("Self-employed professionals", ["self_employed"]),
    ("", None),
])
def test_parse_employment(text, expected):
    assert N.parse_employment(text) == expected


def test_reject_rate_out_of_range():
    rec, errs = N.normalize_candidate(
        {"product_name": "X", "interest_rate": "85%"}, 1, "http://x", "selectors")
    assert rec is None
    assert "rate_out_of_range" in errs


def test_reject_insufficient_data():
    rec, errs = N.normalize_candidate(
        {"product_name": "Only Name"}, 1, "http://x", "selectors")
    assert rec is None
    assert "insufficient_data" in errs


def test_llm_ungrounded_field_nulled():
    # interest_rate text is NOT present in the page -> nulled with warning
    c = {"product_name": "Grounded Loan", "interest_rate": "99.9% p.a.",
         "min_monthly_income": "25000"}
    page = "Grounded Loan minimum income 25000 per month"
    rec, warns = N.normalize_candidate(c, 1, "http://x", "llm", page_text=page)
    assert rec is not None
    assert rec["interest_rate_min"] is None
    assert "ungrounded:interest_rate" in warns


def test_valid_record_fields():
    c = {"lender_name": "MockBank A", "product_name": "Smart Personal Loan",
         "interest_rate": "10.49% - 24% p.a.", "min_monthly_income": "\u20b925,000",
         "credit_score": "CIBIL 750 and above", "age": "21 to 60 years",
         "employment": "Salaried", "loan_amount": "\u20b950,000 to \u20b940 Lakh",
         "tenure": "12 to 60 months"}
    rec, warns = N.normalize_candidate(c, 1, "http://x/v1.html", "selectors")
    assert rec is not None
    assert rec["interest_rate_min"] == 10.49 and rec["interest_rate_max"] == 24.0
    assert rec["min_monthly_income"] == 25000.0
    assert rec["min_credit_score"] == 750
    assert rec["employment_types"] == ["salaried"]
    assert rec["product_key"] == "mockbank-a-smart-personal-loan"
    assert rec["content_hash"]


@pytest.mark.parametrize("valid,prev,expected_ok", [
    (0, 5, False),
    (2, 10, False),
    (5, 10, True),
    (3, 0, True),
])
def test_gate(valid, prev, expected_ok):
    ok, _ = N.gate(valid, prev)
    assert ok is expected_ok
