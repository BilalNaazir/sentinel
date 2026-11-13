"""Fair entity scoring: credit for every entity found, with a matching rule per field.

python -m sentinel.evals.entities   # re-score the latest golden run report (no LLM calls)
"""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

FIELDS = ["names", "emails", "products", "reference_ids", "amounts", "dates"]


def _exact(a: str, b: str) -> bool:
    return a.strip() == b.strip()


def _ignore_case(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _contains(a: str, b: str) -> bool:
    """Either value inside the other, ignoring case: 'Pro' matches 'Pro plan'."""
    a, b = a.strip().casefold(), b.strip().casefold()
    return bool(a) and bool(b) and (a in b or b in a)


# How strictly each field is matched. IDs and money must be exact; names and products are lenient.
MATCH_RULES: dict[str, Callable[[str, str], bool]] = {
    "names": _contains,
    "emails": _ignore_case,
    "products": _contains,
    "reference_ids": _exact,
    "amounts": _exact,
    "dates": _ignore_case,
}


class FieldCount(BaseModel):
    expected: int = 0
    predicted: int = 0
    matched: int = 0


def count_matches(field: str, expected: list[str], predicted: list[str]) -> FieldCount:
    """Pair each expected value with at most one predicted value that matches it."""
    rule = MATCH_RULES[field]
    unused = list(predicted)
    matched = 0
    for e in expected:
        for i, p in enumerate(unused):
            if rule(e, p):
                matched += 1
                del unused[i]
                break
    return FieldCount(expected=len(expected), predicted=len(predicted), matched=matched)


def f1(count: FieldCount) -> float:
    """1.0 when there was nothing to find and nothing was found."""
    if count.expected == 0 and count.predicted == 0:
        return 1.0
    return 2 * count.matched / (count.expected + count.predicted)


def score_case(expected: dict[str, list[str]], predicted: dict[str, list[str]]) -> dict[str, FieldCount]:
    return {f: count_matches(f, expected.get(f, []), predicted.get(f, [])) for f in FIELDS}


def case_f1(counts: dict[str, FieldCount]) -> float:
    """One number per ticket: all fields pooled together."""
    total = FieldCount(
        expected=sum(c.expected for c in counts.values()),
        predicted=sum(c.predicted for c in counts.values()),
        matched=sum(c.matched for c in counts.values()),
    )
    return f1(total)


class FieldScore(BaseModel):
    precision: float | None  # of the entities the model gave, how many were right
    recall: float | None  # of the entities it should have found, how many it found
    f1: float | None
    expected: int
    predicted: int


def aggregate(all_counts: list[dict[str, FieldCount]]) -> dict[str, FieldScore]:
    """Per field, across all tickets. Tickets with nothing expected and nothing found don't count."""
    out = {}
    for f in FIELDS:
        e = sum(c[f].expected for c in all_counts)
        p = sum(c[f].predicted for c in all_counts)
        m = sum(c[f].matched for c in all_counts)
        out[f] = FieldScore(
            precision=m / p if p else None,
            recall=m / e if e else None,
            f1=2 * m / (e + p) if e + p else None,
            expected=e,
            predicted=p,
        )
    return out


def print_field_scores(scores: dict[str, FieldScore]) -> None:
    def pct(x: float | None) -> str:
        return "   -" if x is None else f"{x:4.0%}"

    print(f"{'field':<15}{'precision':>10}{'recall':>8}{'F1':>6}{'expected':>10}{'found':>7}")
    for f, s in scores.items():
        print(
            f"{f:<15}{pct(s.precision):>10}{pct(s.recall):>8}{pct(s.f1):>6}{s.expected:>10}{s.predicted:>7}"
        )


if __name__ == "__main__":
    import glob
    import json
    import sys

    from sentinel.settings import get_settings

    # Pass a report path to choose one; otherwise the newest report is used.
    report_path = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob("reports/golden_run_*.json"))[-1]
    report: dict[str, Any] = json.loads(open(report_path, encoding="utf-8").read())
    golden = json.loads(get_settings().golden_path.read_text(encoding="utf-8"))["cases"]
    expected_by_id = {c["case_id"]: c["expected"]["entities"] for c in golden}

    counts = [
        score_case(expected_by_id[r["ticket_id"]], r["entities"] or {})
        for r in report["results"]
        if r["ticket_id"] in expected_by_id
    ]
    print(f"Re-scoring {report_path} ({len(counts)} tickets)\n")
    print_field_scores(aggregate(counts))
