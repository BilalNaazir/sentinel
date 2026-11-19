from datetime import date

import pandas as pd

from sentinel.gateway.registry import load_registry
from sentinel.reporting.cost import daily_spend, escalation_rate, model_share, savings

COLUMNS = [
    "day",
    "source",
    "trace_id",
    "step",
    "model_id",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "tier",
    "escalated",
]


def spans(*rows: tuple) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=COLUMNS)


D1, D2 = date(2026, 11, 17), date(2026, 11, 18)
SAMPLE = spans(
    (D1, "prod", "t1", "intake", "claude-haiku-5-5", 1000, 200, 0.0001, 1, False),
    (D1, "prod", "t1", "summary", "claude-haiku-5-5", 1000, 200, 0.0001, 1, False),
    (D2, "prod", "t2", "intake", "claude-haiku-5-5", 1000, 200, 0.0001, 1, False),
    (D2, "prod", "t2", "classification", "claude-sonnet-5-5", 1000, 50, 0.001, 2, True),
    (D2, "eval", "t3", "judge", "claude-sonnet-5-5", 2000, 100, 0.002, None, False),
)


def test_daily_spend_splits_by_source() -> None:
    table = daily_spend(SAMPLE)
    assert table.loc[D1, "prod"] == 0.0002
    assert table.loc[D2, "eval"] == 0.002


def test_model_share_counts_calls() -> None:
    share = model_share(SAMPLE)
    assert share.loc["claude-haiku-5-5", "calls"] == 3
    assert round(share["share_of_calls"].sum(), 6) == 1.0


def test_savings_count_pipeline_steps_only_and_reprice_them() -> None:
    registry = load_registry()
    result = savings(SAMPLE, registry)
    assert result["tickets"] == 2  # t3 only has a judge call
    assert round(result["actual"], 6) == 0.0013
    baseline = registry.get(registry.savings_baseline_model)
    expected = 3 * baseline.cost(1000, 200) + baseline.cost(1000, 50)
    assert round(result["if_baseline"], 8) == round(expected, 8)
    assert result["saved_pct"] == 100 * (1 - result["actual"] / result["if_baseline"])


def test_escalation_rate_is_per_routed_ticket() -> None:
    result = escalation_rate(SAMPLE)
    assert (result["routed_tickets"], result["escalated_tickets"]) == (2, 1)
    assert result["rate"] == 0.5


def test_empty_data_does_not_crash() -> None:
    empty = spans()
    assert daily_spend(empty).empty
    assert model_share(empty).empty
    assert savings(empty, load_registry())["saved_pct"] == 0.0
    assert escalation_rate(empty)["rate"] == 0.0
