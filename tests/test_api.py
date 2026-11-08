import pytest
from fastapi.testclient import TestClient

from sentinel.api.main import app
from sentinel.health import Check

client = TestClient(app)


def test_live_says_ok() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_is_503_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sentinel.api.main.check_postgres", lambda url: Check("postgres", True, "7 tables"))
    monkeypatch.setattr("sentinel.api.main.check_redis", lambda url: Check("redis", False, "ConnectionError"))
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
