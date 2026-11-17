"""The pipeline steps. Each one: load its prompt, call the model, parse and check the JSON reply.

python -m sentinel.pipeline.steps          # run all four steps on golden ticket GT-001
python -m sentinel.pipeline.steps GT-049   # ...or on any golden ticket
"""

from functools import cache
from typing import TypeVar

from pydantic import BaseModel

from sentinel.gateway.client import GatewayError, send_request
from sentinel.gateway.routing import Routing, TierNumber, get_routing
from sentinel.pipeline.models import (
    ClassificationOutput,
    Entities,
    ExtractionOutput,
    IntakeOutput,
    StepOutputError,
    SummaryOutput,
    parse_output,
)
from sentinel.pipeline.prompts import Prompt, load_prompt
from sentinel.tracing.core import current_trace, last_span

DEFAULT_MODEL = "claude-haiku-5-5"
# Upper limits, not targets: you only pay for tokens actually used. Generous limits leave room for
ROUTED = None  # pass model_id=ROUTED to let routing.yaml pick the model and escalate when needed
# the model to work through hard tickets before answering.
MAX_TOKENS = {"intake": 4000, "extraction": 2000, "classification": 1000, "summary": 2000}

M = TypeVar("M", bound=BaseModel)


@cache
def _prompt(step: str) -> Prompt:
    return load_prompt(step)


def _attempt(step: str, content: str, output_model: type[M], model_id: str) -> M:
    """Send `content` to one model with the step's prompt, then parse the reply into `output_model`."""
    prompt = _prompt(step)
    resp = send_request(
        f"Ticket:\n{content}",
        model_id,
        system=prompt.system_with_examples(),
        max_tokens=MAX_TOKENS[step],
        step=step,
    )
    span = last_span()
    try:
        out = parse_output(resp.text, output_model)
    except StepOutputError as exc:
        if span is not None:
            span.error = f"StepOutputError: {exc.reason}"
        raise
    if span is not None:
        span.confidence = getattr(out, "confidence", None)
    return out


def run_step(step: str, content: str, output_model: type[M], model_id: str | None) -> M:
    """Run a step on `model_id`, or let the router choose when model_id is ROUTED (None)."""
    if model_id is not None:
        return _attempt(step, content, output_model, model_id)
    return _run_routed(step, content, output_model, get_routing())


def _tag(tier: TierNumber, escalated_from: str | None = None, reason: str | None = None) -> None:
    """Record the router's decision on the span just made."""
    span = last_span()
    if span is not None:
        span.complexity_tier, span.escalated_from, span.escalation_reason = tier, escalated_from, reason


def _escalations_left(routing: Routing) -> bool:
    if not routing.escalation.enabled:
        return False
    trace = current_trace()
    used = sum(1 for s in trace.spans if s.escalated_from) if trace else 0
    return used < routing.escalation.max_escalations_per_request


def _run_routed(step: str, content: str, output_model: type[M], routing: Routing) -> M:
    """Tier 1 first. Escalate once, one tier up, if it fails or reports low confidence."""
    first = routing.model_for(step, 1)
    try:
        out = _attempt(step, content, output_model, first)
    except (GatewayError, StepOutputError) as exc:
        _tag(1)
        if not _escalations_left(routing):
            raise
        reason = f"failed: {type(exc).__name__}"
    else:
        _tag(1)
        confidence = getattr(out, "confidence", None)  # intake has none, so it only escalates on failure
        if confidence is None or confidence > routing.escalation.confidence_at_or_below:
            return out
        if not _escalations_left(routing):
            return out
        reason = f"confidence {confidence}"
    out = _attempt(step, content, output_model, routing.model_for(step, 2))
    _tag(2, escalated_from=first, reason=reason)
    return out


def intake(ticket_text: str, model_id: str | None = DEFAULT_MODEL) -> IntakeOutput:
    return run_step("intake", ticket_text, IntakeOutput, model_id)


def extract(clean_text: str, model_id: str | None = DEFAULT_MODEL) -> ExtractionOutput:
    return run_step("extraction", clean_text, ExtractionOutput, model_id)


def classify(clean_text: str, model_id: str | None = DEFAULT_MODEL) -> ClassificationOutput:
    return run_step("classification", clean_text, ClassificationOutput, model_id)


def summarise(
    clean_text: str,
    entities: Entities,
    category: str,
    language: str,
    model_id: str | None = DEFAULT_MODEL,
) -> SummaryOutput:
    return run_step(
        "summary", summary_content(clean_text, entities, category, language), SummaryOutput, model_id
    )


def summary_content(clean_text: str, entities: Entities, category: str, language: str) -> str:
    """What the summary step is sent, after the 'Ticket:' line."""
    return (
        f"{clean_text}\n\n"
        f"Language: {language}\n"
        f"Category: {category}\n"
        f"Extracted entities (unordered): {entities.model_dump_json()}"
    )


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    from sentinel.tracing.core import start_trace
    from sentinel.tracing.store import save_trace

    case_id = sys.argv[1] if len(sys.argv) > 1 else "GT-001"
    cases = json.loads(Path("data/eval_dataset.v2.json").read_text(encoding="utf-8"))["cases"]
    case = next(c for c in cases if c["case_id"] == case_id)
    expected = case["expected"]
    print(f"{case_id} ({case['difficulty']}): {case['input']['ticket_text']}\n")

    with start_trace(source="dev") as trace:
        cleaned = intake(case["input"]["ticket_text"])
        entities = extract(cleaned.clean_text)
        category = classify(cleaned.clean_text)
        summary = summarise(cleaned.clean_text, entities, category.category, cleaned.language)

    print(f"language   {cleaned.language}")
    print(f"cleaned    {cleaned.clean_text}")
    print(f"category   {category.category} (confidence {category.confidence})")
    print(f"expected   {expected['category']}")
    print(f"entities   {entities.model_dump(exclude={'confidence'})}")
    print(f"expected   {expected['entities']}")
    print(f"summary    {summary.summary}")
    print(f"expected   {expected['summary']}")
    print(f"next step  {summary.next_action}")
    print(f"\n{len(trace.spans)} spans, cost ${trace.total_cost_usd:.6f}, saved to {save_trace(trace)}")
