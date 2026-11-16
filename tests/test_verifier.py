import pytest

from sentinel.gateway import client
from sentinel.gateway.registry import ModelConfig
from sentinel.gateway.routing import get_routing
from sentinel.router import verifier
from sentinel.router.verifier import TicketOutcome, UsedStep, check_step, sampled, score_verifier

CRITERIA = verifier.load_criteria()
VERIFIER = get_routing().verifier_reference_model


def verifier_answers(monkeypatch: pytest.MonkeyPatch, text: str) -> list[str]:
    """The verifier model (OpenAI) answers with `text`. Returns the models called."""
    called: list[str] = []

    def fake_call(model: ModelConfig, prompt: str, system: str | None, max_tokens: int) -> client.CallResult:
        called.append(model.id)
        return text, 100, 20

    monkeypatch.setitem(client.PROVIDERS, "openai", fake_call)
    return called


def used(step: str, raw: str, escalated: bool = False) -> UsedStep:
    return UsedStep(
        span_id="s1", step=step, content="Order BD-1, charged twice", raw_response=raw, escalated=escalated
    )


def test_sampling_is_repeatable_and_roughly_the_right_size() -> None:
    ids = [f"trace-{i}" for i in range(2000)]
    picked = [t for t in ids if sampled(t, 0.1)]
    assert picked == [t for t in ids if sampled(t, 0.1)]  # same sample every time
    assert 150 < len(picked) < 250
    assert all(sampled(t, 1.0) for t in ids)
    assert not any(sampled(t, 0.0) for t in ids)


def test_agreement_is_not_a_routing_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    called = verifier_answers(monkeypatch, '{"category": "billing", "confidence": 5}')
    check = check_step(used("classification", '{"category": "billing", "confidence": 4}'), "t", CRITERIA)
    assert called == [VERIFIER]
    assert check.verdict.agrees
    assert check.verdict.score == 1.0
    assert check.cost_usd > 0


def test_disagreement_is_flagged_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier_answers(monkeypatch, '{"names": ["Grace Okafor"], "reference_ids": ["BD-1"], "confidence": 5}')
    check = check_step(used("extraction", '{"reference_ids": ["BD-1"], "confidence": 5}'), "t", CRITERIA)
    assert not check.verdict.agrees
    assert check.verdict.detail == "entity F1 0.67"
    assert check.reference is not None and check.reference["names"] == ["Grace Okafor"]


def test_a_failed_verifier_flags_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier_answers(monkeypatch, "sorry, no JSON here")
    check = check_step(used("classification", '{"category": "billing", "confidence": 4}'), "t", CRITERIA)
    assert check.verdict.agrees  # no evidence against the answer, so no routing failure
    assert check.reference is None  # and nothing is saved for it
    assert "verifier failed" in check.verdict.detail


def test_score_verifier_counts_each_box() -> None:
    outcomes = [
        TicketOutcome("A", passed=False, flagged_steps=["extraction"]),
        TicketOutcome("B", passed=True, flagged_steps=["summary"]),
        TicketOutcome("C", passed=False, flagged_steps=[]),
        TicketOutcome("D", passed=True, flagged_steps=[]),
        TicketOutcome("E", passed=True, flagged_steps=[]),
    ]
    assert score_verifier(outcomes) == {"caught": 1, "false_alarm": 1, "missed": 1, "quiet": 2}
