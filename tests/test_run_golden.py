import pytest

from sentinel.pipeline import run_golden
from sentinel.pipeline.models import Entities
from sentinel.pipeline.runner import PipelineResult


def case(case_id: str, category: str, names: list[str]) -> dict:
    entities = {field: [] for field in run_golden.ENTITY_FIELDS} | {"names": names}
    return {"case_id": case_id, "expected": {"category": category, "entities": entities}}


def result(category: str | None, names: list[str], status: str = "success") -> PipelineResult:
    return PipelineResult(
        trace_id="t",
        status=status,
        category=category,
        entities=Entities(names=names) if status == "success" else None,
        failed_step=None if status == "success" else "classification",
        cost_usd=0.001,
        latency_ms=2000,
    )


def test_scorecard_counts_categories_entities_and_failures() -> None:
    pairs = [
        (case("A", "billing", ["Maria Lopez"]), result("billing", ["Maria Lopez"])),
        (case("B", "billing", ["Tom Becker"]), result("billing", [])),  # name lost
        (case("C", "account", []), result("technical", [])),  # wrong category
        (case("D", "general", []), result(None, [], status="failed")),
    ]
    card = run_golden.scorecard(pairs)
    assert card["tickets"] == 4
    assert card["failed"] == 1
    assert card["failed_steps"] == {"classification": 1}
    assert card["category_accuracy"] == pytest.approx(2 / 3)
    assert card["category_accuracy_by_category"] == {"account": 0.0, "billing": 1.0}
    assert card["entity_exact_match"]["names"] == pytest.approx(2 / 3)
    assert card["entity_exact_match"]["amounts"] == 1.0
    assert card["total_cost_usd"] == pytest.approx(0.004)


def test_order_of_entities_does_not_matter() -> None:
    pairs = [(case("A", "billing", ["B", "A"]), result("billing", ["A", "B"]))]
    assert run_golden.scorecard(pairs)["entity_exact_match"]["names"] == 1.0


def test_load_cases_respects_the_limit() -> None:
    assert len(run_golden.load_cases(limit=3)) == 3


def test_load_cases_can_pick_ids() -> None:
    cases = run_golden.load_cases(ids=["GT-027", "GT-089"])
    assert [c["case_id"] for c in cases] == ["GT-027", "GT-089"]
