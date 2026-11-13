from datetime import UTC, datetime

import pytest

from sentinel.evals.compare import ResultRow, Run, Thresholds, compare

T = Thresholds(
    warn_drop_pct=3, critical_drop_pct=8, per_category_critical_drop_pct=10, min_cases_for_percentages=20
)


def make_run(label: str, passes: dict[str, bool], category: str = "billing") -> Run:
    return Run(
        run_id=f"{label}-0000",
        label=label,
        created_at=datetime.now(UTC),
        prompt_version="summary@1",
        pass_rate=sum(passes.values()) / len(passes),
        summary={"mean_summary_score": 4.0},
        results={
            c: ResultRow(
                passed=p,
                reasons=[] if p else [f"{c} failed"],
                summary_score=5,
                expected_category=category,
                trace_id=None,
            )
            for c, p in passes.items()
        },
    )


def cases(n_pass: int, n_fail: int) -> dict[str, bool]:
    ids = [f"GT-{i:03d}" for i in range(1, n_pass + n_fail + 1)]
    return {c: i < n_pass for i, c in enumerate(ids)}


def test_flips_are_sorted_into_regressions_and_improvements() -> None:
    base = make_run("base", {"A": True, "B": False, "C": False, "D": True})
    cand = make_run("cand", {"A": False, "B": True, "C": False, "D": True})
    cmp = compare(base, cand, T)
    assert [f.case_id for f in cmp.regressions] == ["A"]
    assert cmp.regressions[0].reasons == ["A failed"]  # why it fails NOW
    assert [f.case_id for f in cmp.improvements] == ["B"]
    assert cmp.improvements[0].reasons == ["B failed"]  # why it USED to fail
    assert cmp.still_failing == ["C"]


def test_a_small_drop_passes_a_medium_drop_warns_a_big_drop_fails() -> None:
    base = make_run("base", cases(95, 5))
    assert compare(base, make_run("c", cases(93, 7)), T).status == "pass"  # -2 points
    assert compare(base, make_run("c", cases(90, 10)), T).status == "warn"  # -5 points
    assert compare(base, make_run("c", cases(85, 15)), T).status == "fail"  # -10 points


def test_a_big_drop_in_one_category_fails_even_if_overall_looks_fine() -> None:
    base_passes = cases(25, 0) | {f"T-{i}": True for i in range(75)}
    cand_passes = cases(20, 5) | {f"T-{i}": True for i in range(75)}
    base = make_run("base", base_passes)
    cand = make_run("cand", cand_passes)
    for run in (base, cand):  # make the first 25 cases 'account', the rest 'billing'
        for c, r in run.results.items():
            r.expected_category = "account" if c.startswith("GT-") else "billing"
    cmp = compare(base, cand, T)
    assert cmp.pass_rate_change_pct == pytest.approx(-5.0)  # only a warning overall...
    assert cmp.category_changes_pct["account"] == pytest.approx(-20.0)
    assert cmp.status == "fail"  # ...but account fell 20 points
