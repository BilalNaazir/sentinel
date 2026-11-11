"""Run every golden ticket through the pipeline and print a first, rough scorecard.

python -m sentinel.pipeline.run_golden              # all 100 tickets
python -m sentinel.pipeline.run_golden --limit 5    # a quick try first
"""

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel.pipeline.models import Entities
from sentinel.pipeline.runner import PipelineResult, run_pipeline
from sentinel.settings import get_settings

ENTITY_FIELDS = list(Entities.model_fields)
REPORTS_DIR = Path("reports")

Case = dict[str, Any]


def load_cases(limit: int | None = None, ids: list[str] | None = None) -> list[Case]:
    cases: list[Case] = json.loads(get_settings().golden_path.read_text(encoding="utf-8"))["cases"]
    if ids:
        cases = [c for c in cases if c["case_id"] in ids]
    return cases[:limit] if limit else cases


def run_case(case: Case, save: bool = True) -> PipelineResult:
    return run_pipeline(case["input"]["ticket_text"], ticket_id=case["case_id"], source="eval", save=save)


def run_cases(cases: list[Case], workers: int = 4, save: bool = True) -> list[tuple[Case, PipelineResult]]:
    """Run tickets a few at a time, printing one line per ticket as soon as it finishes."""
    results: dict[str, PipelineResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_case, c, save): c for c in cases}
        for done, future in enumerate(as_completed(futures), 1):
            case, r = futures[future], future.result()
            results[case["case_id"]] = r
            expected = case["expected"]["category"]
            if r.status == "failed":
                mark = f"FAILED at {r.failed_step}"
            else:
                mark = ("ok  " if r.category == expected else "MISS") + f" {r.category} (expected {expected})"
            print(f"[{done:>3}/{len(cases)}] {case['case_id']}  {mark:<40} {r.latency_ms / 1000:5.1f}s")
    return [(c, results[c["case_id"]]) for c in cases]


def scorecard(pairs: list[tuple[Case, PipelineResult]]) -> dict[str, Any]:
    """Rough numbers only. Phase 3 replaces this with proper scoring (partial matches, judged summaries)."""
    done = [(c, r) for c, r in pairs if r.status == "success"]
    by_cat_total: Counter[str] = Counter(c["expected"]["category"] for c, _ in done)
    by_cat_right: Counter[str] = Counter(
        c["expected"]["category"] for c, r in done if r.category == c["expected"]["category"]
    )
    entity_exact = {}
    for field in ENTITY_FIELDS:
        matches = [
            sorted(getattr(r.entities, field)) == sorted(c["expected"]["entities"][field])
            for c, r in done
            if r.entities is not None
        ]
        entity_exact[field] = sum(matches) / len(matches) if matches else 0.0
    return {
        "tickets": len(pairs),
        "failed": len(pairs) - len(done),
        "failed_steps": dict(Counter(r.failed_step for _, r in pairs if r.status == "failed")),
        "category_accuracy": sum(by_cat_right.values()) / len(done) if done else 0.0,
        "category_accuracy_by_category": {k: by_cat_right[k] / n for k, n in sorted(by_cat_total.items())},
        "entity_exact_match": entity_exact,
        "total_cost_usd": round(sum(r.cost_usd for _, r in pairs), 6),
        "avg_latency_s": round(sum(r.latency_ms for _, r in pairs) / len(pairs) / 1000, 2) if pairs else 0.0,
    }


def print_scorecard(card: dict[str, Any]) -> None:
    print(f"\n{card['tickets']} tickets, {card['failed']} failed {card['failed_steps'] or ''}")
    print(f"category accuracy   {card['category_accuracy']:.0%}")
    for cat, acc in card["category_accuracy_by_category"].items():
        print(f"  {cat:<17} {acc:.0%}")
    print("entity exact match (whole list must match)")
    for field, acc in card["entity_exact_match"].items():
        print(f"  {field:<17} {acc:.0%}")
    print(f"total cost          ${card['total_cost_usd']:.4f}")
    print(f"avg time per ticket {card['avg_latency_s']}s")


def save_report(pairs: list[tuple[Case, PipelineResult]], card: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"golden_run_{datetime.now(UTC):%Y%m%d_%H%M%S}.json"
    report = {
        "golden_path": str(get_settings().golden_path),
        "scorecard": card,
        "results": [r.model_dump() for _, r in pairs],
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, help="only run the first N tickets")
    parser.add_argument("--ids", nargs="+", help="only run these case IDs")
    parser.add_argument("--workers", type=int, default=4, help="tickets to run at the same time")
    args = parser.parse_args()

    cases = load_cases(args.limit, args.ids)
    print(f"Running {len(cases)} golden tickets from {get_settings().golden_path}\n")
    start = time.perf_counter()
    pairs = run_cases(cases, workers=args.workers)
    card = scorecard(pairs)
    print_scorecard(card)
    print(f"wall time           {time.perf_counter() - start:.0f}s")
    print(f"\nfull results saved to {save_report(pairs, card)}")


if __name__ == "__main__":
    main()
