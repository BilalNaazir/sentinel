-- Why the verifier agreed or not, in words: 'entity F1 0.67' or 'judge 3: drops the order ID'.
ALTER TABLE verifications ADD COLUMN IF NOT EXISTS detail TEXT;
