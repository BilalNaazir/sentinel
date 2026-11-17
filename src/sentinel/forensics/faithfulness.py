"""Score each step of a trace against its OWN input: did this step do its job with what it was given?

This is the building block of the backward analyzer. A step that is faithful to a bad input is not
the cause of a failure; the first step that wasn't faithful is.

python -m sentinel.forensics.faithfulness FX-01            # latest trace for a ticket ID
python -m sentinel.forensics.faithfulness 3f2a9c1e         # or a trace ID (or its first characters)
python -m sentinel.forensics.faithfulness --planted        # all 10 planted failures
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import psycopg
from pydantic import BaseModel, Field

from sentinel.evals.judge import judge_model, load_rubric
from sentinel.forensics.planted import PIPELINE_VERSION, load_cases
from sentinel.gateway.client import GatewayError, send_request
from sentinel.pipeline.models import StepOutputError, parse_output
from sentinel.pipeline.prompts import load_prompt
from sentinel.settings import get_settings
from sentinel.tracing.core import start_trace

STEP_ORDER = ("intake", "extraction", "classification", "summary")
RUBRIC = "step_faithfulness_v2"
PROMPT_PREFIX = "Ticket:\n"
JUDGE_SYSTEM = (
    "You are a strict, fair reviewer of one step in a support-ticket pipeline. Reply with JSON only."
)


class Faithfulness(BaseModel):
    score: int = Field(ge=1, le=5)
    problem: str | None = None


@dataclass
class TraceSpan:
    """The answer a step actually returned (the last span for that step)."""

    span_id: str
    step: str
    model_id: str | None
    confidence: int | None
    complexity_tier: int | None
    input_text: str
    output_text: str | None
    error: str | None


@dataclass
class StepScore:
    span: TraceSpan
    score: int | None  # None when the step failed or couldn't be judged
    problem: str | None
    cost_usd: float


def trace_spans(conn: psycopg.Connection[Any], trace_id: str) -> list[TraceSpan]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (step) span_id::text, step, model_id, confidence, complexity_tier,
               input_json->>'prompt', raw_response, error
        FROM spans WHERE trace_id = %s AND step = ANY(%s)
        ORDER BY step, step_order DESC
        """,
        (trace_id, list(STEP_ORDER)),
    ).fetchall()
    spans = [
        TraceSpan(r[0], r[1], r[2], r[3], r[4], (r[5] or "").removeprefix(PROMPT_PREFIX), r[6], r[7])
        for r in rows
    ]
    return sorted(spans, key=lambda s: STEP_ORDER.index(s.step))


def judge_step(step: str, input_text: str, output_text: str) -> Faithfulness:
    """Ask the judge whether OUTPUT is a faithful result of this step's instructions applied to INPUT."""
    prompt = load_rubric(RUBRIC).fill(
        step=step,
        step_instructions=load_prompt(step).system.strip(),
        input=input_text,
        output=output_text,
    )
    resp = send_request(prompt, judge_model(), system=JUDGE_SYSTEM, max_tokens=1000, step="forensics_judge")
    return parse_output(resp.text, Faithfulness)


def score_span(span: TraceSpan) -> StepScore:
    if span.error or span.output_text is None:
        return StepScore(span, None, f"step failed: {span.error}", 0.0)
    with start_trace(source="dev") as trace:  # only to add up the cost; not saved
        try:
            verdict = judge_step(span.step, span.input_text, span.output_text)
        except (GatewayError, StepOutputError) as exc:
            return StepScore(span, None, f"could not judge: {exc}", trace.total_cost_usd)
    return StepScore(span, verdict.score, verdict.problem, trace.total_cost_usd)


def score_trace(trace_id: str) -> list[StepScore]:
    with psycopg.connect(get_settings().database_url) as conn:
        spans = trace_spans(conn, trace_id)
    return [score_span(s) for s in spans]


def find_trace(conn: psycopg.Connection[Any], ref: str) -> str:
    """A ticket ID (latest trace for it) or a trace ID / prefix."""
    row = conn.execute(
        "SELECT trace_id::text FROM traces WHERE ticket_id = %s OR trace_id::text LIKE %s "
        "ORDER BY created_at DESC LIMIT 1",
        (ref, ref + "%"),
    ).fetchone()
    if row is None:
        raise SystemExit(f"no trace found for {ref!r}")
    return str(row[0])


def print_scores(label: str, scores: list[StepScore], expected_step: str | None = None) -> None:
    cost = sum(s.cost_usd for s in scores)
    print(f"\n{label}   (judge cost ${cost:.4f})")
    for s in scores:
        mark = "  <- planted root cause" if s.span.step == expected_step else ""
        score = "fail" if s.score is None else str(s.score)
        print(f"  {s.span.step:<15}{score:>4}   conf {s.span.confidence}   {s.problem or ''}{mark}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ref", nargs="?", help="ticket ID (e.g. FX-01, GT-030) or trace ID prefix")
    parser.add_argument("--planted", action="store_true", help="score all 10 planted failures")
    args = parser.parse_args()

    with psycopg.connect(get_settings().database_url) as conn:
        if args.planted:
            rows = conn.execute(
                "SELECT ticket_id, trace_id::text FROM traces WHERE pipeline_version = %s ORDER BY ticket_id",
                (PIPELINE_VERSION,),
            ).fetchall()
            targets = [(str(r[0]), str(r[1])) for r in rows]
        elif args.ref:
            targets = [(args.ref, find_trace(conn, args.ref))]
        else:
            parser.error("give a ticket/trace ID, or --planted")

    expected = {c["case_id"]: c["root_cause_step"] for c in load_cases()} if args.planted else {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda t: score_trace(t[1]), targets))
    for (label, _), scores in zip(targets, results, strict=True):
        print_scores(label, scores, expected.get(label))


if __name__ == "__main__":
    main()
