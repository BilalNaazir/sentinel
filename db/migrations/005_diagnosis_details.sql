-- Forensics (Phase 5): keep the per-step scores behind each diagnosis, how its type was decided,
-- what it cost, and why it was flagged. Add 'no_fault_found': the trace failed its eval, but every
-- step was faithful to its input, which usually means the expected answer (the label) needs review.
ALTER TABLE diagnoses
    ADD COLUMN IF NOT EXISTS step_scores JSONB,
    ADD COLUMN IF NOT EXISTS type_source TEXT,
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(14, 8),
    ADD COLUMN IF NOT EXISTS flag_note TEXT;
ALTER TABLE diagnoses DROP CONSTRAINT IF EXISTS diagnoses_failure_type_check;
ALTER TABLE diagnoses ADD CONSTRAINT diagnoses_failure_type_check CHECK (failure_type IN (
    'extraction_hallucination', 'misclassification', 'propagation_error', 'prompt_failure',
    'context_loss', 'under_routed', 'no_fault_found'));