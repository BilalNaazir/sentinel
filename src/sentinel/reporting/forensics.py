"""The data behind the dashboard's Forensics page: the planted test-set score, the review queue, time to
root cause, the verifier, and judge calibration.
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg

from sentinel.forensics.planted import PIPELINE_VERSION, load_cases

REPORTS = Path("reports")


def planted_score(conn: psycopg.Connection[Any], answers: list[dict[str, Any]]) -> pd.DataFrame:
    """The latest diagnosis of each planted failure, next to the right answer."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (t.ticket_id) t.ticket_id, s.step, d.failure_type
        FROM diagnoses d JOIN traces t USING (trace_id) LEFT JOIN spans s ON s.span_id = d.root_cause_span_id
        WHERE t.pipeline_version = %s ORDER BY t.ticket_id, d.created_at DESC
        """,
        (PIPELINE_VERSION,),
    ).fetchall()
    found = {r[0]: (r[1] or "-", r[2]) for r in rows}
    table = []
    for case in answers:
        step, ftype = found.get(case["case_id"], ("not diagnosed", "-"))
        table.append(
            (
                case["case_id"],
                case["root_cause_step"],
                step,
                case["root_cause_step"] == step,
                case["failure_type"],
                ftype,
                case["failure_type"] == ftype,
            )
        )
    columns = [
        "case",
        "expected step",
        "diagnosed step",
        "step right",
        "expected type",
        "diagnosed type",
        "type right",
    ]
    return pd.DataFrame(table, columns=columns)


def planted_answers() -> list[dict[str, Any]]:
    return load_cases()


def review_queue(conn: psycopg.Connection[Any]) -> pd.DataFrame:
    """How many diagnoses there are of each failure type, in each review status."""
    rows = conn.execute("SELECT failure_type, status, count(*) FROM diagnoses GROUP BY 1, 2").fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["failure type", "status", "count"])
    return df.pivot_table(index="failure type", columns="status", values="count", aggfunc="sum", fill_value=0)


def diagnosis_timing(conn: psycopg.Connection[Any]) -> dict[str, float]:
    """Median and slowest time to root cause, and the mean cost, over diagnoses that recorded a duration."""
    row = conn.execute(
        """
        SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms),
               max(duration_ms), avg(cost_usd)::float
        FROM diagnoses WHERE duration_ms IS NOT NULL
        """
    ).fetchone()
    assert row is not None
    count, median, slowest, cost = row
    return {
        "timed": int(count),
        "median_s": (median or 0) / 1000,
        "slowest_s": (slowest or 0) / 1000,
        "mean_cost": cost or 0.0,
    }


def verifier_stats(conn: psycopg.Connection[Any]) -> pd.DataFrame:
    """Steps checked by the verifier and how many it flagged, by traffic source."""
    rows = conn.execute(
        """
        SELECT t.source, count(*), count(*) FILTER (WHERE v.routing_failure), avg(v.agreement_score)::float
        FROM verifications v JOIN spans s USING (span_id) JOIN traces t USING (trace_id)
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    df = pd.DataFrame(rows, columns=["source", "checked", "flagged", "mean agreement"])
    df["flag rate"] = df["flagged"] / df["checked"]
    return df.set_index("source")


def latest_calibration(folder: Path = REPORTS) -> dict[str, Any] | None:
    """The newest judge calibration report (python -m sentinel.evals.calibrate), or None."""
    reports = sorted(folder.glob("judge_calibration_*.json"))
    if not reports:
        return None
    data: dict[str, Any] = json.loads(reports[-1].read_text(encoding="utf-8"))
    data["file"] = reports[-1].name
    return data
