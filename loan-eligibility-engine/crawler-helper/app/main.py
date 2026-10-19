import hashlib
import os
import re
import urllib.robotparser as rp
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI

from . import normalize as N
from .models import NormReq, ParseReq, RenderReq, RobotsReq

UA = os.getenv("USER_AGENT", "LoanDiscoveryBot/1.0")
app = FastAPI(title="crawler-helper")


@app.get("/health")
def health():
    return {"ok": True}


# ---------- /robots ----------
@app.post("/robots")
async def robots(req: RobotsReq):
    p = urlparse(req.url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                                     headers={"User-Agent": UA}) as c:
            r = await c.get(robots_url)
    except Exception as e:
        return {"allowed": False, "crawl_delay": None, "reason": f"robots unreachable: {e}"}
    if 400 <= r.status_code < 500:                  # no robots.txt => allowed
        return {"allowed": True, "crawl_delay": None, "reason": f"robots {r.status_code}"}
    if r.status_code >= 500:                        # fail closed
        return {"allowed": False, "crawl_delay": None, "reason": f"robots {r.status_code}"}
    parser = rp.RobotFileParser()
    parser.parse(r.text.splitlines())
    return {"allowed": parser.can_fetch(UA, req.url),
            "crawl_delay": parser.crawl_delay(UA), "reason": "robots.txt evaluated"}


# ---------- /render ----------
@app.post("/render")
async def render(req: RenderReq):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(user_agent=UA)
        page = await ctx.new_page()
        resp = await page.goto(req.url, wait_until="domcontentloaded", timeout=req.timeout_ms)
        if req.wait_selector:
            await page.wait_for_selector(req.wait_selector, timeout=req.timeout_ms)
        else:
            await page.wait_for_load_state("networkidle", timeout=req.timeout_ms)
        html = await page.content()
        await browser.close()
    return {"status": resp.status if resp else None, "html": html}


# ---------- /parse ----------
STRIP = ["script", "style", "noscript", "svg", "iframe", "nav", "footer", "form"]


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _extract(node, spec, base_url):
    if isinstance(spec, str):
        spec = {"css": spec}
    el = node.select_one(spec["css"]) if spec.get("css") else node
    if el is None:
        return None
    attr = spec.get("attr", "text")
    val = _ws(el.get_text(" ", strip=True)) if attr == "text" else el.get(attr)
    if attr == "href" and val:
        val = urljoin(base_url, val)
    return val or None


@app.post("/parse")
def parse(req: ParseReq):
    soup = BeautifulSoup(req.html, "lxml")
    cfg = req.selector_config or {}
    candidates = []
    if cfg.get("card") and cfg.get("fields"):
        for card in soup.select(cfg["card"]):
            row = {k: _extract(card, spec, req.base_url) for k, spec in cfg["fields"].items()}
            row["eligibility_text"] = row.get("eligibility_text") or _ws(card.get_text(" ", strip=True))[:1500]
            if row.get("product_name"):
                candidates.append(row)

    for t in soup(STRIP):
        t.decompose()
    clean_text = soup.get_text("\n", strip=True)[: req.max_text_chars]

    if candidates:   # fingerprint product content only, ignoring page chrome
        basis = "|".join(_ws(c["eligibility_text"]) for c in candidates)
    else:
        basis = _ws(clean_text)
    fingerprint = hashlib.sha256(basis.lower().encode()).hexdigest()

    return {"fingerprint": fingerprint, "candidates": candidates,
            "candidate_count": len(candidates), "clean_text": clean_text}


# ---------- /normalize ----------
@app.post("/normalize")
def normalize(req: NormReq):
    valid, rejected = N.normalize_many(
        req.candidates, req.source_id, req.source_url, req.method, req.page_text)
    gate_ok, gate_reason = N.gate(len(valid), req.prev_active_count, req.min_ratio)
    return {
        "valid": valid,
        "valid_count": len(valid),
        "rejected_count": len(rejected),
        "rejected_sample": rejected[:5],
        "gate_passed": gate_ok,
        "gate_reason": gate_reason,
    }
