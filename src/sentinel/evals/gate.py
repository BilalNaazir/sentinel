"""The regression gate: compare an eval run with the accepted baseline, and fail on a critical drop.

The baseline is a committed file (baselines/eval_baseline.json), so the gate needs no database: it
works the same on your machine and in CI, where every run starts with an empty database.

python -m sentinel.evals.gate save-baseline                  # accept the newest eval report as the baseline
python -m sentinel.evals.gate check                          # newest report vs the baseline; exit 1 on FAIL
python -m sentinel.evals.gate check --report reports/eval_run_20261116_013633.json
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel.evals.compare import Comparison, ResultRow, Run, compare, load_thresholds
from sentinel.evals.store import dataset_version, routing_version
from sentinel.pipeline.runner import PIPELINE_VERSION, prompt_versions

BASELINE = Path("baselines") / "eval_baseline.json"
REPORTS = Path("reports")
COMMENT = REPORTS / "gate_comment.md"
RESULT = REPORTS / "gate_result.json"  # the same verdict, for alerts to read


def newest_report() -> Path:
    reports = sorted(REPORTS.glob("eval_run_*.json"))
    if not reports:
        raise SystemExit("no eval reports in reports/: run python -m sentinel.evals.run first")
    return reports[-1]


def baseline_from_report(report: dict[str, Any], label: str) -> dict[str, Any]:
    """What the gate needs from a run, small enough to commit: per-case results, no traces."""
    return {
        "label": label,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "prompt_version": prompt_versions(),
        "pipeline_version": PIPELINE_VERSION,
        "routing_version": routing_version(),
        "dataset_version": dataset_version(),
        "summary": report["summary"],
        "results": {
            r["case_id"]: {
                "passed": r["passed"],
                "reasons": r["reasons"],
                "summary_score": r["summary_score"],
                "expected_category": r["expected_category"],
                "summary": r["summary"],
            }
            for r in report["results"]
        },
    }


def by_case(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-case results keyed by case ID. A baseline stores them that way; a report stores a list."""
    results = data["results"]
    return results if isinstance(results, dict) else {r["case_id"]: r for r in results}


def to_run(data: dict[str, Any], name: str) -> Run:
    """A report or a baseline file, as the Run that compare() works on."""
    return Run(
        run_id=name,
        label=data.get("label", name),
        created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now(UTC),
        prompt_version=data.get("prompt_version", prompt_versions()),
        pass_rate=data["summary"]["pass_rate"],
        summary=data["summary"],
        results={
            case_id: ResultRow(
                passed=r["passed"],
                reasons=r["reasons"],
                summary_score=r["summary_score"],
                expected_category=r["expected_category"],
                trace_id=r.get("trace_id"),
            )
            for case_id, r in by_case(data).items()
        },
    )


def fmt(score: float | None) -> str:
    return f"{score:.2f}" if score is not None else "-"


def comment(cmp: Comparison, base: dict[str, Any], cand: dict[str, Any]) -> str:
    """The PR comment: the verdict first, then the numbers, then each regression with its reason."""
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}[cmp.status]
    b, c = base["summary"], cand["summary"]
    cost_change = (
        100 * (c["avg_cost_usd"] - b["avg_cost_usd"]) / b["avg_cost_usd"] if b["avg_cost_usd"] else 0.0
    )
    lines = [
        f"## {icon} Sentinel eval gate: {cmp.status.upper()}",
        "",
        *[f"- **{r}**" for r in cmp.status_reasons],
        "",
        "| | baseline | this change |",
        "|---|---|---|",
        f"| Pass rate | {b['pass_rate']:.0%} ({b['passed']}/{b['cases']}) | "
        f"{c['pass_rate']:.0%} ({c['passed']}/{c['cases']}) |",
        f"| Change (shared cases) | | {cmp.pass_rate_change_pct:+.1f} points |",
        f"| Mean summary score | {fmt(b['mean_summary_score'])} | {fmt(c['mean_summary_score'])} |",
        f"| Cost per ticket | ${b['avg_cost_usd']:.6f} | ${c['avg_cost_usd']:.6f} ({cost_change:+.0f}%) |",
        f"| Pipeline failures | {b['pipeline_failures']} | {c['pipeline_failures']} |",
        "",
        "By category (points): "
        + ", ".join(f"{cat} {ch:+.1f}" for cat, ch in cmp.category_changes_pct.items()),
        "",
        f"Baseline: {base.get('label')} (prompts {base.get('prompt_version')}). "
        f"Now: prompts {prompt_versions()}, pipeline v{PIPELINE_VERSION}, routing v{routing_version()}.",
        "",
        f"### Regressions: {len(cmp.regressions)} (passed before, fail now)",
    ]
    before, after = by_case(base), by_case(cand)
    for f in cmp.regressions:
        lines += [
            f"- **{f.case_id}**: {'; '.join(f.reasons)}",
            f"  - before: {before[f.case_id].get('summary') or '(no summary)'}",
            f"  - after: {after[f.case_id].get('summary') or '(no summary)'}",
        ]
    lines += [
        "",
        f"### Improvements: {len(cmp.improvements)}",
        ", ".join(f.case_id for f in cmp.improvements) or "none",
        "",
        f"Still failing: {', '.join(cmp.still_failing) or 'none'}",
    ]
    return "\n".join(lines) + "\n"


def result(cmp: Comparison, base: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """The gate's verdict and headline numbers, as data."""
    return {
        "status": cmp.status,
        "reasons": cmp.status_reasons,
        "baseline_pass_rate": base["summary"]["pass_rate"],
        "pass_rate": cand["summary"]["pass_rate"],
        "change_points": cmp.pass_rate_change_pct,
        "regressions": [f.case_id for f in cmp.regressions],
        "baseline_cost": base["summary"]["avg_cost_usd"],
        "cost": cand["summary"]["avg_cost_usd"],
        "prompts": prompt_versions(),
    }


def check(report_path: Path, baseline_path: Path = BASELINE) -> Comparison:
    if not baseline_path.exists():
        raise SystemExit(f"no baseline at {baseline_path}: run 'save-baseline' first")
    base = json.loads(baseline_path.read_text(encoding="utf-8"))
    cand = json.loads(report_path.read_text(encoding="utf-8"))
    cmp = compare(to_run(base, "baseline"), to_run(cand, report_path.stem), load_thresholds())
    REPORTS.mkdir(exist_ok=True)
    COMMENT.write_text(comment(cmp, base, cand), encoding="utf-8")
    RESULT.write_text(json.dumps(result(cmp, base, cand), indent=2), encoding="utf-8")
    return cmp


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_save = sub.add_parser("save-baseline")
    p_save.add_argument("--report", type=Path, help="default: the newest reports/eval_run_*.json")
    p_save.add_argument("--label", default="accepted baseline")
    p_check = sub.add_parser("check")
    p_check.add_argument("--report", type=Path, help="default: the newest reports/eval_run_*.json")
    args = parser.parse_args()

    report = args.report or newest_report()
    if args.command == "save-baseline":
        data = baseline_from_report(json.loads(report.read_text(encoding="utf-8")), args.label)
        BASELINE.parent.mkdir(exist_ok=True)
        BASELINE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        rate, n = data["summary"]["pass_rate"], len(data["results"])
        print(f"baseline saved from {report}: {rate:.0%} on {n} cases")
        return

    cmp = check(report)
    print(COMMENT.read_text(encoding="utf-8"))
    print(f"(comment saved to {COMMENT})")
    sys.exit(1 if cmp.status == "fail" else 0)


if __name__ == "__main__":
    main()
