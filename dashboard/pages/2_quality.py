"""Quality: pass rate across eval runs with a drift line, and the detail of any one run."""

import pandas as pd
import psycopg
import streamlit as st

from sentinel.reporting.quality import (
    category_table,
    drift_settings,
    drifting,
    entity_table,
    load_runs,
    score_counts,
    summary_of,
    with_drift,
)
from sentinel.settings import get_settings

st.set_page_config(page_title="Sentinel: Quality", layout="wide")
st.title("Quality")


@st.cache_data(ttl=60)
def runs_for(dataset_version: int | None) -> pd.DataFrame:
    with psycopg.connect(get_settings().database_url) as conn:
        return load_runs(conn, dataset_version)


@st.cache_data(ttl=60)
def scores_for(run_id: str) -> pd.Series:
    with psycopg.connect(get_settings().database_url) as conn:
        return score_counts(conn, run_id)


all_runs = runs_for(None)
if all_runs.empty:
    st.info("No eval runs yet: python -m sentinel.evals.run")
    st.stop()

versions = sorted(all_runs["dataset_version"].unique(), reverse=True)
chosen = st.sidebar.selectbox(
    "Dataset version",
    ["All"] + [f"v{v}" for v in versions],
    index=1,
    help="Pass rates are only comparable on the same dataset",
)
runs = all_runs if chosen == "All" else runs_for(int(chosen[1:]))
window, alert_below = drift_settings()
runs = with_drift(runs, window)

st.subheader("Pass rate across eval runs")
if drifting(runs, alert_below):
    st.error(
        f"Drift: the {window}-run average is {runs['moving_avg'].iloc[-1]:.0%}, below {alert_below:.0%}."
    )
chart = runs.set_index("created_at")[["pass_rate", "moving_avg"]].rename(
    columns={"pass_rate": "pass rate", "moving_avg": f"{window}-run average"}
)
chart["alert line"] = alert_below
st.line_chart(chart, y_label="pass rate")

st.subheader("One run in detail")
labels = list(runs["label"])[::-1]  # newest first
run = runs[runs["label"] == st.selectbox("Run", labels)].iloc[-1]
summary = summary_of(run)
cols = st.columns(5)
cols[0].metric("Pass rate", f"{run['pass_rate']:.0%}", help=f"{summary['passed']} of {run['cases']} cases")
cols[1].metric("Category accuracy", f"{summary['category_accuracy']:.0%}")
cols[2].metric(
    "Mean summary score", f"{summary['mean_summary_score']:.2f}" if summary["mean_summary_score"] else "-"
)
cols[3].metric("Pipeline failures", summary["pipeline_failures"])
cols[4].metric("Dataset", f"v{run['dataset_version']}")
st.caption(f"Prompts {run['prompts']} · pipeline v{run['pipeline']} · {run['created_at']:%Y-%m-%d %H:%M}")

left, right = st.columns(2)
left.markdown("**By category**")
left.dataframe(
    category_table(summary).style.format(
        {"cases": "{:.0f}", "pass_rate": "{:.0%}", "category_accuracy": "{:.0%}"}
    ),
    width="stretch",
)
right.markdown("**Entities**")
right.dataframe(
    entity_table(summary).style.format(
        {"precision": "{:.0%}", "recall": "{:.0%}", "f1": "{:.0%}"}, na_rep="-"
    ),
    width="stretch",
)
st.markdown("**Judged summary scores** (4 or more passes)")
st.bar_chart(scores_for(run["run_id"]), x_label="score", y_label="summaries")
