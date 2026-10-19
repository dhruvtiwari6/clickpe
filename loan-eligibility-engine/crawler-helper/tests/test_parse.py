import pathlib

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
MOCK = pathlib.Path(__file__).resolve().parents[2] / "mock-sites"

V1_CFG = {
    "card": "div.loan-card",
    "fields": {
        "lender_name": {"css": ".lender"},
        "product_name": {"css": "h3.product"},
        "interest_rate": {"css": ".roi"},
        "loan_amount": {"css": ".amount"},
        "tenure": {"css": ".tenure"},
        "min_monthly_income": {"css": ".elig .income"},
        "credit_score": {"css": ".elig .score"},
        "age": {"css": ".elig .age"},
        "employment": {"css": ".elig .emp"},
        "processing_fee": {"css": ".fee"},
        "detail_url": {"css": "a.apply", "attr": "href"},
    },
}

V2_CFG = {
    "card": "table.products tbody tr",
    "fields": {
        "lender_name": {"css": "td:nth-child(1)"},
        "product_name": {"css": "td:nth-child(2)"},
        "interest_rate": {"css": "td:nth-child(3)"},
        "min_monthly_income": {"css": "td:nth-child(4)"},
        "credit_score": {"css": "td:nth-child(5)"},
        "employment": {"css": "td:nth-child(6)"},
    },
}


def _parse(fname, cfg):
    html = (MOCK / fname).read_text(encoding="utf-8")
    r = client.post("/parse", json={
        "html": html, "base_url": "http://mock-sites/" + fname, "selector_config": cfg})
    assert r.status_code == 200
    return r.json()


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_v1_three_candidates():
    data = _parse("v1.html", V1_CFG)
    assert data["candidate_count"] == 3
    names = [c["product_name"] for c in data["candidates"]]
    assert "Smart Personal Loan" in names
    # detail_url resolved to absolute
    assert data["candidates"][0]["detail_url"].startswith("http://mock-sites/")


def test_v1_broken_zero_candidates():
    # same config, renamed classes -> Tier 1 finds nothing -> triggers LLM fallback
    data = _parse("v1_broken.html", V1_CFG)
    assert data["candidate_count"] == 0
    # but clean_text still has the product content for the LLM tier
    assert "Smart Personal Loan" in data["clean_text"]


def test_v2_three_candidates():
    data = _parse("v2.html", V2_CFG)
    assert data["candidate_count"] == 3
    names = [c["product_name"] for c in data["candidates"]]
    assert "Prime Personal Loan" in names


def test_fingerprint_stable():
    a = _parse("v1.html", V1_CFG)["fingerprint"]
    b = _parse("v1.html", V1_CFG)["fingerprint"]
    assert a == b and len(a) == 64
