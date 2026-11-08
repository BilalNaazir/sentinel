from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sentinel import __version__
from sentinel.health import check_postgres, check_redis
from sentinel.settings import get_settings

app = FastAPI(title="Sentinel", version=__version__)


@app.get("/health/live")
def live() -> dict[str, str]:
    """The API process is running."""
    return {"status": "ok", "version": __version__}


@app.get("/health/ready")
def ready() -> JSONResponse:
    """Postgres and Redis are reachable. Returns 503 if either is not."""
    s = get_settings()
    checks = [check_postgres(s.database_url), check_redis(s.redis_url)]
    all_ok = all(c.ok for c in checks)
    body: dict[str, Any] = {
        "status": "ok" if all_ok else "degraded",
        "checks": {c.name: {"ok": c.ok, "detail": c.detail} for c in checks},
    }
    return JSONResponse(body, status_code=200 if all_ok else 503)