from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from sentinel.api import flags
from sentinel.worker.jobs import diagnose_trace_job

app = FastAPI()
app.include_router(flags.router)
client = TestClient(app)
TRACE = "0297b06f-1412-4485-92c3-d8d6796c18a0"


class FakeQueue:
    def __init__(self, fail: bool = False) -> None:
        self.jobs: list[tuple[Any, ...]] = []
        self.fail = fail

    def enqueue(self, func: Any, *args: Any) -> Any:
        if self.fail:
            raise RedisConnectionError("redis is down")
        self.jobs.append((func, *args))
        return type("Job", (), {"id": "job-7"})()


def test_a_flag_queues_a_diagnosis_with_the_note(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = FakeQueue()
    monkeypatch.setattr(flags, "trace_exists", lambda trace_id: True)
    monkeypatch.setattr(flags, "get_queue", lambda: queue)
    resp = client.post(f"/v1/flags/{TRACE}", json={"note": "summary says upgrade, it was a downgrade"})
    assert resp.status_code == 202
    assert resp.json() == {"trace_id": TRACE, "job_id": "job-7", "status": "queued"}
    assert queue.jobs == [(diagnose_trace_job, TRACE, "summary says upgrade, it was a downgrade")]


def test_a_flag_without_a_body_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = FakeQueue()
    monkeypatch.setattr(flags, "trace_exists", lambda trace_id: True)
    monkeypatch.setattr(flags, "get_queue", lambda: queue)
    assert client.post(f"/v1/flags/{TRACE}").status_code == 202
    assert queue.jobs[0][2] is None


def test_an_unknown_trace_is_a_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flags, "trace_exists", lambda trace_id: False)
    assert client.post(f"/v1/flags/{TRACE}").status_code == 404


def test_redis_down_is_a_503_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flags, "trace_exists", lambda trace_id: True)
    monkeypatch.setattr(flags, "get_queue", lambda: FakeQueue(fail=True))
    assert client.post(f"/v1/flags/{TRACE}").status_code == 503


def test_no_diagnosis_yet_is_a_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flags, "latest_diagnosis", lambda trace_id: None)
    assert client.get(f"/v1/diagnoses/{TRACE}").status_code == 404
