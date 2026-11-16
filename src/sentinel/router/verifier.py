"""The verifier: re-do a sample of answered steps with a different, stronger model and compare.

A disagreement is a routing failure: the cheap model's answer was not good enough for that request.
The comparison is the one the relabel script uses, so "disagrees" means the same thing everywhere.

python -m sentinel.router.verifier "routed v2"              # every ticket in that eval run
python -m sentinel.router.verifier "routed v2" --rate 0.1   # a 10% sample, as in production
"""

import argparse
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from sentinel.evals.compare import find_run_id
from sentinel.evals.run import PassCriteria, load_criteria
from sentinel.gateway.client import GatewayError
from sentinel.gateway.routing import get_routing
from sentinel.pipeline import steps
from sentinel.pipeline.models import (
    ClassificationOutput,
    ExtractionOutput,
    IntakeOutput,
    StepOutputError,
    SummaryOutput,
    parse_output,
)
from sentinel.router.relabel import Upstream, Verdict, compare
from sentinel.settings import get_settings
from sentinel.tracing.core import start_trace

OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "intake": IntakeOutput,
    "extraction": ExtractionOutput,
    "classification": ClassificationOutput,
    "summary": SummaryOutput,
}
PROMPT_PREFIX = "Ticket:\n"  # every step sends f"Ticket:\n{content}"


def sampled(trace_id: str, rate: float) -> bool:
    """Same answer every time for the same trace, so a re-run checks the same sample."""
    bucket = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < rate


@dataclass
class UsedStep:
    """The answer a step actually returned: the last span for that step, if it succeeded."""

    span_id: str
    step: str
    content: str
    raw_response: str
    escalated: bool


@dataclass
class StepCheck:
    span_id: str
    step: str
    verdict: Verdict
    reference: dict[str, Any] | None
    escalated: bool
    cost_usd: float


def used_steps(conn: psycopg.Connection[Any], trace_id: str) -> list[UsedStep]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (step) span_id::text, step, input_json->>'prompt', raw_response,
               escalated_from IS NOT NULL
        FROM spans
        WHERE trace_id = %s AND error IS NULL AND raw_response IS NOT NULL AND step = ANY(%s)
        ORDER BY step, step_order DESC
        """,
        (trace_id, list(OUTPUT_MODELS)),
    ).fetchall()
    return [UsedStep(r[0], r[1], (r[2] or "").removeprefix(PROMPT_PREFIX), r[3], r[4]) for r in rows]


def check_step(used: UsedStep, ticket_text: str, criteria: PassCriteria) -> StepCheck:
    """Re-run one step with the verifier model on exactly the same input, and compare."""
    model = OUTPUT_MODELS[used.step]
    candidate = parse_output(used.raw_response, model)
    with start_trace(source="dev") as trace:  # only to add up the cost; not saved
        try:
            reference = steps.run_step(used.step, used.content, model, get_routing().verifier_reference_model)
        except (GatewayError, StepOutputError) as exc:
            return StepCheck(
                used.span_id,
                used.step,
                Verdict(True, f"verifier failed: {exc}", 0.0),
                None,
                used.escalated,
                trace.total_cost_usd,
            )
        verdict = compare(used.step, Upstream(ticket_text=ticket_text), candidate, reference, criteria)
    return StepCheck(
        used.span_id, used.step, verdict, reference.model_dump(), used.escalated, trace.total_cost_usd
    )


def save_checks(conn: psycopg.Connection[Any], checks: list[StepCheck]) -> None:
    reference_model = get_routing().verifier_reference_model
    with conn.transaction():
        for c in checks:
            if c.reference is None:  # the verifier itself failed: nothing to record
                continue
            conn.execute(
                """
                INSERT INTO verifications (verification_id, span_id, reference_model, reference_output,
                                           agreement_score, routing_failure, escalated, cost_delta_usd,
                                           detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    c.span_id,
                    reference_model,
                    Jsonb(c.reference),
                    round(c.verdict.score, 3),
                    not c.verdict.agrees,
                    c.escalated,
                    c.cost_usd,
                    c.verdict.detail,
                ),
            )


def verify_trace(trace_id: str, criteria: PassCriteria) -> list[StepCheck]:
    """Check every step of one trace, skipping steps already verified. Saves what it finds."""
    with psycopg.connect(get_settings().database_url) as conn:
        done = {
            r[0]
            for r in conn.execute(
                "SELECT v.span_id::text FROM verifications v JOIN spans s USING (span_id) "
                "WHERE s.trace_id = %s",
                (trace_id,),
            )
        }
        todo = [u for u in used_steps(conn, trace_id) if u.span_id not in done]
        intake = next((u for u in used_steps(conn, trace_id) if u.step == "intake"), None)
        ticket_text = intake.content if intake else ""
        checks = [check_step(u, ticket_text, criteria) for u in todo]
        save_checks(conn, checks)
    return checks


@dataclass
class TicketOutcome:
    case_id: str
    passed: bool
    flagged_steps: list[str]


def score_verifier(outcomes: list[TicketOutcome]) -> dict[str, int]:
    """Did the verifier flag the tickets the eval says failed? Rows of a 2x2 table."""
    return {
        "caught": sum(1 for o in outcomes if o.flagged_steps and not o.passed),
        "false_alarm": sum(1 for o in outcomes if o.flagged_steps and o.passed),
        "missed": sum(1 for o in outcomes if not o.flagged_steps and not o.passed),
        "quiet": sum(1 for o in outcomes if not o.flagged_steps and o.passed),
    }


def flagged_by_trace(conn: psycopg.Connection[Any], trace_ids: list[str]) -> dict[str, list[str]]:
    rows = conn.execute(
        """
        SELECT s.trace_id::text, s.step, v.detail FROM verifications v JOIN spans s USING (span_id)
        WHERE s.trace_id = ANY(%s::uuid[]) AND v.routing_failure
        """,
        (trace_ids,),
    ).fetchall()
    flagged: dict[str, list[str]] = {t: [] for t in trace_ids}
    for trace_id, step, detail in rows:
        flagged[trace_id].append(f"{step} ({detail})")
    return flagged


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("run", help="eval run label or id prefix, e.g. 'routed v2'")
    parser.add_argument("--rate", type=float, default=1.0, help="share of tickets to verify (default all)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    criteria = load_criteria()
    with psycopg.connect(get_settings().database_url) as conn:
        run_id = find_run_id(conn, args.run)
        rows = conn.execute(
            "SELECT case_id, trace_id::text, passed FROM eval_results "
            "WHERE run_id = %s AND trace_id IS NOT NULL",
            (run_id,),
        ).fetchall()
    chosen = [r for r in rows if sampled(r[1], args.rate)]
    print(f"Verifying {len(chosen)} of {len(rows)} tickets with {get_routing().verifier_reference_model}\n")

    spent = 0.0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(verify_trace, trace_id, criteria): case_id for case_id, trace_id, _ in chosen}
        for i, fut in enumerate(as_completed(futures), 1):
            checks = fut.result()
            spent += sum(c.cost_usd for c in checks)
            flags = [c.step for c in checks if not c.verdict.agrees]
            print(f"[{i:>3}/{len(chosen)}] {futures[fut]}  {'FLAG ' + ', '.join(flags) if flags else 'ok'}")

    with psycopg.connect(get_settings().database_url) as conn:
        flagged = flagged_by_trace(conn, [r[1] for r in chosen])
    outcomes = [TicketOutcome(case_id, passed, flagged[trace_id]) for case_id, trace_id, passed in chosen]
    table = score_verifier(outcomes)
    print(f"\n{'':<22}{'eval FAIL':>10}{'eval PASS':>11}")
    print(f"{'verifier flagged':<22}{table['caught']:>10}{table['false_alarm']:>11}")
    print(f"{'verifier quiet':<22}{table['missed']:>10}{table['quiet']:>11}")
    for o in sorted(outcomes, key=lambda o: o.case_id):
        if o.flagged_steps or not o.passed:
            status = "FAIL" if not o.passed else "pass"
            print(f"  {o.case_id}  eval {status}  flagged: {'; '.join(o.flagged_steps) or '-'}")
    print(f"\nspent ${spent:.4f} this run")


if __name__ == "__main__":
    main()
