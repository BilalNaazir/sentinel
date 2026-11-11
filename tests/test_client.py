import anthropic
import httpx
import openai
import pytest

from sentinel import settings
from sentinel.gateway import client
from sentinel.gateway.client import GatewayError, send_request


def fake_call(model, prompt, system, max_tokens):
    return f"Hello from {model.provider}!", 1_000, 500


def anthropic_timeout(model, prompt, system, max_tokens):
    raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))


def openai_timeout(model, prompt, system, max_tokens):
    raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))


@pytest.fixture
def fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(client.PROVIDERS, "anthropic", fake_call)
    monkeypatch.setitem(client.PROVIDERS, "openai", fake_call)


def test_anthropic_cost(fake_providers: None) -> None:
    resp = send_request("Say hello", "claude-haiku-5-5")
    assert resp.text == "Hello from anthropic!"
    assert resp.cost_usd == pytest.approx(0.00035)  # 1,000 x $0.10/M + 500 x $0.50/M


def test_openai_cost(fake_providers: None) -> None:
    resp = send_request("Say hello", "gpt-5.6-luna")
    assert resp.text == "Hello from openai!"
    assert resp.cost_usd == pytest.approx(0.0008)  # 1,000 x $0.20/M + 500 x $1.20/M


@pytest.mark.parametrize(
    ("provider", "model_id", "failing_call"),
    [
        ("anthropic", "claude-haiku-5-5", anthropic_timeout),
        ("openai", "gpt-5.6-luna", openai_timeout),
    ],
)
def test_timeouts_become_gateway_errors(
    monkeypatch: pytest.MonkeyPatch, provider: str, model_id: str, failing_call: object
) -> None:
    monkeypatch.setitem(client.PROVIDERS, provider, failing_call)
    with pytest.raises(GatewayError, match="timed out") as info:
        send_request("Say hello", model_id)
    assert info.value.model_id == model_id


def test_client_uses_timeout_and_retry_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_TIMEOUT_S", "12")
    monkeypatch.setenv("LLM_MAX_RETRIES", "5")
    settings.get_settings.cache_clear()
    client._anthropic_client.cache_clear()
    client._openai_client.cache_clear()
    try:
        for c in (client._anthropic_client(), client._openai_client()):
            assert c.timeout == 12
            assert c.max_retries == 5
    finally:
        settings.get_settings.cache_clear()
        client._anthropic_client.cache_clear()
        client._openai_client.cache_clear()


def test_ollama_is_not_connected_yet() -> None:
    with pytest.raises(NotImplementedError, match="not connected yet"):
        send_request("Say hello", "llama-local")


def test_a_reply_cut_off_by_max_tokens_is_an_error_not_a_partial_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    cut_off = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(type="text", text='{"category": "billing",')],
        usage=SimpleNamespace(input_tokens=500, output_tokens=100),
    )
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: cut_off))
    monkeypatch.setattr(client, "_anthropic_client", lambda: fake_client)
    with pytest.raises(GatewayError, match="ran out of tokens"):
        send_request("Classify this", "claude-haiku-5-5", max_tokens=100)
