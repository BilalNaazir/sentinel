"""Run the whole 4-step pipeline on one ticket, as one trace, and return one result.

python -m sentinel.pipeline.runner GT-021                 # a golden ticket
python -m sentinel.pipeline.runner --text "refund pls"    # any text
"""

import argparse
import json

from pydantic import BaseModel

from sentinel.gateway.client import GatewayError
from sentinel.pipeline import steps
from sentinel.pipeline.models import Entities, StepOutputError
from sentinel.settings import get_settings
from sentinel.tracing.core import last_span, start_trace
from sentinel.tracing.store import save_trace

PIPELINE_VERSION = "1"
STEPS = ["intake", "extraction", "classification", "summary"]


class PipelineResult(BaseModel):
    trace_id: str
    ticket_id: str | None = None
    status: str  # "success" or "failed"
    failed_step: str | None = None
    error: str | None = None
    language: str | None = None
    clean_text: str | None = None
    entities: Entities | None = None
    category: str | None = None
    category_confidence: int | None = None
    summary: str | None = None
    next_action: str | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0


def prompt_versions() -> str:
    """e.g. 'intake@1,extraction@1,classification@1,summary@1'"""
    return ",".join(steps._prompt(step).ref for step in STEPS)


def run_pipeline(
    ticket_text: str,
    ticket_id: str | None = None,
    source: str = "dev",
    model_id: str = steps.DEFAULT_MODEL,
    save: bool = True,
) -> PipelineResult:
    """Run all four steps. A failing step stops the run, but never raises: the result says what failed."""
    with start_trace(source=source) as trace:
        trace.ticket_id = ticket_id
        trace.pipeline_version = PIPELINE_VERSION
        trace.prompt_version = prompt_versions()
        result = PipelineResult(trace_id=trace.trace_id, ticket_id=ticket_id, status="success")
        try:
            cleaned = steps.intake(ticket_text, model_id)
            result.language, result.clean_text = cleaned.language, cleaned.clean_text

            extracted = steps.extract(cleaned.clean_text, model_id)
            result.entities = Entities(**extracted.model_dump(exclude={"confidence"}))

            classified = steps.classify(cleaned.clean_text, model_id)
            result.category, result.category_confidence = classified.category, classified.confidence

            summarised = steps.summarise(
                cleaned.clean_text, result.entities, classified.category, cleaned.language, model_id
            )
            result.summary, result.next_action = summarised.summary, summarised.next_action
        except (GatewayError, StepOutputError) as exc:
            span = last_span()
            result.status = "failed"
            result.failed_step = span.step if span else None
            result.error = f"{type(exc).__name__}: {exc}"

    result.cost_usd = trace.total_cost_usd
    result.latency_ms = trace.total_latency_ms
    if save:
        save_trace(trace)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("case_id", nargs="?", default="GT-001", help="golden case ID, e.g. GT-021")
    parser.add_argument("--text", help="run on this text instead of a golden ticket")
    args = parser.parse_args()

    if args.text:
        result = run_pipeline(args.text)
    else:
        golden = json.loads(get_settings().golden_path.read_text(encoding="utf-8"))["cases"]
        case = next(c for c in golden if c["case_id"] == args.case_id)
        result = run_pipeline(case["input"]["ticket_text"], ticket_id=args.case_id)

    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
