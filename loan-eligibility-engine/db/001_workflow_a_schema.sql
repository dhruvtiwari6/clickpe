CREATE TABLE IF NOT EXISTS crawl_sources (
  source_id            SERIAL PRIMARY KEY,
  name                 TEXT UNIQUE NOT NULL,
  listing_url          TEXT NOT NULL,
  requires_js          BOOLEAN NOT NULL DEFAULT FALSE,
  wait_selector        TEXT,                              -- for JS rendering
  extraction_mode      TEXT NOT NULL DEFAULT 'auto'
                       CHECK (extraction_mode IN ('selectors','llm','auto')),
  selector_config      JSONB,
  expected_min_products INT NOT NULL DEFAULT 1,
  is_active            BOOLEAN NOT NULL DEFAULT TRUE,
  last_content_hash    TEXT,
  last_crawled_at      TIMESTAMPTZ,
  last_success_at      TIMESTAMPTZ,
  consecutive_failures INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS loan_products (
  product_id          BIGSERIAL PRIMARY KEY,
  source_id           INT NOT NULL REFERENCES crawl_sources(source_id),
  product_key         TEXT NOT NULL,
  lender_name         TEXT,
  product_name        TEXT NOT NULL,
  interest_rate_min   NUMERIC(5,2),
  interest_rate_max   NUMERIC(5,2),
  min_loan_amount     NUMERIC(12,0),
  max_loan_amount     NUMERIC(12,0),
  min_tenure_months   INT,
  max_tenure_months   INT,
  min_monthly_income  NUMERIC(12,2),
  min_credit_score    INT,
  max_credit_score    INT,
  min_age             INT,
  max_age             INT,
  employment_types    TEXT[],
  processing_fee_text TEXT,
  eligibility_text    TEXT,
  source_url          TEXT,
  extraction_method   TEXT,                 -- 'selectors' | 'llm'
  confidence          NUMERIC(3,2),
  content_hash        TEXT,
  raw_extract         JSONB,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_changed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_id, product_key)
);
-- Supports Workflow B's SQL pre-filter
CREATE INDEX IF NOT EXISTS idx_lp_prefilter
  ON loan_products (is_active, min_monthly_income, min_credit_score);

CREATE TABLE IF NOT EXISTS crawl_runs (
  run_id               BIGSERIAL PRIMARY KEY,
  started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at          TIMESTAMPTZ,
  status               TEXT NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running','success','partial','failed')),
  trigger_type         TEXT,                      -- 'schedule' | 'manual'
  sources_total        INT, sources_ok INT, sources_failed INT, sources_unchanged INT,
  products_inserted    INT, products_updated INT, products_deactivated INT,
  summary              JSONB
);

CREATE TABLE IF NOT EXISTS crawl_source_log (
  log_id           BIGSERIAL PRIMARY KEY,
  run_id           BIGINT NOT NULL REFERENCES crawl_runs(run_id),
  source_id        INT NOT NULL REFERENCES crawl_sources(source_id),
  status           TEXT NOT NULL CHECK (status IN
                   ('ok','unchanged','blocked_robots','fetch_failed','parse_failed','gate_failed','db_failed')),
  method           TEXT,                          -- 'selectors' | 'llm' | NULL
  http_status      INT,
  candidates_found INT, valid_count INT, rejected_count INT,
  inserted INT, updated INT, deactivated INT,
  duration_ms      INT,
  error            TEXT,
  rejected_sample  JSONB,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW v_last_run AS
SELECT s.name AS source, l.status, l.method, l.candidates_found, l.valid_count,
       l.rejected_count, l.inserted, l.updated, l.deactivated, l.error, l.created_at
FROM crawl_source_log l JOIN crawl_sources s USING (source_id)
WHERE l.run_id = (SELECT max(run_id) FROM crawl_runs)
ORDER BY l.log_id;
