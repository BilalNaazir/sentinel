import time
from datetime import UTC, datetime
from typing import Any

from sentinel.evals.run import load_criteria
from sentinel.forensics.analyzer import analyze_trace
from sentinel.forensics.store import save_diagnosis
from sentinel.router.verifier import verify_trace


def slow_ping(message: str, seconds: int = 5) -> dict[str, str]:
    """A stand-in for slow work, like an LLM call. Waits, then answers."""
    time.sleep(seconds)
    return {"message": message, "finished_at": datetime.now(UTC).isoformat()}


def verify_trace_job(trace_id: str) -> dict[str, Any]:
    """Re-check a live trace with the verifier model. Results go to the verifications table."""
    checks = verify_trace(trace_id, load_criteria())
    return {
        "trace_id": trace_id,
        "checked": [c.step for c in checks],
        "routing_failures": [c.step for c in checks if not c.verdict.agrees],
        "cost_usd": round(sum(c.cost_usd for c in checks), 6),
    }


def diagnose_trace_job(trace_id: str, flag_note: str | None = None) -> dict[str, Any]:
    """Find the root cause of a flagged trace and store it as a proposed diagnosis."""
    diagnosis = analyze_trace(trace_id)
    diagnosis_id = save_diagnosis(diagnosis, flag_note)
    return {
        "diagnosis_id": diagnosis_id,
        "root_cause_step": diagnosis.root_cause.span.step if diagnosis.root_cause else None,
        "failure_type": diagnosis.failure_type or "no_fault_found",
        "cost_usd": round(diagnosis.cost_usd, 6),
    }
