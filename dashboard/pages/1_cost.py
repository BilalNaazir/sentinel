"""Cost: what Sentinel spends, what it saves by routing, and how often it escalates."""

import pandas as pd
import psycopg
import streamlit as st

from sentinel.gateway.registry import load_registry
from sentinel.reporting.cost import daily_spend, escalation_rate, load_spans, model_share, savings
from sentinel.settings import get_settings

st.set_page_config(page_title="Sentinel: Cost", layout="wide")
st.title("Cost")

SOURCES = {"All traffic": None, "Live (prod)": "prod", "Eval runs": "eval", "CI": "ci", "Dev": "dev"}
WINDOWS = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "All time": None}
source = SOURCES[st.sidebar.selectbox("Traffic", list(SOURCES))]
days = WINDOWS[st.sidebar.selectbox("Period", list(WINDOWS), index=1)]


@st.cache_data(ttl=60)  # re-query at most once a minute, not on every click
def spans_for(source: str | None, days: int | None) -> pd.DataFrame:
    with psycopg.connect(get_settings().database_url) as conn:
        return load_spans(conn, source, days)


spans = spans_for(source, days)
if spans.empty:
    st.info("No model calls in this period.")
    st.stop()

registry = load_registry()
s = savings(spans, registry)
e = escalation_rate(spans)

cols = st.columns(5)
cols[0].metric("Total spend", f"${spans['cost_usd'].sum():.4f}", help="Every model call, including judges")
cols[1].metric("Pipeline cost per ticket", f"${s['actual_per_ticket']:.6f}", help=f"{s['tickets']} tickets")
cols[2].metric(f"If all on {registry.savings_baseline_model}", f"${s['baseline_per_ticket']:.6f}")
cols[3].metric("Saved by routing", f"{s['saved_pct']:.0f}%")
cols[4].metric(
    "Escalation rate",
    f"{e['rate']:.1%}",
    help=f"{e['escalated_tickets']} of {e['routed_tickets']} routed tickets had a step escalated",
)
st.caption(
    f"Savings are an estimate: each pipeline call re-priced as if it had run on "
    f"{registry.savings_baseline_model} with the same number of tokens."
)

st.subheader("Spend per day")
st.bar_chart(daily_spend(spans), y_label="USD")

st.subheader("Traffic by model")
share = model_share(spans)
left, right = st.columns(2)
left.bar_chart(share["calls"], y_label="calls")
right.dataframe(
    share.style.format({"cost_usd": "${:.4f}", "share_of_calls": "{:.0%}"}),
    width="stretch",
)
