"""Save traces: one JSON file per trace, plus rows in the Postgres traces and spans tables."""

import json
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sentinel.settings import get_settings
from sentinel.tracing.core import Trace


def trace_to_dict(trace: Trace) -> dict[str, Any]:
    data = trace.model_dump(mode="json")
    data["status"] = trace.status
    data["total_cost_usd"] = trace.total_cost_usd
    data["total_latency_ms"] = trace.total_latency_ms
    return data


def save_trace_json(trace: Trace, folder: Path | None = None) -> Path:
    """Write the full trace to <folder>/<trace_id>.json and return the path."""
    folder = folder or get_settings().traces_dir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{trace.trace_id}.json"
    path.write_text(json.dumps(trace_to_dict(trace), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def save_trace_db(trace: Trace, database_url: str | None = None) -> None:
    """Insert the trace and its spans in one transaction: either all rows are saved or none."""
    url = database_url or get_settings().database_url
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(
            """
            INSERT INTO traces (trace_id, created_at, source, status, total_cost_usd, total_latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                trace.trace_id,
                trace.created_at,
                trace.source,
                trace.status,
                trace.total_cost_usd,
                trace.total_latency_ms,
            ),
        )
        for s in trace.spans:
            conn.execute(
                """
                INSERT INTO spans (span_id, trace_id, step, step_order, started_at, model_id,
                                   input_json, output_json, prompt, raw_response,
                                   tokens_in, tokens_out, cost_usd, latency_ms, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    s.span_id,
                    s.trace_id,
                    s.step,
                    s.step_order,
                    s.started_at,
                    s.model_id,
                    Jsonb(s.input),
                    Jsonb(s.output),
                    s.input.get("prompt"),
                    s.output if s.model_id else None,
                    s.tokens_in,
                    s.tokens_out,
                    s.cost_usd,
                    s.latency_ms,
                    s.error,
                ),
            )


def save_trace(trace: Trace) -> Path:
    """Save to both places. Returns the JSON file's path."""
    path = save_trace_json(trace)
    save_trace_db(trace)
    return path
