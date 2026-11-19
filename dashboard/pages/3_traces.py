"""Traces: one ticket's journey through the pipeline, step by step, with its diagnosis."""

import json
import os
from typing import Any

import httpx
import psycopg
import streamlit as st

from sentinel.forensics.store import latest_diagnosis
from sentinel.reporting.traces import (
    actual_answer,
    compare_answers,
    eval_case_for,
    recent_traces,
    step_colour,
    trace_spans,
)
from sentinel.settings import get_settings

API_URL = os.environ.get("API_URL", "http://localhost:8080")
DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴"}

st.set_page_config(page_title="Sentinel: Traces", layout="wide")
st.title("Traces")

SOURCES = {"All": None, "Live (prod)": "prod", "Eval runs": "eval", "CI / planted": "ci", "Dev": "dev"}
source = SOURCES[st.sidebar.selectbox("Traffic", list(SOURCES))]
ticket = st.sidebar.text_input("Ticket ID starts with", placeholder="GT-035, FX-08, HT-")


def connect() -> psycopg.Connection[Any]:
    return psycopg.connect(get_settings().database_url)


with connect() as conn:
    traces = recent_traces(conn, source, ticket)
if traces.empty:
    st.info("No traces match.")
    st.stop()

options = {
    f"{r.ticket}  ·  {r.source}  ·  {r.created_at:%Y-%m-%d %H:%M}  ·  {r.status}": r.trace_id
    for r in traces.itertuples()
}
trace_id = options[st.selectbox(f"Trace ({len(traces)} most recent)", list(options))]

with connect() as conn:
    spans = trace_spans(conn, trace_id)
    case = eval_case_for(conn, trace_id)
    actual = actual_answer(conn, trace_id)
diagnosis = latest_diagnosis(trace_id)
faith = {s["span_id"]: s["score"] for s in diagnosis["step_scores"]} if diagnosis else {}
pipeline_spans = [s for s in spans if s["step"] in ("intake", "extraction", "classification", "summary")]

st.subheader("Pipeline")
cols = st.columns(max(len(pipeline_spans), 1))
for col, s in zip(cols, pipeline_spans, strict=False):
    colour = step_colour(s, faith.get(s["span_id"]))
    note = f"↑ from {s['escalated_from']}" if s["escalated_from"] else (s["error"] or "")[:40]
    col.markdown(f"### {DOT[colour]} {s['step']}")
    col.caption(f"{s['model_id']} · conf {s['confidence'] or '-'} · ${s['cost_usd'] or 0:.6f}  \n{note}")

labels = [f"{DOT[step_colour(s, faith.get(s['span_id']))]} {s['order']}. {s['step']}" for s in spans]
chosen = spans[labels.index(st.radio("Step details", labels, horizontal=True))]
left, right = st.columns(2)
left.markdown("**Input**")
left.code(chosen["prompt"] or "(none)", language="text", wrap_lines=True)
right.markdown("**Output**" + (" (failed)" if chosen["error"] else ""))
try:
    right.json(json.loads(chosen["output"][chosen["output"].find("{") :]) if chosen["output"] else {})
except json.JSONDecodeError:
    right.code(chosen["output"], language="text", wrap_lines=True)
if chosen["error"]:
    right.error(chosen["error"])
facts = {
    k: chosen[k] for k in ("model_id", "tier", "escalation_reason", "tokens_in", "tokens_out", "latency_ms")
}
st.caption(" · ".join(f"{k}: {v}" for k, v in facts.items() if v is not None))

if case:
    verdict = "passed" if case["passed"] else "failed: " + "; ".join(case["reasons"])
    st.subheader(f"Expected vs pipeline ({case['case_id']}, {verdict})")
    table = compare_answers(case["expected"], actual)
    table["match"] = table["match"].map({True: "✓", False: "✗", None: ""})
    st.dataframe(table, width="stretch")

st.subheader("Diagnosis")
if diagnosis:
    root = diagnosis["root_cause_step"] or "no step at fault"
    st.markdown(
        f"**{diagnosis['failure_type']}** at **{root}** · status **{diagnosis['status']}** "
        f"· decided by {diagnosis['type_source']}"
    )
    st.text(diagnosis["evidence"])
    if diagnosis["flag_note"]:
        st.caption(f"Flag note: {diagnosis['flag_note']}")
else:
    st.caption("Not diagnosed yet.")
note = st.text_input("What looks wrong? (optional)", key="flag_note")
if st.button("Flag for diagnosis", help="Queues the backward analyzer in the worker (about 15 seconds)"):
    try:
        resp = httpx.post(f"{API_URL}/v1/flags/{trace_id}", json={"note": note or None}, timeout=10)
        resp.raise_for_status()
        st.success(
            f"Queued (job {resp.json()['job_id'][:8]}). Refresh in a few seconds to see the diagnosis."
        )
    except httpx.HTTPError as exc:
        st.error(f"Could not flag it through the API at {API_URL}: {exc}")
