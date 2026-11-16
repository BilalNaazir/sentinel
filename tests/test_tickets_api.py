from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from sentinel.api import tickets
from sentinel.pipeline.runner import PipelineResult
from sentinel.worker.jobs import verify_trace_job

app = FastAPI()
app.include_router(tickets.router)
client = TestClient(app)


class FakeQueue:
    def __init__(self, fail: bool = False) -> None:
        self.jobs: list[tuple[Any, ...]] = []
        self.fail = fail

    def enqueue(self, func: Any, *args: Any) -> Any:
        if self.fail:
            raise RedisConnectionError("redis is down")
        self.jobs.append((func, *args))
        return type("Job", (), {"id": "job-1"})()


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A pretend pipeline that records how it was called."""
    calls: list[dict[str, Any]] = []

    def fake(text: str, **kwargs: Any) -> PipelineResult:
        calls.append({"text": text, **kwargs})
        return PipelineResult(trace_id="trace-1", status="success", category="billing")

    monkeypatch.setattr(tickets, "run_pipeline", fake)
    return calls


def test_a_ticket_runs_routed_and_traced_as_prod(monkeypatch: pytest.MonkeyPatch, pipeline: list) -> None:
    monkeypatch.setattr(tickets, "sampled", lambda trace_id, rate: False)
    resp = client.post("/tickets", json={"text": "Refund BD-1 please"})
    assert resp.status_code == 200
    assert resp.json()["result"]["category"] == "billing"
    assert resp.json()["verification_job"] is None
    assert pipeline[0]["source"] == "prod"
    assert pipeline[0]["model_id"] is None  # ROUTED


def test_a_sampled_ticket_is_queued_for_verification(monkeypatch: pytest.MonkeyPatch, pipeline: list) -> None:
    queue = FakeQueue()
    monkeypatch.setattr(tickets, "sampled", lambda trace_id, rate: True)
    monkeypatch.setattr(tickets, "get_queue", lambda: queue)
    resp = client.post("/tickets", json={"text": "Refund BD-1 please"})
    assert resp.json()["verification_job"] == "job-1"
    assert queue.jobs == [(verify_trace_job, "trace-1")]


def test_redis_down_does_not_fail_the_ticket(monkeypatch: pytest.MonkeyPatch, pipeline: list) -> None:
    monkeypatch.setattr(tickets, "sampled", lambda trace_id, rate: True)
    monkeypatch.setattr(tickets, "get_queue", lambda: FakeQueue(fail=True))
    resp = client.post("/tickets", json={"text": "Refund BD-1 please"})
    assert resp.status_code == 200
    assert resp.json()["verification_job"] is None


def test_an_empty_ticket_is_rejected(pipeline: list) -> None:
    assert client.post("/tickets", json={"text": ""}).status_code == 422
    assert pipeline == []
