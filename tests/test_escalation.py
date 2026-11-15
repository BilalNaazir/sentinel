"""The router: tier 1 first, one tier up when tier 1 fails or is unsure, at most once per request."""

import pytest

from sentinel.gateway import client
from sentinel.gateway.registry import ModelConfig
from sentinel.gateway.routing import get_routing
from sentinel.pipeline import runner, steps
from sentinel.pipeline.models import StepOutputError
from sentinel.tracing.core import start_trace

ROUTING = get_routing()
TIER_1 = ROUTING.model_for("extraction", 1)
TIER_2 = ROUTING.model_for("extraction", 2)
LOW = ROUTING.escalation.confidence_at_or_below


def models_answer(monkeypatch: pytest.MonkeyPatch, answers: dict[str, list[str]]) -> list[str]:
    """Each model gives its next answer from its own list. Returns the models called, in order."""
    called: list[str] = []

    def fake_call(model: ModelConfig, prompt: str, system: str | None, max_tokens: int) -> client.CallResult:
        called.append(model.id)
        return answers[model.id].pop(0), 100, 20

    monkeypatch.setitem(client.PROVIDERS, "anthropic", fake_call)
    return called


def entities(confidence: int) -> str:
    return f'{{"reference_ids": ["BD-1"], "confidence": {confidence}}}'


def test_a_confident_tier_1_answer_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    called = models_answer(monkeypatch, {TIER_1: [entities(5)]})
    with start_trace() as trace:
        out = steps.extract("Order BD-1", steps.ROUTED)
    assert out.confidence == 5
    assert called == [TIER_1]
    assert trace.spans[0].complexity_tier == 1
    assert trace.spans[0].escalated_from is None


def test_low_confidence_escalates_one_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    called = models_answer(monkeypatch, {TIER_1: [entities(LOW)], TIER_2: [entities(5)]})
    with start_trace() as trace:
        out = steps.extract("Order BD-1", steps.ROUTED)
    assert out.confidence == 5  # the tier 2 answer is the one returned
    assert called == [TIER_1, TIER_2]
    first, second = trace.spans
    assert (first.complexity_tier, first.escalated_from) == (1, None)
    assert (second.complexity_tier, second.escalated_from) == (2, TIER_1)
    assert second.escalation_reason == f"confidence {LOW}"


def test_a_failed_tier_1_answer_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    called = models_answer(monkeypatch, {TIER_1: ['{"names": []}'], TIER_2: [entities(4)]})
    with start_trace() as trace:
        steps.extract("Order BD-1", steps.ROUTED)
    assert called == [TIER_1, TIER_2]
    assert trace.spans[0].error is not None  # the failed attempt stays in the trace: we paid for it
    assert trace.spans[1].escalation_reason == "failed: StepOutputError"
    assert trace.total_cost_usd > 0


def test_only_one_escalation_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    called = models_answer(monkeypatch, {TIER_1: [entities(LOW), entities(LOW)], TIER_2: [entities(5)]})
    with start_trace() as trace:
        steps.extract("first", steps.ROUTED)  # escalates
        out = steps.extract("second", steps.ROUTED)  # budget used: keeps the low-confidence answer
    assert out.confidence == LOW
    assert called == [TIER_1, TIER_2, TIER_1]
    assert sum(1 for s in trace.spans if s.escalated_from) == 1


def test_a_failure_with_no_budget_left_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    models_answer(monkeypatch, {TIER_1: [entities(LOW), "not json"], TIER_2: [entities(5)]})
    with start_trace(), pytest.raises(StepOutputError):
        steps.extract("first", steps.ROUTED)
        steps.extract("second", steps.ROUTED)


def test_a_pinned_model_never_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    called = models_answer(monkeypatch, {TIER_1: [entities(1)]})
    with start_trace():
        out = steps.extract("Order BD-1", TIER_1)
    assert out.confidence == 1
    assert called == [TIER_1]


def test_a_routed_pipeline_records_the_routing_version(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {
        ROUTING.model_for("intake", 1): [
            '{"clean_text": "Refund BD-1", "language": "en"}',
            entities(5),
            '{"category": "billing", "confidence": 5}',
            '{"summary": "Wants a refund for BD-1.", "next_action": "Refund.", "confidence": 5}',
        ]
    }
    models_answer(monkeypatch, answers)
    saved: list = []
    monkeypatch.setattr(runner, "save_trace", saved.append)
    result = runner.run_pipeline("Refund BD-1", model_id=steps.ROUTED)
    assert result.status == "success"
    assert saved[0].routing_version == ROUTING.version
    assert {s.complexity_tier for s in saved[0].spans} == {1}
