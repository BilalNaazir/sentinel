import pytest

from sentinel.gateway import client
from sentinel.gateway.client import send_request


def fake_anthropic(model, prompt, system, max_tokens):
    return "Hello there!", 1_000, 500


def test_send_request_fills_in_tokens_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "_call_anthropic", fake_anthropic)
    resp = send_request("Say hello", "claude-haiku-5-5")
    assert resp.text == "Hello there!"
    assert resp.provider == "anthropic"
    assert (resp.tokens_in, resp.tokens_out) == (1_000, 500)
    assert resp.cost_usd == pytest.approx(0.00035)
    assert resp.latency_ms >= 0


def test_providers_not_built_yet_say_so() -> None:
    with pytest.raises(NotImplementedError, match="Step 3"):
        send_request("Say hello", "gpt-5.6-luna")
