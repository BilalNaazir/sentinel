-- A label review that has been settled: the expected answer was corrected in a new dataset version.
ALTER TABLE diagnoses DROP CONSTRAINT IF EXISTS diagnoses_status_check;
ALTER TABLE diagnoses ADD CONSTRAINT diagnoses_status_check
    CHECK (status IN ('proposed', 'confirmed', 'overridden', 'label_review', 'label_fixed'));