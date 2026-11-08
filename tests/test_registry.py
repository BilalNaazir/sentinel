import pytest

from sentinel.gateway.registry import load_registry

registry = load_registry()


def test_all_models_load() -> None:
    assert len(registry.models) == 7
    assert registry.savings_baseline_model in registry.models


def test_haiku_cost() -> None:
    # 1,000 input tokens at $0.10/M + 500 output tokens at $0.50/M
    assert registry.get("claude-haiku-5-5").cost(1_000, 500) == pytest.approx(0.00035)


def test_sonnet_cost() -> None:
    # 1,000 x $2/M + 500 x $10/M
    assert registry.get("claude-sonnet-5-5").cost(1_000, 500) == pytest.approx(0.007)


def test_haiku_long_prompt_uses_long_context_prices() -> None:
    # over 100K input tokens: $0.50/M in, $2.50/M out
    assert registry.get("claude-haiku-5-5").cost(200_000, 1_000) == pytest.approx(0.1025)


def test_unknown_model_gives_a_clear_error() -> None:
    with pytest.raises(KeyError, match="Unknown model"):
        registry.get("gpt-4o")


def test_anthropic_models() -> None:
    ids = {m.id for m in registry.by_provider("anthropic")}
    assert ids == {"claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"}
