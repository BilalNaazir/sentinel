import time
from collections.abc import Callable
from functools import lru_cache

import anthropic
import openai
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


# (text, input tokens, output tokens)
CallResult = tuple[str, int, int]
ProviderCall = Callable[[ModelConfig, str, str | None, int], CallResult]


@lru_cache
def _registry() -> Registry:
    return load_registry()


# ---------------------------------------------------------------- Anthropic
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


def _call_anthropic(model: ModelConfig, prompt: str, system: str | None, max_tokens: int) -> CallResult:
    msg = _anthropic_client().messages.create(
        model=model.id,
        max_tokens=max_tokens,
        system=system if system else anthropic.omit,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    return text, msg.usage.input_tokens, msg.usage.output_tokens


# ---------------------------------------------------------------- OpenAI
@lru_cache
def _openai_client() -> openai.OpenAI:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
    return openai.OpenAI(
        api_key=s.openai_api_key,
        timeout=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
    )


def _call_openai(model: ModelConfig, prompt: str, system: str | None, max_tokens: int) -> CallResult:
    resp = _openai_client().responses.create(
        model=model.id,
        input=prompt,
        instructions=system if system else openai.omit,
        max_output_tokens=max_tokens,
    )
    if resp.status == "incomplete" and not resp.output_text:
        reason = resp.incomplete_details.reason if resp.incomplete_details else "unknown"
        raise GatewayError(model.id, f"stopped before answering ({reason}); try a larger max_tokens")
    usage = resp.usage
    tokens_in = usage.input_tokens if usage else 0
    tokens_out = usage.output_tokens if usage else 0
    return resp.output_text, tokens_in, tokens_out


# ---------------------------------------------------------------- the one public function
PROVIDERS: dict[str, ProviderCall] = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
}

TIMEOUT_ERRORS = (anthropic.APITimeoutError, openai.APITimeoutError)
CONNECTION_ERRORS = (anthropic.APIConnectionError, openai.APIConnectionError)
STATUS_ERRORS = (anthropic.APIStatusError, openai.APIStatusError)


def send_request(
    prompt: str, model_id: str, system: str | None = None, max_tokens: int = 1024
) -> LLMResponse:
    model = _registry().get(model_id)
    call = PROVIDERS.get(model.provider)
    if call is None:
        raise NotImplementedError(f"Provider {model.provider!r} is not connected yet.")
    start = time.perf_counter()
    try:
        text, tokens_in, tokens_out = call(model, prompt, system, max_tokens)
    except TIMEOUT_ERRORS as exc:
        raise GatewayError(model.id, "timed out") from exc
    except CONNECTION_ERRORS as exc:
        raise GatewayError(model.id, "could not connect") from exc
    except STATUS_ERRORS as exc:
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
