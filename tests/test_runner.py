import json

import pytest

from sentinel.gateway import client
from sentinel.pipeline import runner

INTAKE = '{"clean_text": "I was charged $49.99 twice. Order BD-48213.", "language": "en"}'
EXTRACTION = '{"amounts": ["$49.99"], "reference_ids": ["BD-48213"], "confidence": 5}'
CLASSIFICATION = '{"category": "billing", "confidence": 5}'
SUMMARY = '{"summary": "Charged $49.99 twice on BD-48213.", "next_action": "Refund one.", "confidence": 4}'


def replies(monkeypatch: pytest.MonkeyPatch, *texts: str) -> None:
    queue = list(texts)
    monkeypatch.setitem(client.PROVIDERS, "anthropic", lambda m, p, s, t: (queue.pop(0), 100, 20))


@pytest.fixture(autouse=True)
def no_saving(monkeypatch: pytest.MonkeyPatch) -> list:
    saved: list = []
    monkeypatch.setattr(runner, "save_trace", saved.append)
    return saved


def test_a_full_run_fills_in_every_field(monkeypatch: pytest.MonkeyPatch, no_saving: list) -> None:
    replies(monkeypatch, INTAKE, EXTRACTION, CLASSIFICATION, SUMMARY)
    result = runner.run_pipeline("Hi, I was charged...", ticket_id="GT-001")
    assert result.status == "success"
    assert result.language == "en"
    assert result.entities is not None and result.entities.amounts == ["$49.99"]
    assert result.category == "billing"
    assert result.summary == "Charged $49.99 twice on BD-48213."
    assert result.cost_usd == pytest.approx(4 * (100 * 0.10 + 20 * 0.50) / 1_000_000)

    trace = no_saving[0]
    assert [s.step for s in trace.spans] == ["intake", "extraction", "classification", "summary"]
    assert trace.ticket_id == "GT-001"
    assert trace.prompt_version == "intake@1,extraction@1,classification@1,summary@1"


def test_a_failing_step_stops_the_run_without_raising(
    monkeypatch: pytest.MonkeyPatch, no_saving: list
) -> None:
    replies(monkeypatch, INTAKE, EXTRACTION, '{"category": "refunds", "confidence": 5}')
    result = runner.run_pipeline("Hi, I was charged...")
    assert result.status == "failed"
    assert result.failed_step == "classification"
    assert "StepOutputError" in (result.error or "")
    assert result.entities is not None  # steps before the failure are kept
    assert result.summary is None  # steps after it never ran
    assert no_saving[0].status == "failed"  # the failed trace is still saved


def test_the_result_is_plain_json() -> None:
    result = runner.PipelineResult(trace_id="t", status="success", category="billing")
    assert json.loads(result.model_dump_json())["category"] == "billing"
