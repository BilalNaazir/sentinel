"""The pipeline steps. Each one: load its prompt, call the model, parse and check the JSON reply.

python -m sentinel.pipeline.steps   # run intake + extraction on golden ticket GT-001
"""

from functools import cache
from typing import TypeVar

from pydantic import BaseModel

from sentinel.gateway.client import send_request
from sentinel.pipeline.models import ExtractionOutput, IntakeOutput, StepOutputError, parse_output
from sentinel.pipeline.prompts import Prompt, load_prompt
from sentinel.tracing.core import last_span

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOKENS = {"intake": 2000, "extraction": 600, "classification": 100, "summary": 400}

M = TypeVar("M", bound=BaseModel)


@cache
def _prompt(step: str) -> Prompt:
    return load_prompt(step)


def run_step(step: str, content: str, output_model: type[M], model_id: str) -> M:
    """Send `content` to the model with the step's prompt, then parse the reply into `output_model`."""
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


def intake(ticket_text: str, model_id: str = DEFAULT_MODEL) -> IntakeOutput:
    return run_step("intake", ticket_text, IntakeOutput, model_id)


def extract(clean_text: str, model_id: str = DEFAULT_MODEL) -> ExtractionOutput:
    return run_step("extraction", clean_text, ExtractionOutput, model_id)


if __name__ == "__main__":
    import json
    from pathlib import Path

    from sentinel.tracing.core import start_trace
    from sentinel.tracing.store import save_trace

    case = json.loads(Path("data/eval_dataset.v1.json").read_text(encoding="utf-8"))["cases"][0]
    print(f"{case['case_id']}: {case['input']['ticket_text']}\n")
    with start_trace(source="dev") as trace:
        cleaned = intake(case["input"]["ticket_text"])
        entities = extract(cleaned.clean_text)
    print("intake    ", cleaned.model_dump())
    print("extraction", entities.model_dump())
    print("expected  ", case["expected"]["entities"])
    print(f"\ncost ${trace.total_cost_usd:.6f}, saved to {save_trace(trace)}")
