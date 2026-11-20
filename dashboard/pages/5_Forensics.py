"""Forensics: how well Sentinel finds root causes, what is waiting for review, the verifier, and the judge."""

from typing import Any

import psycopg
import streamlit as st

from sentinel.reporting.forensics import (
    diagnosis_timing,
    latest_calibration,
    planted_answers,
    planted_score,
    review_queue,
    verifier_stats,
)
from sentinel.settings import get_settings

st.set_page_config(page_title="Sentinel: Forensics", layout="wide")
st.title("Forensics")


def connect() -> psycopg.Connection[Any]:
    return psycopg.connect(get_settings().database_url)


with connect() as conn:
    score = planted_score(conn, planted_answers())
    queue = review_queue(conn)
    timing = diagnosis_timing(conn)
    verifier = verifier_stats(conn)

st.subheader("Root cause on the planted failures")
diagnosed = score[score["diagnosed step"] != "not diagnosed"]
cols = st.columns(4)
cols[0].metric(
    "Root cause step right", f"{int(score['step right'].sum())}/{len(score)}", help="plan target: 8 of 10"
)
cols[1].metric("Failure type right", f"{int(score['type right'].sum())}/{len(score)}")
cols[2].metric(
    "Median time to root cause",
    f"{timing['median_s']:.1f} s" if timing["timed"] else "-",
    help=f"{timing['timed']} timed diagnoses, slowest {timing['slowest_s']:.1f} s. Plan target: under 30 s",
)
cols[3].metric("Cost per diagnosis", f"${timing['mean_cost']:.4f}" if timing["timed"] else "-")
if diagnosed.empty:
    st.caption("Not diagnosed yet: python -m sentinel.forensics.analyzer --planted --save")
mark = {True: "✓", False: "✗"}
st.dataframe(
    score.assign(
        **{"step right": score["step right"].map(mark), "type right": score["type right"].map(mark)}
    ),
    width="stretch",
    hide_index=True,
)

left, right = st.columns(2)
left.subheader("Review queue")
if queue.empty:
    left.caption("No diagnoses yet.")
else:
    left.dataframe(queue, width="stretch")
    left.caption(
        "proposed = waiting for a person · confirmed = became an eval case · label_review = check the label"
    )

right.subheader("Verifier")
if verifier.empty:
    right.caption("Nothing verified yet.")
else:
    right.dataframe(
        verifier.style.format({"mean agreement": "{:.2f}", "flag rate": "{:.0%}"}), width="stretch"
    )
    right.caption(
        "A flag is a disagreement with the reference model: it still needs review to say who was right."
    )

st.subheader("Judge calibration")
calibration = latest_calibration()
if calibration is None:
    st.caption("No calibration report found in reports/ (python -m sentinel.evals.calibrate).")
else:
    a = calibration["agreement"]
    cols = st.columns(5)
    cols[0].metric("Within 1 point of a human", f"{a['within_one']:.0%}")
    cols[1].metric("Exact agreement", f"{a['exact']:.0%}")
    cols[2].metric("Bias", f"{a['bias']:+.2f}", help="negative = harsher than the human scores")
    cols[3].metric("Cases", a["cases"])
    cols[4].metric("Verdict", "trusted" if a["trusted"] else "not trusted")
    st.caption(f"{calibration['judge_model']} · {calibration['file']}")
