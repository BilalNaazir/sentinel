"""The data behind the dashboard's Traces page: find a trace, its steps with a colour each, what was
expected (when it was an eval case), and how its answer compares.
"""

from typing import Any

import pandas as pd
import psycopg

from sentinel.evals.entities import count_matches, f1
from sentinel.feedback.review import draft_answer, final_outputs

ENTITY_FIELDS = ("names", "emails", "products", "reference_ids", "amounts", "dates")


def recent_traces(
    conn: psycopg.Connection[Any], source: str | None, ticket: str, limit: int = 200
) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT trace_id::text, coalesce(ticket_id, '-'), source, status, created_at,
               coalesce(total_cost_usd, 0)::float, coalesce(pipeline_version, '-')
        FROM traces
        WHERE (%(source)s::text IS NULL OR source = %(source)s)
          AND (%(ticket)s = '' OR ticket_id ILIKE %(ticket)s || '%%')
        ORDER BY created_at DESC LIMIT %(limit)s
        """,
        {"source": source, "ticket": ticket.strip(), "limit": limit},
    ).fetchall()
    return pd.DataFrame(
        rows, columns=["trace_id", "ticket", "source", "status", "created_at", "cost_usd", "pipeline"]
    )


def trace_spans(conn: psycopg.Connection[Any], trace_id: str) -> list[dict[str, Any]]:
    """Every span of a trace, in the order they ran (an escalated step appears twice)."""
    rows = conn.execute(
        """
        SELECT span_id::text, step, step_order, model_id, complexity_tier, escalated_from, escalation_reason,
               confidence, tokens_in, tokens_out, cost_usd::float, latency_ms, input_json->>'prompt',
               raw_response, error
        FROM spans WHERE trace_id = %s ORDER BY step_order
        """,
        (trace_id,),
    ).fetchall()
    keys = [
        "span_id",
        "step",
        "order",
        "model_id",
        "tier",
        "escalated_from",
        "escalation_reason",
        "confidence",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "latency_ms",
        "prompt",
        "output",
        "error",
    ]
    return [dict(zip(keys, r, strict=True)) for r in rows]


def step_colour(span: dict[str, Any], faithfulness: int | None) -> str:
    """red: failed, or the forensics judge scored it 2 or lower. yellow: escalated, unsure (confidence 2 or
    lower) or scored 3. green: everything else."""
    if span["error"] or (faithfulness is not None and faithfulness <= 2):
        return "red"
    if (
        span["escalated_from"]
        or faithfulness == 3
        or (span["confidence"] is not None and span["confidence"] <= 2)
    ):
        return "yellow"
    return "green"


def eval_case_for(conn: psycopg.Connection[Any], trace_id: str) -> dict[str, Any] | None:
    """The eval case this trace was run for, with its result, or None for live and dev traces."""
    row = conn.execute(
        """
        SELECT c.case_id, c.expected_output, r.passed, r.reasons
        FROM eval_results r JOIN eval_cases c USING (case_id) WHERE r.trace_id = %s
        """,
        (trace_id,),
    ).fetchone()
    if row is None:
        return None
    return {"case_id": row[0], "expected": row[1], "passed": row[2], "reasons": row[3]}


def actual_answer(conn: psycopg.Connection[Any], trace_id: str) -> dict[str, Any]:
    return draft_answer(final_outputs(conn, trace_id))


def compare_answers(expected: dict[str, Any], actual: dict[str, Any]) -> pd.DataFrame:
    """One row per field: what was expected, what the pipeline gave, and whether it matches."""
    rows = [
        ("category", expected["category"], actual["category"], expected["category"] == actual["category"])
    ]
    for field in ENTITY_FIELDS:
        want, got = expected["entities"].get(field, []), actual["entities"].get(field, [])
        rows.append(
            (field, ", ".join(want) or "-", ", ".join(got) or "-", f1(count_matches(field, want, got)) == 1.0)
        )
    rows += [
        ("summary", expected["summary"], actual["summary"], None),
        ("next_action", expected["next_action"], actual["next_action"], None),
    ]
    return pd.DataFrame(rows, columns=["field", "expected", "pipeline", "match"]).set_index("field")
