import pytest

from app import ingest as ING


# ---- employment_status normalization ----
@pytest.mark.parametrize("raw,expected", [
    ("Salaried", "salaried"),
    ("salaried", "salaried"),
    ("SALARY", "salaried"),
    ("Full Time", "salaried"),
    ("Self-Employed", "self_employed"),
    ("self employed", "self_employed"),
    ("self_employed", "self_employed"),
    ("SE", "self_employed"),
    ("Business", "self_employed"),
    ("freelancer", "self_employed"),
    ("", None),
    (None, None),
    ("student", None),            # genuinely unmappable
])
def test_normalize_employment(raw, expected):
    assert ING.normalize_employment(raw) == expected


# ---- numeric coercion ----
@pytest.mark.parametrize("raw,expected", [
    ("131000", 131000.0),
    ("1,31,000", 131000.0),
    ("\u20b9 85,000", 85000.0),
    ("$52000", 52000.0),
    ("", None),
    ("N/A", None),
    (None, None),
])
def test_to_number(raw, expected):
    assert ING._to_number(raw) == expected


@pytest.mark.parametrize("raw,lo,hi,expected", [
    ("725", 300, 900, (725, True)),
    ("300", 300, 900, (300, True)),
    ("900", 300, 900, (900, True)),
    ("200", 300, 900, (None, False)),   # below range -> invalid
    ("1000", 300, 900, (None, False)),  # above range -> invalid
    ("", 300, 900, (None, True)),       # missing is allowed
])
def test_to_int_in_range(raw, lo, hi, expected):
    assert ING._to_int_in_range(raw, lo, hi) == expected


# ---- single-row normalization ----
def test_normalize_row_valid():
    row = {
        "user_id": "b35de23e", "name": "Dhruv Iyengar",
        "email": "dhruv.iyengar@example.com", "monthly_income": "131000",
        "credit_score": "725", "employment_status": "Self-Employed", "age": "37",
    }
    clean, rej = ING.normalize_row(row, "batch-1")
    assert rej is None
    assert clean["user_id"] == "b35de23e"
    assert clean["name"] == "Dhruv Iyengar"
    assert clean["employment_status"] == "self_employed"
    assert clean["monthly_income"] == 131000.0
    assert clean["credit_score"] == 725
    assert clean["age"] == 37
    assert clean["batch_id"] == "batch-1"


def test_normalize_row_missing_user_id():
    row = {"user_id": "", "email": "a@b.com", "employment_status": "salaried"}
    clean, rej = ING.normalize_row(row, "b")
    assert clean is None and "missing user_id" in rej["reason"]


def test_normalize_row_bad_email():
    row = {"user_id": "u1", "email": "not-an-email", "employment_status": "salaried"}
    clean, rej = ING.normalize_row(row, "b")
    assert clean is None and "invalid email" in rej["reason"]


def test_normalize_row_unmappable_employment():
    row = {"user_id": "u1", "email": "a@b.com", "employment_status": "retired"}
    clean, rej = ING.normalize_row(row, "b")
    assert clean is None and "employment_status" in rej["reason"]


def test_normalize_row_credit_out_of_range():
    row = {"user_id": "u1", "email": "a@b.com",
           "employment_status": "salaried", "credit_score": "1200"}
    clean, rej = ING.normalize_row(row, "b")
    assert clean is None and "credit_score out of" in rej["reason"]


def test_normalize_row_nullable_credit():
    row = {"user_id": "u1", "email": "a@b.com",
           "employment_status": "salaried", "credit_score": "", "age": "40"}
    clean, rej = ING.normalize_row(row, "b")
    assert rej is None and clean["credit_score"] is None


# ---- full CSV parse ----
SAMPLE = (
    "user_id,name,email,monthly_income,credit_score,employment_status,age\n"
    "u1,Alice,alice@example.com,131000,725,Self-Employed,37\n"
    "u2,Bob,bob@example.com,38000,765,Salaried,36\n"
    "u3,Carol,bad-email,62000,705,Salaried,36\n"              # rejected: email
    "u4,Dan,dan@example.com,31000,1200,Salaried,43\n"          # rejected: credit range
)


def test_parse_csv_counts_and_batch():
    out = ING.parse_csv(SAMPLE, batch_id="fixed-batch")
    assert out["batch_id"] == "fixed-batch"
    assert out["total_rows"] == 4
    assert out["valid_count"] == 2
    assert out["rejected_count"] == 2
    assert out["missing_headers"] == []
    assert all(r["batch_id"] == "fixed-batch" for r in out["valid"])
    assert {r["employment_status"] for r in out["valid"]} <= {"salaried", "self_employed"}


def test_parse_csv_generates_batch_id_when_absent():
    out = ING.parse_csv(SAMPLE)
    assert out["batch_id"] and len(out["batch_id"]) >= 32


def test_parse_csv_missing_headers():
    out = ING.parse_csv("foo,bar\n1,2\n")
    assert "user_id" in out["missing_headers"]
    assert out["valid_count"] == 0
