-- Why the router escalated a step: 'confidence 2' or 'failed: StepOutputError'.
-- complexity_tier and escalated_from already exist in the original schema.
ALTER TABLE spans ADD COLUMN IF NOT EXISTS escalation_reason TEXT;