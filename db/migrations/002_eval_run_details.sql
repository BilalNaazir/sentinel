-- 002: details the eval engine needs to explain and compare runs.
-- IF NOT EXISTS makes this safe to run on a database that already has these columns.

ALTER TABLE eval_runs
    ADD COLUMN IF NOT EXISTS label TEXT,
    ADD COLUMN IF NOT EXISTS pipeline_version TEXT,
    ADD COLUMN IF NOT EXISTS judge_model TEXT;

ALTER TABLE eval_results
    ADD COLUMN IF NOT EXISTS category TEXT,
    ADD COLUMN IF NOT EXISTS reasons TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS summary_reason TEXT,
    ADD COLUMN IF NOT EXISTS failed_step TEXT,
    ADD COLUMN IF NOT EXISTS entity_counts JSONB;