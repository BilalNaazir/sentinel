"""Regressions: every eval run, what changed between any two, and the known issues being tracked."""

from typing import Any

import psycopg
import streamlit as st

from sentinel.reporting.regressions import compare_runs, flips_table, known_issues, run_history
from sentinel.settings import get_settings

st.set_page_config(page_title="Sentinel: Regressions", layout="wide")
st.title("Regressions")


def connect() -> psycopg.Connection[Any]:
    return psycopg.connect(get_settings().database_url)


with connect() as conn:
    history = run_history(conn)
if history.empty:
    st.info("No eval runs yet: python -m sentinel.evals.run")
    st.stop()

st.subheader("Run history")
st.dataframe(
    history.drop(columns=["run_id"]).style.format({"pass_rate": "{:.0%}", "created_at": "{:%Y-%m-%d %H:%M}"}),
    width="stretch",
    hide_index=True,
)

st.subheader("Compare two runs")
names = {
    f"{r.label}  ·  v{r.dataset}  ·  {r.created_at:%Y-%m-%d %H:%M}": r.run_id for r in history.itertuples()
}
labels = list(names)
left, right = st.columns(2)
base_label = left.selectbox("Baseline", labels, index=min(1, len(labels) - 1))
cand_label = right.selectbox("Candidate", labels, index=0)
if base_label == cand_label:
    st.caption("Pick two different runs.")
else:
    with connect() as conn:
        cmp = compare_runs(conn, names[base_label], names[cand_label])
        flips = flips_table(cmp)
    show = {"pass": st.success, "warn": st.warning, "fail": st.error}[cmp.status]
    show(f"{cmp.status.upper()}" + (": " + "; ".join(cmp.status_reasons) if cmp.status_reasons else ""))
    cols = st.columns(4)
    cols[0].metric(
        "Pass rate change", f"{cmp.pass_rate_change_pct:+.1f} points", help="on the cases both runs share"
    )
    cols[1].metric("Regressions", len(cmp.regressions))
    cols[2].metric("Improvements", len(cmp.improvements))
    cols[3].metric(
        "Summary score change",
        f"{cmp.summary_score_change:+.2f}" if cmp.summary_score_change is not None else "-",
    )
    st.caption(
        "By category (points): " + ", ".join(f"{c} {v:+.1f}" for c, v in cmp.category_changes_pct.items())
    )
    if flips.empty:
        st.caption("No case changed result.")
    else:
        st.dataframe(flips, width="stretch", hide_index=True)
    st.caption(f"Still failing in both: {', '.join(cmp.still_failing) or 'none'}")

st.subheader("Known issues")
with connect() as conn:
    flagged, reviews = known_issues(conn)
st.markdown("**Flagged cases**, history oldest → newest. *Regressed* means it passed before and fails now.")
if flagged.empty:
    st.caption("No flagged cases yet.")
else:
    st.dataframe(
        flagged.style.map(
            lambda s: "color: red; font-weight: bold" if s == "regressed" else "", subset=["status"]
        ),
        width="stretch",
        hide_index=True,
    )
st.markdown("**Open label reviews**")
if reviews.empty:
    st.caption("None open.")
else:
    st.dataframe(reviews, width="stretch", hide_index=True)
