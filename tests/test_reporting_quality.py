from datetime import datetime

import pandas as pd

from sentinel.reporting.quality import category_table, drift_settings, drifting, entity_table, with_drift


def runs(*rates: float) -> pd.DataFrame:
    return pd.DataFrame(
        {"created_at": [datetime(2026, 11, d) for d in range(1, len(rates) + 1)], "pass_rate": list(rates)}
    )


def test_moving_average_uses_fewer_runs_at_the_start() -> None:
    out = with_drift(runs(0.9, 0.8, 1.0, 0.6), window=3)
    assert list(out["moving_avg"].round(4)) == [0.9, 0.85, 0.9, 0.8]


def test_drift_is_judged_on_the_latest_average() -> None:
    assert drifting(with_drift(runs(0.95, 0.95, 0.70), window=3), alert_below=0.85) is False  # avg 0.867
    assert drifting(with_drift(runs(0.95, 0.75, 0.70), window=3), alert_below=0.85) is True  # avg 0.80
    assert drifting(with_drift(runs(), window=3), alert_below=0.85) is False


def test_drift_settings_come_from_thresholds() -> None:
    window, alert = drift_settings()
    assert window >= 1 and 0 < alert < 1


def test_run_summary_tables() -> None:
    summary = {
        "by_category": {"billing": {"cases": 33, "pass_rate": 0.97, "category_accuracy": 1.0}},
        "entities": {"names": {"precision": 1.0, "recall": None, "f1": None, "expected": 0, "predicted": 0}},
    }
    assert category_table(summary).loc["billing", "cases"] == 33
    assert pd.isna(entity_table(summary).loc["names", "recall"])
