import pytest

from sentinel.evals import judge
from sentinel.gateway import client
from sentinel.pipeline.models import StepOutputError
from sentinel.tracing.core import start_trace


def reply_with(monkeypatch: pytest.MonkeyPatch, text: str) -> list[str]:
    prompts: list[str] = []

    def fake_call(model, prompt, system, max_tokens):
        prompts.append(prompt)
        return text, 300, 40

    monkeypatch.setitem(client.PROVIDERS, "anthropic", fake_call)
    return prompts


def test_the_rubric_gets_the_ticket_reference_and_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = reply_with(monkeypatch, '{"score": 4, "reason": "Accurate but wordy."}')
    verdict = judge.judge_summary("TICKET TEXT", "REFERENCE TEXT", "CANDIDATE TEXT")
    assert verdict.score == 4
    sent = prompts[0]
    assert "TICKET TEXT" in sent and "REFERENCE TEXT" in sent and "CANDIDATE TEXT" in sent
    assert "{ticket_text}" not in sent  # every placeholder was filled
    assert '{"score": <1-5>' in sent  # ...but the JSON example in the rubric was left alone


def test_the_judge_uses_the_model_fixed_in_routing_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    reply_with(monkeypatch, '{"score": 5, "reason": "Complete."}')
    with start_trace() as trace:
        judge.judge_summary("t", "r", "c")
    assert trace.spans[0].step == "judge"
    assert trace.spans[0].model_id == judge.judge_model() == "claude-sonnet-5-5"


def test_a_score_outside_1_to_5_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    reply_with(monkeypatch, '{"score": 7, "reason": "Great!"}')
    with start_trace() as trace, pytest.raises(StepOutputError):
        judge.judge_summary("t", "r", "c")
    assert trace.status == "failed"
