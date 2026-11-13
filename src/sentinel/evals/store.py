"""Save an eval run to Postgres: the cases, one eval_runs row, and one eval_results row per ticket."""

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg.types.json import Jsonb

from sentinel.evals.judge import judge_model
from sentinel.evals.run import CaseResult, RunSummary
from sentinel.pipeline.runner import PIPELINE_VERSION, prompt_versions
from sentinel.settings import get_settings

ROUTING_PATH = Path("config") / "routing.yaml"


def git_sha() -> str | None:
    """The commit the code was run from, so a run can always be traced back to its code."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def dataset_version() -> int:
    version: int = json.loads(get_settings().golden_path.read_text(encoding="utf-8"))["version"]
    return version


def routing_version() -> int:
    version: int = yaml.safe_load(ROUTING_PATH.read_text(encoding="utf-8"))["version"]
    return version


def save_run(
    cases: list[dict[str, Any]],
    results: list[CaseResult],
    summary: RunSummary,
    label: str | None = None,
    trigger: str = "manual",
    database_url: str | None = None,
) -> str:
    """Save everything in one transaction. Returns the new run_id."""
    run_id = str(uuid.uuid4())
    version = dataset_version()
    url = database_url or get_settings().database_url
    with psycopg.connect(url) as conn, conn.transaction():
        for c in cases:
            # eval_results points at eval_cases, so the cases must exist first. Update them if they changed.
            conn.execute(
                """
                INSERT INTO eval_cases (case_id, dataset_version, source, input, expected_output,
                                        difficulty, tags, notes)
                VALUES (%s, %s, 'golden', %s, %s, %s, %s, %s)
                ON CONFLICT (case_id) DO UPDATE SET
                    dataset_version = EXCLUDED.dataset_version,
                    input = EXCLUDED.input,
                    expected_output = EXCLUDED.expected_output,
                    difficulty = EXCLUDED.difficulty,
                    tags = EXCLUDED.tags,
                    notes = EXCLUDED.notes
                """,
                (
                    c["case_id"],
                    version,
                    Jsonb(c["input"]),
                    Jsonb(c["expected"]),
                    c["difficulty"],
                    c["tags"],
                    c["notes"],
                ),
            )
        conn.execute(
            """
            INSERT INTO eval_runs (run_id, trigger, git_sha, prompt_version, routing_version, dataset_version,
                                   n_cases, pass_rate, per_category_json, avg_cost_usd, status,
                                   label, pipeline_version, judge_model)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pass', %s, %s, %s)
            """,
            (
                run_id,
                trigger,
                git_sha(),
                prompt_versions(),
                routing_version(),
                version,
                summary.cases,
                summary.pass_rate,
                Jsonb(summary.model_dump()),
                summary.avg_cost_usd,
                label,
                PIPELINE_VERSION,
                judge_model(),
            ),
        )
        for r in results:
            conn.execute(
                """
                INSERT INTO eval_results (run_id, case_id, trace_id, category_match, entity_f1, summary_score,
                                          latency_ms, cost_usd, passed, category, reasons, summary_reason,
                                          failed_step, entity_counts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    r.case_id,
                    r.trace_id,
                    r.category_match,
                    r.entity_f1,
                    r.summary_score,
                    r.latency_ms,
                    r.cost_usd,
                    r.passed,
                    r.category,
                    r.reasons,
                    r.summary_reason,
                    r.failed_step,
                    Jsonb({f: c.model_dump() for f, c in r.entity_counts.items()}),
                ),
            )
    return run_id
