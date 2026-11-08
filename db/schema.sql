-- Sentinel Postgres schema: the seven tables from the plan's data model. trace_id joins everything.

CREATE TABLE traces (
    trace_id          UUID PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    source            TEXT NOT NULL CHECK (source IN ('prod', 'eval', 'ci')),
    pipeline_version  TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,            -- e.g. "intake@1,extraction@1,classification@1,summary@1"
    routing_version   INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('success', 'degraded', 'failed')),
    total_cost_usd    NUMERIC(12, 6) NOT NULL DEFAULT 0,
    total_latency_ms  INTEGER NOT NULL DEFAULT 0,
    final_score       NUMERIC(4, 2),
    ticket_id         TEXT                      -- golden case_id or load-test ticket_id when source != 'prod'
);

CREATE TABLE spans (
    span_id          UUID PRIMARY KEY,
    trace_id         UUID NOT NULL REFERENCES traces (trace_id) ON DELETE CASCADE,
    step             TEXT NOT NULL,             -- intake | extraction | classification | summary | reply_draft
    step_order       SMALLINT NOT NULL,
    model_id         TEXT NOT NULL,
    complexity_tier  SMALLINT CHECK (complexity_tier IN (1, 2, 3)),
    escalated_from   TEXT,                      -- model_id before auto-escalation, if any
    input_json       JSONB NOT NULL,
    output_json      JSONB,
    prompt           TEXT NOT NULL,
    raw_response     TEXT,
    tokens_in        INTEGER NOT NULL DEFAULT 0,
    tokens_out       INTEGER NOT NULL DEFAULT 0,
    cost_usd         NUMERIC(12, 6) NOT NULL DEFAULT 0,
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    confidence       SMALLINT CHECK (confidence BETWEEN 1 AND 5),
    error            TEXT
);
CREATE INDEX spans_trace_idx ON spans (trace_id, step_order);

CREATE TABLE verifications (
    verification_id  UUID PRIMARY KEY,
    span_id          UUID NOT NULL REFERENCES spans (span_id) ON DELETE CASCADE,
    reference_model  TEXT NOT NULL,
    reference_output JSONB,
    agreement_score  NUMERIC(4, 3) NOT NULL,
    routing_failure  BOOLEAN NOT NULL,
    escalated        BOOLEAN NOT NULL DEFAULT false,
    cost_delta_usd   NUMERIC(12, 6) NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE diagnoses (
    diagnosis_id       UUID PRIMARY KEY,
    trace_id           UUID NOT NULL REFERENCES traces (trace_id) ON DELETE CASCADE,
    root_cause_span_id UUID REFERENCES spans (span_id),
    failure_type       TEXT NOT NULL CHECK (failure_type IN ('extraction_hallucination', 'misclassification',
                         'propagation_error', 'prompt_failure', 'context_loss', 'under_routed')),
    evidence_text      TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'confirmed', 'overridden')),
    corrected_output   JSONB,
    reviewed_by        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at        TIMESTAMPTZ
);

CREATE TABLE eval_cases (
    case_id          TEXT PRIMARY KEY,          -- GT-001 for golden, FL-0001 for flags, RF-0001 for routing failures
    dataset_version  INTEGER NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('golden', 'flag', 'routing_failure')),
    input            JSONB NOT NULL,
    expected_output  JSONB NOT NULL,
    difficulty       TEXT CHECK (difficulty IN ('easy', 'medium', 'hard')),
    tags             TEXT[] NOT NULL DEFAULT '{}',
    failure_type     TEXT,
    origin_trace_id  UUID REFERENCES traces (trace_id),
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE eval_runs (
    run_id             UUID PRIMARY KEY,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger            TEXT NOT NULL CHECK (trigger IN ('pr', 'nightly', 'manual')),
    git_sha            TEXT,
    prompt_version     TEXT NOT NULL,
    routing_version    INTEGER NOT NULL,
    dataset_version    INTEGER NOT NULL,
    baseline_run_id    UUID REFERENCES eval_runs (run_id),
    n_cases            INTEGER NOT NULL,
    pass_rate          NUMERIC(5, 4) NOT NULL,
    per_category_json  JSONB NOT NULL,
    avg_cost_usd       NUMERIC(12, 6) NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('pass', 'warn', 'fail'))
);

CREATE TABLE eval_results (
    run_id          UUID NOT NULL REFERENCES eval_runs (run_id) ON DELETE CASCADE,
    case_id         TEXT NOT NULL REFERENCES eval_cases (case_id),
    trace_id        UUID REFERENCES traces (trace_id),
    category_match  BOOLEAN NOT NULL,
    entity_f1       NUMERIC(4, 3) NOT NULL,
    summary_score   SMALLINT CHECK (summary_score BETWEEN 1 AND 5),
    latency_ms      INTEGER NOT NULL,
    cost_usd        NUMERIC(12, 6) NOT NULL,
    passed          BOOLEAN NOT NULL,
    PRIMARY KEY (run_id, case_id)
);
