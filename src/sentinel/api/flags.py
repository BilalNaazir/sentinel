"""POST /v1/flags/{trace_id}: someone thinks this answer is wrong. Diagnose it in the background."""

from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from redis.exceptions import RedisError

from sentinel.api.tickets import get_queue
from sentinel.forensics.store import latest_diagnosis
from sentinel.settings import get_settings
from sentinel.worker.jobs import diagnose_trace_job

router = APIRouter(prefix="/v1")


class FlagIn(BaseModel):
    note: str | None = None  # what the reviewer thinks is wrong, kept with the diagnosis


class FlagOut(BaseModel):
    trace_id: str
    job_id: str
    status: str = "queued"


def trace_exists(trace_id: str) -> bool:
    with psycopg.connect(get_settings().database_url) as conn:
        row = conn.execute("SELECT 1 FROM traces WHERE trace_id::text = %s", (trace_id,)).fetchone()
    return row is not None


@router.post("/flags/{trace_id}", status_code=202)
def flag_trace(trace_id: str, flag: FlagIn | None = None) -> FlagOut:
    """202 Accepted: the diagnosis runs in the worker and takes about 15 seconds."""
    if not trace_exists(trace_id):
        raise HTTPException(status_code=404, detail=f"no trace {trace_id}")
    note = flag.note if flag else None
    try:
        job = get_queue().enqueue(diagnose_trace_job, trace_id, note)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail=f"could not queue the diagnosis: {exc}") from exc
    return FlagOut(trace_id=trace_id, job_id=job.id)


@router.get("/diagnoses/{trace_id}")
def get_diagnosis(trace_id: str) -> dict[str, Any]:
    diagnosis = latest_diagnosis(trace_id)
    if diagnosis is None:
        raise HTTPException(status_code=404, detail=f"no diagnosis for trace {trace_id} yet")
    return diagnosis
