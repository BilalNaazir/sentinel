-- Eval cases made from flagged traces point back at the trace they came from. If that trace is
-- deleted later (the planted traces are reloaded, for example), keep the eval case and forget the link.
ALTER TABLE eval_cases DROP CONSTRAINT IF EXISTS eval_cases_origin_trace_id_fkey;
ALTER TABLE eval_cases ADD CONSTRAINT eval_cases_origin_trace_id_fkey
    FOREIGN KEY (origin_trace_id) REFERENCES traces (trace_id) ON DELETE SET NULL;