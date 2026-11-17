"""Load the planted failures into Postgres as ordinary traces, so forensics can be tested on them.

Each case in data/forensics_planted_failures.json is a pretend pipeline run with one deliberate
mistake, plus the right answer (the step that caused it, and the failure type). No model is called.

python -m sentinel.forensics.planted    # (re)load all 10; prints each trace ID
"""

import json
import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sentinel.pipeline.models import Entities
from sentinel.pipeline.steps import summary_content
from sentinel.settings import get_settings
from sentinel.tracing.core import Span, Trace
from sentinel.tracing.store import save_trace_db

PLANTED = Path("data") / "forensics_planted_failures.json"
PIPELINE_VERSION = "planted"  # marks these traces, so a reload can find and replace them
PROMPT_PREFIX = "Ticket:\n"


def load_cases(path: Path = PLANTED) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return cases


def step_inputs(case: dict[str, Any]) -> dict[str, str]:
    """What each step would have been sent, built from the earlier steps' outputs as the pipeline does."""
    out = {s["step"]: s["output"] for s in case["spans"]}
    clean = out["intake"]["clean_text"]
    entities = Entities(**{k: v for k, v in out["extraction"].items() if k != "confidence"})
    summary = summary_content(clean, entities, out["classification"]["category"], out["intake"]["language"])
    return {
        "intake": PROMPT_PREFIX + case["ticket_text"],
        "extraction": PROMPT_PREFIX + clean,
        "classification": PROMPT_PREFIX + clean,
        "summary": PROMPT_PREFIX + summary,
    }


def to_trace(case: dict[str, Any]) -> Trace:
    trace = Trace(source="ci", ticket_id=case["case_id"], pipeline_version=PIPELINE_VERSION)
    prompts = step_inputs(case)
    for order, s in enumerate(case["spans"], 1):
        trace.spans.append(
            Span(
                trace_id=trace.trace_id,
                step=s["step"],
                step_order=order,
                input={"prompt": prompts[s["step"]]},
                output=json.dumps(s["output"]),  # real LLM spans store the raw reply text
                model_id=s["model_id"],
                confidence=s.get("confidence"),
                complexity_tier=s.get("complexity_tier"),
            )
        )
    return trace


def save_verification(conn: psycopg.Connection[Any], trace: Trace, case: dict[str, Any]) -> None:
    """FX-10 comes with a verifier result. Attach it to the step it checked (classification)."""
    v = case["verification"]
    span = next(s for s in trace.spans if s.step == case["root_cause_step"])
    conn.execute(
        """
        INSERT INTO verifications (verification_id, span_id, reference_model, reference_output,
                                   agreement_score, routing_failure, escalated, cost_delta_usd, detail)
        VALUES (%s, %s, %s, %s, %s, %s, false, 0, %s)
        """,
        (
            str(uuid.uuid4()),
            span.span_id,
            v["reference_model"],
            Jsonb(v["reference_output"]),
            v["agreement_score"],
            v["routing_failure"],
            "planted",
        ),
    )


def load_into_db(cases: list[dict[str, Any]]) -> dict[str, str]:
    """Replace any earlier copies, save every case as a trace. Returns case_id -> trace_id."""
    url = get_settings().database_url
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM traces WHERE pipeline_version = %s", (PIPELINE_VERSION,))
    loaded = {}
    for case in cases:
        trace = to_trace(case)
        save_trace_db(trace)
        if "verification" in case:
            with psycopg.connect(url) as conn:
                save_verification(conn, trace, case)
        loaded[case["case_id"]] = trace.trace_id
    return loaded


def main() -> None:
    cases = load_cases()
    loaded = load_into_db(cases)
    print(f"{'case':<7}{'failure type':<26}{'root cause':<16}trace_id")
    for case in cases:
        print(
            f"{case['case_id']:<7}{case['failure_type']:<26}{case['root_cause_step']:<16}{loaded[case['case_id']]}"
        )


if __name__ == "__main__":
    main()
