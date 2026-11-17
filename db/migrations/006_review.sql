-- Phase 6: reviewing diagnoses. 'label_review' means the trace failed its eval but no step was at
-- fault, so the expected answer itself needs checking. review_note records why a reviewer decided.
ALTER TABLE diagnoses ADD COLUMN IF NOT EXISTS review_note TEXT;
ALTER TABLE diagnoses DROP CONSTRAINT IF EXISTS diagnoses_status_check;
ALTER TABLE diagnoses ADD CONSTRAINT diagnoses_status_check
    CHECK (status IN ('proposed', 'confirmed', 'overridden', 'label_review'));