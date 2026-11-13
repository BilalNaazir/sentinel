"""Compare two saved eval runs: what got better, what got worse, and whether that's a regression.

python -m sentinel.evals.compare --list                              # recent runs
python -m sentinel.evals.compare "baseline v3" "baseline v3 repeat" # by label (newest with that label)
python -m sentinel.evals.compare 1d51ef3a 7c2e9b10                   # or by the start of a run_id
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import yaml
from pydantic import BaseModel

from sentinel.settings import get_settings

THRESHOLDS_PATH = Path("config") / "thresholds.yaml"
REPORTS_DIR = Path("reports")


class Thresholds(BaseModel):
    warn_drop_pct: float = 3.0
    critical_drop_pct: float = 8.0
    per_category_critical_drop_pct: float = 10.0
    min_cases_for_percentages: int = 20


def load_thresholds() -> Thresholds:
    return Thresholds(**yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8"))["regression"])


class ResultRow(BaseModel):
    passed: bool
    reasons: list[str]
    summary_score: int | None
    expected_category: str
    trace_id: str | None


class Run(BaseModel):
    run_id: str
    label: str | None
    created_at: datetime
    prompt_version: str
    pass_rate: float
    summary: dict[str, Any]  # the RunSummary saved by the eval run
    results: dict[str, ResultRow]  # by case_id

    @property
    def name(self) -> str:
        return f"{self.label or 'unlabelled'} ({self.run_id[:8]})"


class Flip(BaseModel):
    case_id: str
    reasons: list[str]  # why it failed: in the candidate for a regression, in the baseline for an improvement


class Comparison(BaseModel):
    baseline: str
    candidate: str
    pass_rate_change_pct: float  # percentage points, candidate minus baseline
    category_changes_pct: dict[str, float]
    summary_score_change: float | None
    regressions: list[Flip]  # passed before, fail now
    improvements: list[Flip]  # failed before, pass now
    still_failing: list[str]
    status: str  # pass | warn | fail
    status_reasons: list[str]


def compare(base: Run, cand: Run, t: Thresholds) -> Comparison:
    shared = sorted(set(base.results) & set(cand.results))
    regressions = [
        Flip(case_id=c, reasons=cand.results[c].reasons)
        for c in shared
        if base.results[c].passed and not cand.results[c].passed
    ]
    improvements = [
        Flip(case_id=c, reasons=base.results[c].reasons)
        for c in shared
        if not base.results[c].passed and cand.results[c].passed
    ]
    still_failing = [c for c in shared if not base.results[c].passed and not cand.results[c].passed]

    def pass_rate(run: Run, cases: list[str]) -> float:
        return 100 * sum(run.results[c].passed for c in cases) / len(cases) if cases else 0.0

    change = pass_rate(cand, shared) - pass_rate(base, shared)
    category_changes = {}
    status, status_reasons = "pass", []
    for cat in sorted({base.results[c].expected_category for c in shared}):
        cases = [c for c in shared if base.results[c].expected_category == cat]
        category_changes[cat] = pass_rate(cand, cases) - pass_rate(base, cases)
        if (
            len(cases) >= t.min_cases_for_percentages
            and category_changes[cat] < -t.per_category_critical_drop_pct
        ):
            status = "fail"
            status_reasons.append(f"{cat} pass rate fell {-category_changes[cat]:.1f} points")

    if change < -t.critical_drop_pct:
        status = "fail"
        status_reasons.append(f"pass rate fell {-change:.1f} points (critical above {t.critical_drop_pct})")
    elif change < -t.warn_drop_pct and status != "fail":
        status = "warn"
        status_reasons.append(f"pass rate fell {-change:.1f} points (warning above {t.warn_drop_pct})")

    base_score, cand_score = base.summary.get("mean_summary_score"), cand.summary.get("mean_summary_score")
    return Comparison(
        baseline=base.name,
        candidate=cand.name,
        pass_rate_change_pct=round(change, 1),
        category_changes_pct={k: round(v, 1) for k, v in category_changes.items()},
        summary_score_change=round(cand_score - base_score, 2) if base_score and cand_score else None,
        regressions=regressions,
        improvements=improvements,
        still_failing=still_failing,
        status=status,
        status_reasons=status_reasons,
    )


# ---------------------------------------------------------------- reading runs from Postgres
def find_run_id(conn: psycopg.Connection[Any], ref: str) -> str:
    """A label (newest run with it wins) or the start of a run_id."""
    row = conn.execute(
        """
        SELECT run_id::text FROM eval_runs
        WHERE label = %s OR run_id::text LIKE %s
        ORDER BY created_at DESC LIMIT 1
        """,
        (ref, f"{ref}%"),
    ).fetchone()
    if row is None:
        raise SystemExit(f"No eval run with label or id {ref!r}. Try --list.")
    run_id: str = row[0]
    return run_id


def load_run(conn: psycopg.Connection[Any], ref: str) -> Run:
    run_id = find_run_id(conn, ref)
    head = conn.execute(
        """
        SELECT label, created_at, prompt_version, pass_rate, per_category_json
        FROM eval_runs WHERE run_id = %s
        """,
        (run_id,),
    ).fetchone()
    assert head is not None
    rows = conn.execute(
        """
        SELECT r.case_id, r.passed, r.reasons, r.summary_score,
               c.expected_output->>'category', r.trace_id::text
        FROM eval_results r JOIN eval_cases c USING (case_id)
        WHERE r.run_id = %s
        """,
        (run_id,),
    ).fetchall()
    return Run(
        run_id=run_id,
        label=head[0],
        created_at=head[1],
        prompt_version=head[2],
        pass_rate=float(head[3]),
        summary=head[4],
        results={
            r[0]: ResultRow(
                passed=r[1], reasons=r[2], summary_score=r[3], expected_category=r[4], trace_id=r[5]
            )
            for r in rows
        },
    )


def summary_text(conn: psycopg.Connection[Any], trace_id: str | None) -> str:
    """The summary the pipeline wrote, read back from its trace."""
    if trace_id is None:
        return "(no trace)"
    row = conn.execute(
        "SELECT output_json #>> '{}' FROM spans WHERE trace_id = %s AND step = 'summary'", (trace_id,)
    ).fetchone()
    return row[0] if row and row[0] else "(no summary)"


def list_runs(conn: psycopg.Connection[Any]) -> None:
    rows = conn.execute(
        """
        SELECT run_id::text, created_at, label, n_cases, pass_rate, status, baseline_run_id::text
        FROM eval_runs ORDER BY created_at DESC LIMIT 15
        """
    ).fetchall()
    print(f"{'run':<10}{'when (UTC)':<18}{'label':<28}{'cases':>6}{'pass':>7}  status")
    for r in rows:
        compared = f"  vs {r[6][:8]}" if r[6] else ""
        label = (r[2] or "-")[:26]
        print(
            f"{r[0][:8]:<10}{r[1]:%Y-%m-%d %H:%M}  {label:<28}{r[3]:>6}{float(r[4]):>7.0%}  {r[5]}{compared}"
        )


# ---------------------------------------------------------------- report
def render(cmp: Comparison, base: Run, cand: Run, conn: psycopg.Connection[Any]) -> str:
    lines = [
        f"# {cmp.status.upper()}: {cmp.candidate} vs baseline {cmp.baseline}",
        "",
        f"- Pass rate: {base.pass_rate:.0%} -> {cand.pass_rate:.0%} ({cmp.pass_rate_change_pct:+.1f} points)",
    ]
    if cmp.summary_score_change is not None:
        lines.append(f"- Mean summary score: {cmp.summary_score_change:+.2f}")
    for cat, ch in cmp.category_changes_pct.items():
        lines.append(f"- {cat}: {ch:+.1f} points")
    lines += [f"- {r}" for r in cmp.status_reasons]
    lines += ["", f"Prompts: {base.prompt_version} -> {cand.prompt_version}", ""]

    lines.append(f"## Regressions: {len(cmp.regressions)} (passed before, fail now)")
    for f in cmp.regressions:
        lines += [
            f"### {f.case_id}: {'; '.join(f.reasons)}",
            f"- before: {summary_text(conn, base.results[f.case_id].trace_id)}",
            f"- after:  {summary_text(conn, cand.results[f.case_id].trace_id)}",
            "",
        ]
    lines.append(f"## Improvements: {len(cmp.improvements)} (failed before, pass now)")
    for f in cmp.improvements:
        lines.append(f"- {f.case_id} (was: {'; '.join(f.reasons)})")
    lines += ["", f"## Still failing: {len(cmp.still_failing)}", ", ".join(cmp.still_failing) or "none"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("baseline", nargs="?")
    parser.add_argument("candidate", nargs="?")
    parser.add_argument("--list", action="store_true", help="show recent runs")
    args = parser.parse_args()

    with psycopg.connect(get_settings().database_url) as conn:
        if args.list or not (args.baseline and args.candidate):
            list_runs(conn)
            return
        base, cand = load_run(conn, args.baseline), load_run(conn, args.candidate)
        cmp = compare(base, cand, load_thresholds())
        report = render(cmp, base, cand, conn)
        conn.execute(
            "UPDATE eval_runs SET baseline_run_id = %s, status = %s WHERE run_id = %s",
            (base.run_id, cmp.status, cand.run_id),
        )

    print(report)
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"compare_{datetime.now(UTC):%Y%m%d_%H%M%S}.md"
    path.write_text(report + "\n", encoding="utf-8")
    print(f"\nsaved to {path}")


if __name__ == "__main__":
    main()
