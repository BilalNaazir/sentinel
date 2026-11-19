"""The numbers behind the dashboard's Quality page: pass rate over eval runs with a drift line, and the
detail of one run (categories, entity fields, judged summary scores).

Drift settings come from config/thresholds.yaml (drift: window_runs, alert_below_pass_rate).
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
import yaml

THRESHOLDS = Path("config") / "thresholds.yaml"


def drift_settings(path: Path = THRESHOLDS) -> tuple[int, float]:
    drift = yaml.safe_load(path.read_text(encoding="utf-8"))["drift"]
    return int(drift["window_runs"]), float(drift["alert_below_pass_rate"])


def load_runs(conn: psycopg.Connection[Any], dataset_version: int | None) -> pd.DataFrame:
    """Every eval run, oldest first, optionally for one dataset version only."""
    rows = conn.execute(
        """
        SELECT run_id::text, coalesce(label, left(run_id::text, 8)), created_at, pass_rate::float,
               dataset_version, n_cases, prompt_version, pipeline_version, per_category_json
        FROM eval_runs
        WHERE %(v)s::int IS NULL OR dataset_version = %(v)s
        ORDER BY created_at
        """,
        {"v": dataset_version},
    ).fetchall()
    columns = [
        "run_id",
        "label",
        "created_at",
        "pass_rate",
        "dataset_version",
        "cases",
        "prompts",
        "pipeline",
        "summary",
    ]
    return pd.DataFrame(rows, columns=columns)


def with_drift(runs: pd.DataFrame, window: int) -> pd.DataFrame:
    """Adds the moving average of pass rate over the last `window` runs (fewer at the start)."""
    out = runs.copy()
    out["moving_avg"] = out["pass_rate"].rolling(window, min_periods=1).mean()
    return out


def drifting(runs: pd.DataFrame, alert_below: float) -> bool:
    """True when the latest moving average is below the alert line."""
    return bool(not runs.empty and runs["moving_avg"].iloc[-1] < alert_below)


def summary_of(run: pd.Series) -> dict[str, Any]:
    raw = run["summary"]
    return raw if isinstance(raw, dict) else json.loads(raw)


def category_table(summary: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(summary["by_category"]).T[["cases", "pass_rate", "category_accuracy"]]


def entity_table(summary: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(summary["entities"]).T[["precision", "recall", "f1", "expected", "predicted"]]


def score_counts(conn: psycopg.Connection[Any], run_id: str) -> pd.Series:
    """How many summaries the judge scored 1, 2, 3, 4 and 5 in one run (missing scores left out)."""
    rows = conn.execute(
        "SELECT summary_score, count(*) FROM eval_results WHERE run_id = %s AND summary_score IS NOT NULL "
        "GROUP BY 1",
        (run_id,),
    ).fetchall()
    counts = dict.fromkeys(range(1, 6), 0) | {int(score): int(n) for score, n in rows}
    return pd.Series(counts, name="summaries")
