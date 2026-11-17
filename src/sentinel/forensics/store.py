"""Save diagnoses to Postgres as 'proposed': a person confirms or overrides them later."""

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sentinel.forensics.analyzer import Diagnosis
from sentinel.settings import get_settings


def step_scores(d: Diagnosis) -> list[dict[str, Any]]:
    return [
        {"step": s.span.step, "span_id": s.span.span_id, "score": s.score, "problem": s.problem}
        for s in d.scores
    ]


def save_diagnosis(d: Diagnosis, flag_note: str | None = None) -> str:
    """Store one diagnosis. A trace with no faulty step is stored as 'no_fault_found', not skipped."""
    diagnosis_id = str(uuid.uuid4())
    with psycopg.connect(get_settings().database_url) as conn:
        conn.execute(
            """
            INSERT INTO diagnoses (diagnosis_id, trace_id, root_cause_span_id, failure_type, evidence_text,
                                   step_scores, type_source, cost_usd, flag_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                diagnosis_id,
                d.trace_id,
                d.root_cause.span.span_id if d.root_cause else None,
                d.failure_type or "no_fault_found",
                d.evidence,
                Jsonb(step_scores(d)),
                d.type_source or "rule: no step scored 2 or lower",
                round(d.cost_usd, 8),
                flag_note,
            ),
        )
    return diagnosis_id


def latest_diagnosis(trace_id: str) -> dict[str, Any] | None:
    with psycopg.connect(get_settings().database_url) as conn:
        row = conn.execute(
            """
            SELECT d.diagnosis_id::text, d.status, d.failure_type, s.step, d.evidence_text, d.step_scores,
                   d.type_source, d.flag_note, d.created_at
            FROM diagnoses d LEFT JOIN spans s ON s.span_id = d.root_cause_span_id
            WHERE d.trace_id = %s ORDER BY d.created_at DESC LIMIT 1
            """,
            (trace_id,),
        ).fetchone()
    if row is None:
        return None
    keys = [
        "diagnosis_id",
        "status",
        "failure_type",
        "root_cause_step",
        "evidence",
        "step_scores",
        "type_source",
        "flag_note",
        "created_at",
    ]
    return dict(zip(keys, row, strict=True))
