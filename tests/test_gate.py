import json
from pathlib import Path

from sentinel.evals.gate import baseline_from_report, by_case, check, comment, to_run


def report(passed: dict[str, bool], cost: float = 0.0005) -> dict:
    """A small eval report: 25 technical cases (enough for per-category checks) plus whatever is given."""
    results = [
        {
            "case_id": cid,
            "passed": ok,
            "reasons": [] if ok else ["entity F1 0.00"],
            "summary_score": 5,
            "expected_category": "technical",
            "summary": f"summary of {cid}",
            "trace_id": None,
        }
        for cid, ok in passed.items()
    ]
    n = sum(passed.values())
    summary = {
        "cases": len(results),
        "passed": n,
        "pass_rate": n / len(results),
        "mean_summary_score": 4.9,
        "avg_cost_usd": cost,
        "pipeline_failures": 0,
    }
    return {"summary": summary, "results": results}


ALL_PASS = {f"GT-{i:03d}": True for i in range(1, 26)}


def write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_baseline_keeps_per_case_results_without_traces() -> None:
    base = baseline_from_report(report(ALL_PASS), "baseline v7")
    assert base["label"] == "baseline v7"
    assert set(base["results"]) == set(ALL_PASS)
    assert "trace_id" not in base["results"]["GT-001"]
    assert to_run(base, "b").results["GT-001"].passed


def test_reports_and_baselines_read_the_same_way() -> None:
    rep = report(ALL_PASS)
    assert by_case(rep).keys() == by_case(baseline_from_report(rep, "x")).keys()


def test_the_same_run_passes_the_gate(tmp_path: Path) -> None:
    base = write(tmp_path, "base.json", baseline_from_report(report(ALL_PASS), "b"))
    cmp = check(write(tmp_path, "cand.json", report(ALL_PASS)), base)
    assert cmp.status == "pass"


def test_a_big_drop_fails_the_gate_and_names_the_regressions(tmp_path: Path) -> None:
    worse = dict(ALL_PASS) | {f"GT-{i:03d}": False for i in range(1, 6)}  # 5 of 25 now fail: -20 points
    base = write(tmp_path, "base.json", baseline_from_report(report(ALL_PASS), "b"))
    cmp = check(write(tmp_path, "cand.json", report(worse)), base)
    assert cmp.status == "fail"
    assert [f.case_id for f in cmp.regressions] == [f"GT-{i:03d}" for i in range(1, 6)]


def test_the_comment_leads_with_the_verdict_and_shows_cost(tmp_path: Path) -> None:
    base_data = baseline_from_report(report(ALL_PASS, cost=0.0005), "baseline v7")
    worse = report(dict(ALL_PASS) | {"GT-001": False}, cost=0.001)
    cmp = check(write(tmp_path, "cand.json", worse), write(tmp_path, "base.json", base_data))
    text = comment(cmp, base_data, worse)
    assert text.startswith("## ")
    assert "+100%" in text  # cost per ticket doubled
    assert "**GT-001**: entity F1 0.00" in text
