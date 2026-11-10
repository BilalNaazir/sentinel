import time
from functools import lru_cache

import anthropic
from pydantic import BaseModel

from sentinel.gateway.registry import ModelConfig, Registry, load_registry
from sentinel.settings import get_settings


class GatewayError(Exception):
    """Any failure calling a model, whichever provider it came from."""

    def __init__(self, model_id: str, reason: str) -> None:
        super().__init__(f"{model_id}: {reason}")
        self.model_id = model_id
        self.reason = reason


class LLMResponse(BaseModel):
    text: str
    model_id: str
    provider: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


@lru_cache
def _registry() -> Registry:
    return load_registry()


@lru_cache
def _anthropic_client() -> anthropic.Anthropic:
    s = get_settings()
    if not s.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.Anthropic(
        api_key=s.anthropic_api_key,
        timeout=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
    )


def _call_anthropic(
    model: ModelConfig, prompt: str, system: str | None, max_tokens: int
) -> tuple[str, int, int]:
    """Returns (text, input tokens, output tokens)."""
    msg = _anthropic_client().messages.create(
        model=model.id,
        max_tokens=max_tokens,
        system=system if system else anthropic.omit,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    return text, msg.usage.input_tokens, msg.usage.output_tokens


def send_request(
    prompt: str, model_id: str, system: str | None = None, max_tokens: int = 1024
) -> LLMResponse:
    model = _registry().get(model_id)
    start = time.perf_counter()
    try:
        if model.provider == "anthropic":
            text, tokens_in, tokens_out = _call_anthropic(model, prompt, system, max_tokens)
        else:
            raise NotImplementedError(f"Provider {model.provider!r} is not connected yet.")
    except anthropic.APITimeoutError as exc:
        raise GatewayError(model.id, "timed out") from exc
    except anthropic.APIConnectionError as exc:
        raise GatewayError(model.id, "could not connect") from exc
    except anthropic.APIStatusError as exc:
        raise GatewayError(model.id, f"HTTP {exc.status_code}: {exc.message}") from exc
    latency_ms = int((time.perf_counter() - start) * 1000)
    return LLMResponse(
        text=text,
        model_id=model.id,
        provider=model.provider,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=model.cost(tokens_in, tokens_out),
        latency_ms=latency_ms,
    )
