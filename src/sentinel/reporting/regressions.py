"""The data behind the dashboard's Regressions page: run history, what flipped between two runs, and the
known-issues report. Comparison and issue rules are the ones the CLI tools use (compare.py, issues.py).
"""

from typing import Any

import pandas as pd
import psycopg

from sentinel.evals.compare import Comparison, compare, load_run, load_thresholds
from sentinel.feedback.issues import first_fixed, flagged_cases, histories, label_reviews, marks, status_of


def run_history(conn: psycopg.Connection[Any]) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT e.run_id::text, coalesce(e.label, left(e.run_id::text, 8)), e.created_at, e.dataset_version,
               e.n_cases, e.pass_rate::float, e.prompt_version, e.pipeline_version, e.trigger,
               coalesce(b.label, '-')
        FROM eval_runs e LEFT JOIN eval_runs b ON b.run_id = e.baseline_run_id
        ORDER BY e.created_at DESC
        """
    ).fetchall()
    columns = [
        "run_id",
        "label",
        "created_at",
        "dataset",
        "cases",
        "pass_rate",
        "prompts",
        "pipeline",
        "trigger",
        "compared_with",
    ]
    return pd.DataFrame(rows, columns=columns)


def compare_runs(conn: psycopg.Connection[Any], base_id: str, cand_id: str) -> Comparison:
    return compare(load_run(conn, base_id), load_run(conn, cand_id), load_thresholds())


def flips_table(cmp: Comparison) -> pd.DataFrame:
    """Every case whose result changed, regressions first."""
    rows = [(f.case_id, "regressed", "; ".join(f.reasons)) for f in cmp.regressions]
    rows += [(f.case_id, "improved", "was: " + "; ".join(f.reasons)) for f in cmp.improvements]
    return pd.DataFrame(rows, columns=["case", "change", "reasons"])


def issue_rows(cases: list[tuple[str, str]], hist: dict[str, list[tuple[str, bool]]]) -> pd.DataFrame:
    """cases are (case_id, what it is). One row each: status, history (oldest first) and the fixing run."""
    rows = []
    for case_id, kind in cases:
        h = hist.get(case_id, [])
        rows.append((case_id, kind, status_of([p for _, p in h]), marks(h), first_fixed(h)))
    return pd.DataFrame(rows, columns=["case", "what", "status", "history", "fixed in"])


def known_issues(conn: psycopg.Connection[Any], runs: int = 8) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flagged cases and open label reviews, each with its recent pass/fail history."""
    flagged = [(c, f"{source}: {ftype}") for c, source, ftype in flagged_cases(conn)]
    reviews = [(c, f"label review: {note}") for c, _, note in label_reviews(conn)]
    hist = histories(conn, [c for c, _ in flagged + reviews], runs)
    return issue_rows(flagged, hist), issue_rows(reviews, hist)
