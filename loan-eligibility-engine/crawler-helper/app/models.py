"""Pydantic request models for the crawler-helper endpoints."""
from pydantic import BaseModel


class RobotsReq(BaseModel):
    url: str


class RenderReq(BaseModel):
    url: str
    wait_selector: str | None = None
    timeout_ms: int = 45000


class ParseReq(BaseModel):
    html: str
    base_url: str
    selector_config: dict | None = None
    max_text_chars: int = 20000


class NormReq(BaseModel):
    source_id: int
    source_url: str
    method: str                                  # 'selectors' | 'llm'
    candidates: list[dict]
    page_text: str | None = None                 # required for grounding when method == 'llm'
    prev_active_count: int = 0
    min_ratio: float = 0.5


class RulesReq(BaseModel):
    pairs: list[dict]


class LlmApplyReq(BaseModel):
    ambiguous: list[dict]
    verdicts: dict | None = None                 # None => offline fallback


class IngestReq(BaseModel):
    """Stateless CSV ingestion. Accepts the raw CSV text (n8n reads the uploaded
    file and sends its contents). The helper parses + normalizes rows; n8n owns
    the DB write. A batch_id may be supplied for idempotent re-runs; otherwise
    the helper generates one."""
    csv_text: str
    batch_id: str | None = None
    filename: str | None = None
