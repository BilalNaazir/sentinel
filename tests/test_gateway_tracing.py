import pytest

from sentinel.gateway import client
from sentinel.gateway.client import GatewayError, send_request
from sentinel.tracing.core import start_trace


def fake_call(model, prompt, system, max_tokens):
    return f"answer from {model.id}", 1_000, 500


def broken_call(model, prompt, system, max_tokens):
    raise GatewayError(model.id, "timed out")


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(client.PROVIDERS, "anthropic", fake_call)


def test_llm_calls_become_spans_with_model_tokens_and_cost() -> None:
    with start_trace() as trace:
        send_request("hi", "claude-haiku-5-5", step="classification")
    s = trace.spans[0]
    assert s.step == "classification"
    assert s.model_id == "claude-haiku-5-5"
    assert (s.tokens_in, s.tokens_out) == (1_000, 500)
    assert s.cost_usd == pytest.approx(0.00035)
    assert s.input["prompt"] == "hi"
    assert s.output == "answer from claude-haiku-5-5"


def test_trace_totals_add_up() -> None:
    with start_trace() as trace:
        send_request("hi", "claude-haiku-5-5")  # $0.00035
        send_request("hi", "claude-sonnet-5-5")  # 1,000 x $2/M + 500 x $10/M = $0.007
    assert trace.total_cost_usd == pytest.approx(0.00735)
    assert trace.status == "success"


def test_a_failed_llm_call_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(client.PROVIDERS, "anthropic", broken_call)
    with start_trace() as trace, pytest.raises(GatewayError):
        send_request("hi", "claude-haiku-5-5", step="summary")
    assert trace.spans[0].error == "GatewayError: claude-haiku-5-5: timed out"
    assert trace.spans[0].model_id == "claude-haiku-5-5"
    assert trace.status == "failed"
