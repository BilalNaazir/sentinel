"""The numbers behind the dashboard's Cost page. Queries return a DataFrame; the sums are plain functions
of that DataFrame, so they can be tested without a database.

Savings are an estimate: each pipeline call is re-priced as if it had run on the savings baseline model
(models.yaml: savings_baseline_model) with the same token counts.
"""

from typing import Any

import pandas as pd
import psycopg

from sentinel.gateway.registry import Registry

PIPELINE_STEPS = ("intake", "extraction", "classification", "summary")


def load_spans(conn: psycopg.Connection[Any], source: str | None, days: int | None) -> pd.DataFrame:
    """Every model call (span with a model) in the window, with its trace's source."""
    rows = conn.execute(
        """
        SELECT s.started_at::date AS day, t.source, t.trace_id::text, s.step, s.model_id,
               coalesce(s.tokens_in, 0), coalesce(s.tokens_out, 0), coalesce(s.cost_usd, 0)::float,
               s.complexity_tier, s.escalated_from IS NOT NULL
        FROM spans s JOIN traces t USING (trace_id)
        WHERE s.model_id IS NOT NULL
          AND (%(source)s::text IS NULL OR t.source = %(source)s)
          AND (%(days)s::int IS NULL OR s.started_at > now() - make_interval(days => %(days)s))
        """,
        {"source": source, "days": days},
    ).fetchall()
    columns = [
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
    return pd.DataFrame(rows, columns=columns)


def daily_spend(spans: pd.DataFrame) -> pd.DataFrame:
    """Spend per day, one column per source (prod, eval, ...)."""
    if spans.empty:
        return pd.DataFrame()
    return spans.pivot_table(index="day", columns="source", values="cost_usd", aggfunc="sum", fill_value=0.0)


def model_share(spans: pd.DataFrame) -> pd.DataFrame:
    """Calls and cost per model, with each model's share of all calls."""
    if spans.empty:
        return pd.DataFrame(columns=["calls", "cost_usd", "share_of_calls"])
    by_model = spans.groupby("model_id").agg(calls=("step", "size"), cost_usd=("cost_usd", "sum"))
    by_model["share_of_calls"] = by_model["calls"] / by_model["calls"].sum()
    return by_model.sort_values("calls", ascending=False)


def savings(spans: pd.DataFrame, registry: Registry) -> dict[str, float]:
    """Actual pipeline cost against the same calls priced on the baseline model."""
    pipeline = spans[spans["step"].isin(PIPELINE_STEPS)]
    tickets = pipeline["trace_id"].nunique()
    baseline = registry.get(registry.savings_baseline_model)
    actual = float(pipeline["cost_usd"].sum())
    if_baseline = float(
        sum(
            baseline.cost(int(i), int(o))
            for i, o in zip(pipeline["tokens_in"], pipeline["tokens_out"], strict=True)
        )
    )
    return {
        "tickets": tickets,
        "actual": actual,
        "if_baseline": if_baseline,
        "actual_per_ticket": actual / tickets if tickets else 0.0,
        "baseline_per_ticket": if_baseline / tickets if tickets else 0.0,
        "saved_pct": 100 * (1 - actual / if_baseline) if if_baseline else 0.0,
    }


def escalation_rate(spans: pd.DataFrame) -> dict[str, float]:
    """Among routed tickets (any span with a tier), the share where some step was escalated."""
    routed = spans[spans["tier"].notna()]
    tickets = routed["trace_id"].nunique()
    escalated = routed.loc[routed["escalated"], "trace_id"].nunique()
    return {
        "routed_tickets": tickets,
        "escalated_tickets": escalated,
        "rate": escalated / tickets if tickets else 0.0,
    }
