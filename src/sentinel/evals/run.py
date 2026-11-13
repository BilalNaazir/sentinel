"""An eval run: pipeline + scoring for every golden ticket, with a pass/fail per ticket.

python -m sentinel.evals.run                    # all golden tickets
python -m sentinel.evals.run --limit 5          # a quick try
python -m sentinel.evals.run --ids GT-001 GT-089
python -m sentinel.evals.run --label "baseline v3"   # name the run so you can find it later
"""

import argparse
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from sentinel.evals.entities import FieldCount, FieldScore, aggregate, case_f1, score_case
from sentinel.evals.judge import JudgeScore, judge_summary
from sentinel.gateway.client import GatewayError
from sentinel.pipeline.models import StepOutputError
from sentinel.pipeline.run_golden import load_cases
from sentinel.pipeline.runner import PipelineResult, run_pipeline

THRESHOLDS_PATH = Path("config") / "thresholds.yaml"
REPORTS_DIR = Path("reports")
Case = dict[str, Any]


class PassCriteria(BaseModel):
    category_must_match: bool = True
    min_entity_f1: float = 0.8
    min_summary_score: int = 4


def load_criteria() -> PassCriteria:
    return PassCriteria(**yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8"))["pass_criteria"])


class CaseResult(BaseModel):
    case_id: str
    trace_id: str
    passed: bool
    reasons: list[str]  # why it failed; empty when it passed
    expected_category: str
    category: str | None
    category_match: bool
    entity_f1: float
    entity_counts: dict[str, FieldCount]
    summary: str | None
    summary_score: int | None
    summary_reason: str | None
    failed_step: str | None
    error: str | None
    latency_ms: int
    cost_usd: float


def score_result(
    case: Case, result: PipelineResult, verdict: JudgeScore | None, criteria: PassCriteria
) -> CaseResult:
    """Turn one pipeline result into a scored, pass/fail case. A failed run fails the case."""
    expected = case["expected"]
    counts = score_case(expected["entities"], result.entities.model_dump() if result.entities else {})
    entity_f1 = case_f1(counts) if result.entities else 0.0
    category_match = result.category == expected["category"]

    reasons = []
    if result.status == "failed":
        reasons.append(f"pipeline failed at {result.failed_step}")
    if criteria.category_must_match and not category_match and result.status != "failed":
        reasons.append(f"category {result.category}, expected {expected['category']}")
    if result.entities and entity_f1 < criteria.min_entity_f1:
        reasons.append(f"entity F1 {entity_f1:.2f}")
    if result.summary and verdict is None:
        reasons.append("summary could not be judged")
    elif verdict and verdict.score < criteria.min_summary_score:
        reasons.append(f"summary scored {verdict.score}")

    return CaseResult(
        case_id=case["case_id"],
        trace_id=result.trace_id,
        passed=not reasons,
        reasons=reasons,
        expected_category=expected["category"],
        category=result.category,
        category_match=category_match,
        entity_f1=round(entity_f1, 3),
        entity_counts=counts,
        summary=result.summary,
        summary_score=verdict.score if verdict else None,
        summary_reason=verdict.reason if verdict else None,
        failed_step=result.failed_step,
        error=result.error,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
    )


def evaluate_case(case: Case, criteria: PassCriteria) -> CaseResult:
    result = run_pipeline(case["input"]["ticket_text"], ticket_id=case["case_id"], source="eval")
    verdict = None
    if result.summary:
        try:
            verdict = judge_summary(case["input"]["ticket_text"], case["expected"]["summary"], result.summary)
        except (GatewayError, StepOutputError) as exc:
            result.error = f"judge failed: {exc}"
    return score_result(case, result, verdict, criteria)


class RunSummary(BaseModel):
    cases: int
    passed: int
    pass_rate: float
    category_accuracy: float  # out of ALL cases: a failed run counts as wrong
    by_category: dict[str, dict[str, float]]
    entities: dict[str, FieldScore]
    mean_summary_score: float | None
    pipeline_failures: int
    avg_cost_usd: float
    avg_latency_ms: int


def summarise(results: list[CaseResult]) -> RunSummary:
    by_category: dict[str, dict[str, float]] = {}
    for cat in sorted({r.expected_category for r in results}):
        rs = [r for r in results if r.expected_category == cat]
        by_category[cat] = {
            "cases": len(rs),
            "pass_rate": sum(r.passed for r in rs) / len(rs),
            "category_accuracy": sum(r.category_match for r in rs) / len(rs),
        }
    scores = [r.summary_score for r in results if r.summary_score is not None]
    return RunSummary(
        cases=len(results),
        passed=sum(r.passed for r in results),
        pass_rate=sum(r.passed for r in results) / len(results),
        category_accuracy=sum(r.category_match for r in results) / len(results),
        by_category=by_category,
        entities=aggregate([r.entity_counts for r in results]),
        mean_summary_score=statistics.mean(scores) if scores else None,
        pipeline_failures=sum(r.failed_step is not None for r in results),
        avg_cost_usd=statistics.mean(r.cost_usd for r in results),
        avg_latency_ms=int(statistics.mean(r.latency_ms for r in results)),
    )


def run_eval(cases: list[Case], workers: int = 4) -> list[CaseResult]:
    criteria = load_criteria()
    done: dict[str, CaseResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(evaluate_case, c, criteria): c for c in cases}
        for n, future in enumerate(as_completed(futures), 1):
            r = future.result()
            done[r.case_id] = r
            mark = "PASS" if r.passed else "FAIL"
            print(f"[{n:>3}/{len(cases)}] {r.case_id}  {mark}  {'; '.join(r.reasons)}")
    return [done[c["case_id"]] for c in cases]


def print_summary(s: RunSummary) -> None:
    print(f"\npass rate          {s.pass_rate:.0%}  ({s.passed}/{s.cases})")
    print(f"category accuracy  {s.category_accuracy:.0%}  (failed runs count as wrong)")
    print(f"mean summary score {s.mean_summary_score:.2f} / 5" if s.mean_summary_score else "")
    print(f"pipeline failures  {s.pipeline_failures}")
    print(f"avg cost / ticket  ${s.avg_cost_usd:.6f}   avg time {s.avg_latency_ms / 1000:.1f}s\n")
    print(f"{'category':<12}{'cases':>6}{'pass':>7}{'accuracy':>10}")
    for cat, v in s.by_category.items():
        print(f"{cat:<12}{int(v['cases']):>6}{v['pass_rate']:>7.0%}{v['category_accuracy']:>10.0%}")
    print(f"\n{'entity':<15}{'precision':>10}{'recall':>8}{'F1':>6}")
    for f, e in s.entities.items():
        cells = ["   -" if x is None else f"{x:4.0%}" for x in (e.precision, e.recall, e.f1)]
        print(f"{f:<15}{cells[0]:>10}{cells[1]:>8}{cells[2]:>6}")


def save_report(results: list[CaseResult], summary: RunSummary) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"eval_run_{datetime.now(UTC):%Y%m%d_%H%M%S}.json"
    data = {"summary": summary.model_dump(), "results": [r.model_dump() for r in results]}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, help="only the first N tickets")
    parser.add_argument("--ids", nargs="+", help="only these case IDs")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--label", help="a name for this run, e.g. 'baseline v3' or 'summary@2'")
    parser.add_argument("--no-db", action="store_true", help="don't save the run to Postgres")
    args = parser.parse_args()

    cases = load_cases(args.limit, args.ids)
    print(f"Eval run on {len(cases)} golden tickets\n")
    results = run_eval(cases, workers=args.workers)
    summary = summarise(results)
    print_summary(summary)
    print(f"\nsaved to {save_report(results, summary)}")
    if not args.no_db:
        from sentinel.evals.store import save_run  # imported here: store.py imports this module

        run_id = save_run(cases, results, summary, label=args.label)
        print(f"saved to Postgres as run {run_id}" + (f" ({args.label})" if args.label else ""))


if __name__ == "__main__":
    main()
