"""Check the judge against human scores before trusting it.

python -m sentinel.evals.calibrate
"""

import json
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sentinel.evals.judge import JudgeScore, judge_model, judge_summary

CALIBRATION_PATH = Path("data") / "judge_calibration.json"
REPORTS_DIR = Path("reports")

# The judge is trusted when it is within 1 point of the human score at least this often,
# and on average no more than this lenient or harsh.
MIN_WITHIN_ONE = 0.90
MAX_ABS_BIAS = 0.5


class Agreement(BaseModel):
    cases: int
    exact: float  # judge gave exactly the human score
    within_one: float  # judge was at most 1 point away
    mean_abs_error: float
    bias: float  # judge minus human, on average: positive = too lenient, negative = too harsh
    trusted: bool


def agreement(pairs: list[tuple[int, int]]) -> Agreement:
    """pairs = (human score, judge score)"""
    diffs = [judge - human for human, judge in pairs]
    within_one = sum(abs(d) <= 1 for d in diffs) / len(diffs)
    bias = statistics.mean(diffs)
    return Agreement(
        cases=len(pairs),
        exact=sum(d == 0 for d in diffs) / len(diffs),
        within_one=within_one,
        mean_abs_error=statistics.mean(abs(d) for d in diffs),
        bias=bias,
        trusted=within_one >= MIN_WITHIN_ONE and abs(bias) <= MAX_ABS_BIAS,
    )


def confusion(pairs: list[tuple[int, int]]) -> str:
    """Rows: human score. Columns: judge score. The diagonal is where they agree."""
    counts = Counter(pairs)
    lines = ["human \\ judge   1   2   3   4   5"]
    for human in range(1, 6):
        row = "".join(f"{counts[(human, j)] or '.':>4}" for j in range(1, 6))
        lines.append(f"{human:>13}  {row}")
    return "\n".join(lines)


def main() -> None:
    cases: list[dict[str, Any]] = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))["cases"]
    print(f"Judging {len(cases)} human-scored summaries with {judge_model()}...\n")

    def run(case: dict[str, Any]) -> JudgeScore:
        return judge_summary(case["ticket_text"], case["reference_summary"], case["candidate_summary"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        verdicts = list(pool.map(run, cases))

    pairs = [(c["human_score"], v.score) for c, v in zip(cases, verdicts, strict=True)]
    result = agreement(pairs)

    print(confusion(pairs))
    print(f"\nexact agreement     {result.exact:.0%}")
    print(f"within 1 point      {result.within_one:.0%}   (need {MIN_WITHIN_ONE:.0%})")
    print(f"mean error          {result.mean_abs_error:.2f} points")
    print(f"bias                {result.bias:+.2f}   (+ lenient, - harsh; need within ±{MAX_ABS_BIAS})")
    print(f"\nverdict: {'TRUSTED' if result.trusted else 'NOT TRUSTED YET'}\n")

    print("Biggest disagreements:")
    worst = sorted(zip(cases, verdicts, strict=True), key=lambda cv: -abs(cv[1].score - cv[0]["human_score"]))
    for case, v in worst[:5]:
        if v.score == case["human_score"]:
            break
        ids = f"{case['calibration_id']} ({case['golden_case_id']})"
        print(f"  {ids}  human {case['human_score']}  judge {v.score}")
        print(f"    summary: {case['candidate_summary']}")
        print(f"    human:   {case['rationale']}")
        print(f"    judge:   {v.reason}\n")

    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"judge_calibration_{datetime.now(UTC):%Y%m%d_%H%M%S}.json"
    path.write_text(
        json.dumps(
            {
                "judge_model": judge_model(),
                "agreement": result.model_dump(),
                "cases": [
                    {**c, "judge_score": v.score, "judge_reason": v.reason}
                    for c, v in zip(cases, verdicts, strict=True)
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"saved to {path}")


if __name__ == "__main__":
    main()
