import pytest

from sentinel.forensics import faithfulness
from sentinel.forensics.faithfulness import TraceSpan, judge_step, score_span
from sentinel.gateway import client
from sentinel.gateway.registry import ModelConfig


def judge_replies(monkeypatch: pytest.MonkeyPatch, reply: str) -> list[str]:
    """The judge (Anthropic) answers with `reply`. Returns the prompts it was sent."""
    prompts: list[str] = []

    def fake(model: ModelConfig, prompt: str, system: str | None, max_tokens: int) -> client.CallResult:
        prompts.append(prompt)
        return reply, 300, 40

    monkeypatch.setitem(client.PROVIDERS, "anthropic", fake)
    return prompts


def span(step: str = "extraction", output: str | None = '{"reference_ids": ["BD-48213"]}') -> TraceSpan:
    return TraceSpan(
        "s1", step, "claude-haiku-5-5", 4, 1, "I was charged twice, please refund.", output, None
    )


def test_the_judge_sees_the_step_input_output_and_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = judge_replies(monkeypatch, '{"score": 1, "problem": "BD-48213 is not in the input"}')
    verdict = judge_step(
        "extraction", "I was charged twice, please refund.", '{"reference_ids": ["BD-48213"]}'
    )
    assert verdict.score == 1
    assert "I was charged twice" in prompts[0] and "BD-48213" in prompts[0]
    assert "Extract entities" in prompts[0]  # the extraction prompt's own instructions
    assert '"extraction"' in prompts[0]


def test_a_scored_span_carries_its_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    judge_replies(monkeypatch, '{"score": 2, "problem": "invents an order ID"}')
    result = score_span(span())
    assert (result.score, result.problem) == (2, "invents an order ID")
    assert result.cost_usd > 0


def test_a_failed_step_is_not_sent_to_the_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = judge_replies(monkeypatch, "unused")
    failed = TraceSpan(
        "s1", "extraction", "claude-haiku-5-5", None, 1, "x", None, "StepOutputError: bad JSON"
    )
    result = score_span(failed)
    assert result.score is None and "step failed" in (result.problem or "")
    assert prompts == []


def test_an_unreadable_judge_reply_is_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    judge_replies(monkeypatch, "I think it's fine")
    result = score_span(span())
    assert result.score is None and "could not judge" in (result.problem or "")


def test_steps_come_back_in_pipeline_order() -> None:
    assert faithfulness.STEP_ORDER == ("intake", "extraction", "classification", "summary")
