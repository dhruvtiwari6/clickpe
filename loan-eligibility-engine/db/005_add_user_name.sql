-- Idempotent migration: add the `name` column to users.
-- 003 already includes `name` for FRESH boots. This file exists so that an
-- ALREADY-initialized Postgres volume (where 003 ran before `name` existed)
-- also gets the column. Safe to run repeatedly.
ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT;
