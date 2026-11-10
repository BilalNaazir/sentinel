import json
from pathlib import Path

import psycopg
import pytest

from sentinel.gateway import client
from sentinel.gateway.client import send_request
from sentinel.health import check_postgres
from sentinel.settings import get_settings
from sentinel.tracing.core import Trace, start_trace, traced
from sentinel.tracing.store import save_trace_db, save_trace_json


def fake_call(model, prompt, system, max_tokens):
    return "billing", 50, 1


@traced("intake")
def clean(text: str) -> str:
    return text.strip()


@pytest.fixture
def sample_trace(monkeypatch: pytest.MonkeyPatch) -> Trace:
    monkeypatch.setitem(client.PROVIDERS, "anthropic", fake_call)
    with start_trace() as trace:
        text = clean("  My invoice is wrong ")
        send_request(text, "claude-haiku-5-5", step="classification")
    return trace


def test_json_file_has_every_span_and_the_totals(sample_trace: Trace, tmp_path: Path) -> None:
    path = save_trace_json(sample_trace, folder=tmp_path)
    assert path.name == f"{sample_trace.trace_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [s["step"] for s in data["spans"]] == ["intake", "classification"]
    assert data["status"] == "success"
    assert data["total_cost_usd"] == pytest.approx(sample_trace.total_cost_usd)


def test_trace_and_spans_are_saved_to_postgres(sample_trace: Trace) -> None:
    url = get_settings().database_url
    if not check_postgres(url).ok:
        pytest.skip("Postgres is not running (start it with: docker compose up -d)")
    save_trace_db(sample_trace)
    try:
        with psycopg.connect(url) as conn:
            row = conn.execute(
                "SELECT status, total_cost_usd FROM traces WHERE trace_id = %s", (sample_trace.trace_id,)
            ).fetchone()
            spans = conn.execute(
                "SELECT step, model_id, tokens_in FROM spans WHERE trace_id = %s ORDER BY step_order",
                (sample_trace.trace_id,),
            ).fetchall()
        assert row is not None and row[0] == "success"
        assert float(row[1]) == pytest.approx(sample_trace.total_cost_usd)
        assert spans == [("intake", None, 0), ("classification", "claude-haiku-5-5", 50)]
    finally:
        with psycopg.connect(url) as conn:
            conn.execute("DELETE FROM traces WHERE trace_id = %s", (sample_trace.trace_id,))
