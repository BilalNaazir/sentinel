import os

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8080")

st.set_page_config(page_title="Sentinel", layout="wide")
st.title("Sentinel")
st.caption("LLM reliability platform")

try:
    body = httpx.get(f"{API_URL}/health/ready", timeout=5).json()
    st.subheader(f"Platform status: {body['status']}")

    col_api, col_pg, col_redis = st.columns(3)
    col_api.metric("API", "ok")
    pg, rd = body["checks"]["postgres"], body["checks"]["redis"]
    col_pg.metric("Postgres", "ok" if pg["ok"] else "down", pg["detail"] or None)
    col_redis.metric("Redis", "ok" if rd["ok"] else "down", rd["detail"] or None)
except httpx.HTTPError:
    st.error(f"Can't reach the API at {API_URL}. Is uvicorn running?")
