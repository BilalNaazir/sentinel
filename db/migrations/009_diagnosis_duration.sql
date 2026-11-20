-- How long each diagnosis took, so time to root cause can be reported (plan target: under 30 seconds).
ALTER TABLE diagnoses ADD COLUMN IF NOT EXISTS duration_ms INTEGER;