-- ---------- users (populated by the ingestion step; demo seed in 004) ----------
CREATE TABLE IF NOT EXISTS users (
  user_id            TEXT PRIMARY KEY,           -- natural key from the CSV
  name               TEXT,                        -- display name from the CSV (used by Workflow C)
  email              TEXT NOT NULL,
  monthly_income     NUMERIC(12,2),
  credit_score       INT,
  employment_status  TEXT,                        -- normalized to {salaried, self_employed}
  age                INT,
  batch_id           UUID NOT NULL,               -- ingestion batch this user belongs to
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_batch ON users (batch_id);

-- ---------- matches (B writes; C reads) ----------
CREATE TABLE IF NOT EXISTS matches (
  match_id     BIGSERIAL PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(user_id),
  product_id   BIGINT NOT NULL REFERENCES loan_products(product_id),
  batch_id     UUID NOT NULL,
  match_stage  TEXT NOT NULL CHECK (match_stage IN ('rule','llm')),
  score        NUMERIC(4,3),                      -- 0..1 ranking for C's ordering
  reasons      JSONB,                             -- explainability
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  notified     BOOLEAN NOT NULL DEFAULT FALSE,    -- C flips this; B never resets it
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_batch ON matches (batch_id);
CREATE INDEX IF NOT EXISTS idx_matches_notify ON matches (batch_id, notified) WHERE is_active;

-- ---------- match_runs (one row per batch run; the funnel) ----------
CREATE TABLE IF NOT EXISTS match_runs (
  run_id           BIGSERIAL PRIMARY KEY,
  batch_id         UUID NOT NULL,
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at      TIMESTAMPTZ,
  status           TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running','success','partial','failed')),
  users_in_batch   INT,
  active_products  INT,
  pairs_considered BIGINT,       -- users_in_batch * active_products (theoretical max)
  after_stage1     INT,          -- survivors of the SQL pre-filter
  after_stage2     INT,          -- clear rule matches
  llm_pairs        INT,          -- ambiguous pairs sent to the LLM
  matches_written  INT,
  summary          JSONB
);
CREATE INDEX IF NOT EXISTS idx_match_runs_batch ON match_runs (batch_id);

-- ---------- demo view: the funnel of the latest run ----------
CREATE OR REPLACE VIEW v_last_match_run AS
SELECT batch_id, status, users_in_batch, active_products, pairs_considered,
       after_stage1, after_stage2, llm_pairs, matches_written,
       finished_at - started_at AS duration
FROM match_runs
ORDER BY run_id DESC
LIMIT 1;

