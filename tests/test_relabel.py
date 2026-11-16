from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from sentinel.evals.run import PassCriteria
from sentinel.gateway.client import GatewayError
from sentinel.pipeline.models import ClassificationOutput, ExtractionOutput, IntakeOutput
from sentinel.router import relabel
from sentinel.router.relabel import (
    Upstream,
    Verdict,
    compare,
    label_request,
    needs,
    read_cache,
    rows_for_tickets,
    same_language,
)

CRITERIA = PassCriteria()
UP = Upstream(ticket_text="refund pls", clean_text="refund pls", language="en")


def fake_runner(answers: dict[str, Any]) -> relabel.Runner:
    """A pretend model call: each model id gives a fixed answer (or raises it, if it's an exception)."""

    def run(step: str, model_id: str, up: Upstream) -> BaseModel:
        answer = answers[model_id]
        if isinstance(answer, Exception):
            raise answer
        return ClassificationOutput(category=answer, confidence=5)

    return run


def same_category(step: str, up: Upstream, cand: Any, ref: Any, criteria: PassCriteria) -> Verdict:
    return Verdict(cand.category == ref.category, f"{cand.category} vs {ref.category}")


def models(step: str = "classification") -> dict[int, str]:
    routing = relabel.get_routing()
    return {t: routing.model_for(step, t) for t in (1, 2, 3)}


def test_tier_1_when_the_cheap_model_agrees() -> None:
    m = models()
    run = fake_runner({m[1]: "billing", m[2]: "billing", m[3]: "billing"})
    assert label_request("classification", UP, CRITERIA, run, same_category)["tier"] == 1


def test_tier_2_when_only_the_middle_model_agrees() -> None:
    m = models()
    run = fake_runner({m[1]: "general", m[2]: "billing", m[3]: "billing"})
    assert label_request("classification", UP, CRITERIA, run, same_category)["tier"] == 2


def test_tier_3_when_neither_agrees() -> None:
    m = models()
    run = fake_runner({m[1]: "general", m[2]: "account", m[3]: "billing"})
    out = label_request("classification", UP, CRITERIA, run, same_category)
    assert out["tier"] == 3
    assert set(out["tried"]) == {m[1], m[2]}


def test_a_failed_cheap_answer_counts_as_disagreement() -> None:
    m = models()
    run = fake_runner({m[1]: GatewayError(m[1], "timeout"), m[2]: "billing", m[3]: "billing"})
    out = label_request("classification", UP, CRITERIA, run, same_category)
    assert out["tier"] == 2
    assert "failed" in out["tried"][m[1]]


def test_reference_failure_is_raised_not_labelled() -> None:
    m = models()
    run = fake_runner({m[1]: "billing", m[2]: "billing", m[3]: GatewayError(m[3], "timeout")})
    with pytest.raises(GatewayError):
        label_request("classification", UP, CRITERIA, run, same_category)


def test_compare_extraction_uses_entity_f1() -> None:
    ref = ExtractionOutput(reference_ids=["BD-1"], amounts=["$19"], confidence=5)
    half = ExtractionOutput(reference_ids=["BD-1"], confidence=5)
    assert compare("extraction", UP, ref, ref, CRITERIA).agrees
    assert not compare("extraction", UP, half, ref, CRITERIA).agrees  # F1 0.67 < 0.8


def test_compare_intake_needs_similar_text_and_same_language() -> None:
    ref = IntakeOutput(clean_text="My invoice is wrong.", language="en")
    assert compare(
        "intake", UP, IntakeOutput(clean_text="My invoice is wrong", language="EN"), ref, CRITERIA
    ).agrees
    assert not compare(
        "intake", UP, IntakeOutput(clean_text="My invoice is wrong.", language="es"), ref, CRITERIA
    ).agrees
    assert not compare("intake", UP, IntakeOutput(clean_text="Invoice.", language="en"), ref, CRITERIA).agrees


def test_needs_only_fetches_what_the_steps_use() -> None:
    assert needs(["intake"]) == "intake"
    assert needs(["intake", "classification"]) == "clean"
    assert needs(["extraction", "summary"]) == "summary"


def test_read_cache_skips_failed_rows(tmp_path: Path) -> None:
    cache = tmp_path / "cache.jsonl"
    cache.write_text(
        '{"request_id": "CX-1", "tier": 1}\n{"request_id": "CX-2", "tier": null}\n', encoding="utf-8"
    )
    assert set(read_cache(cache)) == {"CX-1"}  # CX-2 failed, so it will be retried


def test_rows_for_tickets_makes_one_row_per_step() -> None:
    rows = rows_for_tickets([{"ticket_id": "HT-001", "stress_type": "long_thread", "ticket_text": "hi"}])
    assert [r["request_id"] for r in rows] == [
        "HT-001-intake",
        "HT-001-extraction",
        "HT-001-classification",
        "HT-001-summary",
    ]
    assert {r["stress_type"] for r in rows} == {"long_thread"}


def test_mixed_language_codes_match_when_they_share_a_language() -> None:
    assert same_language("en", "ar-en")
    assert same_language("en-ar", "AR-EN")
    assert not same_language("en", "es")
