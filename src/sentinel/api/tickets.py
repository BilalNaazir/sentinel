"""POST /tickets: run the routed pipeline on a live ticket, and send a sample to the verifier."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue

from sentinel.pipeline.runner import PipelineResult, run_pipeline
from sentinel.pipeline.steps import ROUTED
from sentinel.router.verifier import sampled
from sentinel.settings import get_settings
from sentinel.worker.jobs import verify_trace_job

log = logging.getLogger(__name__)
router = APIRouter()


class TicketIn(BaseModel):
    text: str = Field(min_length=1)
    ticket_id: str | None = None


class TicketOut(BaseModel):
    result: PipelineResult
    verification_job: str | None = None  # set when this ticket was picked for verification


def get_queue() -> Queue:
    return Queue("default", connection=Redis.from_url(get_settings().redis_url))


@router.post("/tickets")
def handle_ticket(ticket: TicketIn) -> TicketOut:
    """Live traffic: routed models, traced as source 'prod'. Verification happens later, in the worker."""
    result = run_pipeline(ticket.text, ticket_id=ticket.ticket_id, source="prod", model_id=ROUTED)
    out = TicketOut(result=result)
    if result.status == "success" and sampled(result.trace_id, get_settings().verify_sample_rate):
        try:
            out.verification_job = get_queue().enqueue(verify_trace_job, result.trace_id).id
        except RedisError as exc:  # verifying is optional: never fail the customer's ticket over it
            log.warning("could not queue verification for %s: %s", result.trace_id, exc)
    return out
