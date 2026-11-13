import json

import psycopg
import pytest

from sentinel.evals import run
from sentinel.evals.store import save_run
from sentinel.gateway import client
from sentinel.health import check_postgres
from sentinel.pipeline.run_golden import load_cases
from sentinel.settings import get_settings


def fake(model, prompt, system, max_tokens):
    if "strict, fair grader" in (system or ""):
        return '{"score": 5, "reason": "Complete."}', 300, 30
    if "prepare customer support tickets" in system:
        return json.dumps({"clean_text": prompt.split("\n", 1)[1], "language": "en"}), 300, 60
    if "Extract entities" in system:
        return '{"confidence": 4}', 250, 50
    if "Classify" in system:
        return '{"category": "billing", "confidence": 4}', 400, 10
    return '{"summary": "x", "next_action": "y", "confidence": 4}', 300, 30


def test_a_run_and_its_results_are_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    url = get_settings().database_url
    if not check_postgres(url).ok:
        pytest.skip("Postgres is not running (start it with: docker compose up -d)")
    monkeypatch.setitem(client.PROVIDERS, "anthropic", fake)

    cases = load_cases(ids=["GT-003", "GT-015"])  # tickets with few entities
    results = [run.evaluate_case(c, run.load_criteria()) for c in cases]
    run_id = save_run(cases, results, run.summarise(results), label="test run")
    try:
        with psycopg.connect(url) as conn:
            label, n_cases = conn.execute(
                "SELECT label, n_cases FROM eval_runs WHERE run_id = %s", (run_id,)
            ).fetchone() or (None, None)
            rows = conn.execute(
                "SELECT case_id, category, reasons FROM eval_results WHERE run_id = %s ORDER BY case_id",
                (run_id,),
            ).fetchall()
        assert (label, n_cases) == ("test run", 2)
        assert [r[0] for r in rows] == ["GT-003", "GT-015"]
        assert all(r[1] == "billing" for r in rows)
    finally:
        with psycopg.connect(url) as conn:
            conn.execute("DELETE FROM eval_runs WHERE run_id = %s", (run_id,))
            conn.execute("DELETE FROM traces WHERE trace_id = ANY(%s)", ([r.trace_id for r in results],))
