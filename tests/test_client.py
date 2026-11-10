import anthropic
import httpx
import pytest

from sentinel import settings
from sentinel.gateway import client
from sentinel.gateway.client import GatewayError, send_request


def fake_anthropic(model, prompt, system, max_tokens):
    return "Hello there!", 1_000, 500


def failing_anthropic(model, prompt, system, max_tokens):
    raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))


def test_send_request_fills_in_tokens_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "_call_anthropic", fake_anthropic)
    resp = send_request("Say hello", "claude-haiku-5-5")
    assert resp.text == "Hello there!"
    assert (resp.tokens_in, resp.tokens_out) == (1_000, 500)
    assert resp.cost_usd == pytest.approx(0.00035)


def test_provider_errors_become_gateway_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "_call_anthropic", failing_anthropic)
    with pytest.raises(GatewayError, match="timed out") as info:
        send_request("Say hello", "claude-haiku-5-5")
    assert info.value.model_id == "claude-haiku-5-5"


def test_client_uses_timeout_and_retry_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_TIMEOUT_S", "12")
    monkeypatch.setenv("LLM_MAX_RETRIES", "5")
    settings.get_settings.cache_clear()
    client._anthropic_client.cache_clear()
    try:
        c = client._anthropic_client()
        assert c.timeout == 12
        assert c.max_retries == 5
    finally:
        settings.get_settings.cache_clear()
        client._anthropic_client.cache_clear()


def test_providers_not_connected_yet_say_so() -> None:
    with pytest.raises(NotImplementedError, match="not connected yet"):
        send_request("Say hello", "gpt-5.6-luna")
