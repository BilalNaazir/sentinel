from pathlib import Path

import pytest
import yaml

from sentinel.gateway import baseline, client
from sentinel.gateway.client import GatewayError

YAML = """\
models:
  # ---- tier: low
  - id: model-a
    provider: anthropic
    avg_latency_ms: null        # fill from your Phase 1 baseline run
  - id: model-b
    provider: openai
    avg_latency_ms: null
"""


def test_update_latencies_changes_only_the_named_models_and_keeps_comments(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(YAML, encoding="utf-8")
    baseline.update_latencies(path, {"model-a": 812})
    text = path.read_text(encoding="utf-8")
    assert "# ---- tier: low" in text
    data = yaml.safe_load(text)
    assert [m["avg_latency_ms"] for m in data["models"]] == [812, None]


def test_load_tickets_spreads_across_the_golden_set() -> None:
    tickets = baseline.load_tickets()
    assert len(tickets) == 10
    assert len(set(tickets)) == 10


def test_run_model_reports_median_latency_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(client.PROVIDERS, "anthropic", lambda m, p, s, t: ("summary", 100, 20))
    monkeypatch.setattr(baseline, "save_trace", lambda trace: None)
    model = client._registry().get("claude-haiku-5-5")
    result = baseline.run_model(model, ["ticket one", "ticket two", "ticket three"])
    assert result.calls == 3
    assert result.failed is None
    assert result.median_latency_ms is not None
    assert result.total_cost_usd == pytest.approx(3 * (100 * 0.10 + 20 * 0.50) / 1_000_000)


def test_run_model_stops_at_the_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_credit(m, p, s, t):
        raise GatewayError(m.id, "HTTP 429: no credits remaining")

    monkeypatch.setitem(client.PROVIDERS, "openai", no_credit)
    monkeypatch.setattr(baseline, "save_trace", lambda trace: None)
    result = baseline.run_model(client._registry().get("gpt-5.6-luna"), ["a", "b", "c"])
    assert result.calls == 0
    assert result.failed == "HTTP 429: no credits remaining"
