"""The backward analyzer: for a failed trace, find the step that caused it, its failure type and evidence.

1. Every step is judged against its own input (faithfulness.py).
2. Root cause: the EARLIEST step that scored 2 or lower, or that failed outright. Later steps
   usually inherit an earlier mistake, so the first bad step is the cause, not the worst-looking one.
3. Failure type: rules first (they are certain), then the judge picks from the taxonomy.
4. Evidence: the root cause's problem, then what each later low-scoring step did with it.

python -m sentinel.forensics.analyzer FX-08          # one trace (ticket ID or trace ID prefix)
python -m sentinel.forensics.analyzer --planted      # all 10 planted failures, scored against the answers
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from pydantic import BaseModel

from sentinel.evals.judge import judge_model, load_rubric
from sentinel.forensics.faithfulness import StepScore, find_trace, score_trace
from sentinel.forensics.planted import PIPELINE_VERSION, load_cases
from sentinel.gateway.client import GatewayError, send_request
from sentinel.pipeline.models import StepOutputError, parse_output
from sentinel.settings import get_settings
from sentinel.tracing.core import start_trace

FailureType = Literal[
    "extraction_hallucination",
    "misclassification",
    "propagation_error",
    "prompt_failure",
    "context_loss",
    "under_routed",
]
JudgedType = Literal[
    "extraction_hallucination", "misclassification", "propagation_error", "prompt_failure", "context_loss"
]
ROOT_CAUSE_MAX_SCORE = 2
CATEGORIES = {"billing", "technical", "account", "general"}
TYPE_RUBRIC = "failure_type_v1"
TYPE_SYSTEM = "You label failures in a support-ticket pipeline. Reply with JSON only."
DEFAULT_TYPE: dict[str, JudgedType] = {
    "intake": "context_loss",
    "extraction": "extraction_hallucination",
    "classification": "misclassification",
    "summary": "context_loss",
}


class TypeVerdict(BaseModel):
    failure_type: JudgedType
    why: str = ""


@dataclass
class Diagnosis:
    trace_id: str
    scores: list[StepScore]
    root_cause: StepScore | None = None
    failure_type: FailureType | None = None
    type_source: str = ""  # "rule: ..." or "judge" or "default"
    evidence: str = ""
    cost_usd: float = field(default=0.0)


def find_root_cause(scores: list[StepScore]) -> StepScore | None:
    """The earliest step that failed outright or scored ROOT_CAUSE_MAX_SCORE or lower."""
    for s in scores:
        if s.span.error or (s.score is not None and s.score <= ROOT_CAUSE_MAX_SCORE):
            return s
    return None


def output_json(text: str | None) -> dict[str, Any]:
    """Read a reply's JSON without validating it, so an invalid value can still be inspected."""
    if not text or "{" not in text:
        return {}
    try:
        data = json.loads(text[text.find("{") : text.rfind("}") + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def rule_type(conn: psycopg.Connection[Any], root: StepScore) -> tuple[FailureType, str] | None:
    """Failure types that can be decided for certain, without asking a model."""
    span = root.span
    if span.complexity_tier == 1:
        row = conn.execute(
            "SELECT reference_model FROM verifications WHERE span_id = %s AND routing_failure LIMIT 1",
            (span.span_id,),
        ).fetchone()
        if row:
            return "under_routed", f"rule: tier 1 answer, and the verifier ({row[0]}) disagreed"
    if span.step == "classification":
        category = output_json(span.output_text).get("category")
        if category is not None and category not in CATEGORIES:
            return "prompt_failure", f"rule: {category!r} is not an allowed category"
    if span.error:
        return "prompt_failure", "rule: the step's reply could not be used"
    return None


def judged_type(root: StepScore) -> tuple[JudgedType, str, float]:
    """Ask the judge which taxonomy entry fits. Falls back to the usual type for that step."""
    span = root.span
    prompt = load_rubric(TYPE_RUBRIC).fill(
        step=span.step, input=span.input_text, output=span.output_text or "", problem=root.problem or ""
    )
    with start_trace(source="dev") as trace:
        try:
            resp = send_request(
                prompt, judge_model(), system=TYPE_SYSTEM, max_tokens=500, step="forensics_type"
            )
            verdict = parse_output(resp.text, TypeVerdict)
        except (GatewayError, StepOutputError):
            return DEFAULT_TYPE[span.step], "default", trace.total_cost_usd
    return verdict.failure_type, "judge", trace.total_cost_usd


def evidence_chain(root: StepScore, scores: list[StepScore]) -> str:
    """The root cause's problem, then what each later low-scoring step did with it."""
    parts = [
        f"{root.span.step.capitalize()} (score {root.score if root.score is not None else 'failed'}): "
        f"{root.problem}"
    ]
    after = scores[scores.index(root) + 1 :]
    for s in after:
        if s.score is not None and s.score <= ROOT_CAUSE_MAX_SCORE:
            parts.append(f"Then {s.span.step} (score {s.score}): {s.problem}")
    return "\n".join(parts)


def analyze_trace(trace_id: str) -> Diagnosis:
    scores = score_trace(trace_id)
    diag = Diagnosis(trace_id=trace_id, scores=scores, cost_usd=sum(s.cost_usd for s in scores))
    root = find_root_cause(scores)
    if root is None:
        diag.evidence = (
            f"No step scored {ROOT_CAUSE_MAX_SCORE} or lower: every step was faithful to its input."
        )
        return diag
    diag.root_cause = root
    with psycopg.connect(get_settings().database_url) as conn:
        ruled = rule_type(conn, root)
    if ruled:
        diag.failure_type, diag.type_source = ruled
    else:
        judged, source, cost = judged_type(root)
        diag.failure_type, diag.type_source, diag.cost_usd = judged, source, diag.cost_usd + cost
    diag.evidence = evidence_chain(root, scores)
    return diag


def print_diagnosis(label: str, d: Diagnosis) -> None:
    step = d.root_cause.span.step if d.root_cause else "-"
    print(
        f"\n{label}: root cause {step}, {d.failure_type or 'none'}  ({d.type_source or 'no failure found'})"
    )
    print(
        "  scores: " + "  ".join(f"{s.span.step} {s.score if s.score is not None else 'x'}" for s in d.scores)
    )
    for line in d.evidence.splitlines():
        print(f"  {line}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ref", nargs="?", help="ticket ID (e.g. FX-08, GT-030) or trace ID prefix")
    parser.add_argument(
        "--planted", action="store_true", help="diagnose all 10 planted failures and score them"
    )
    parser.add_argument("--save", action="store_true", help="store the diagnoses as 'proposed'")
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

    with ThreadPoolExecutor(max_workers=4) as pool:
        diagnoses = list(pool.map(lambda t: analyze_trace(t[1]), targets))
    for (label, _), d in zip(targets, diagnoses, strict=True):
        print_diagnosis(label, d)
    if args.save:
        from sentinel.forensics.store import save_diagnosis  # here, not at the top: store imports this module

        for d in diagnoses:
            save_diagnosis(d)
        print(f"\nsaved {len(diagnoses)} proposed diagnoses")

    if args.planted:
        answers = {c["case_id"]: c for c in load_cases()}
        right_step = right_type = 0
        print(f"\n{'case':<7}{'expected':<38}{'diagnosed':<38}")
        for (label, _), d in zip(targets, diagnoses, strict=True):
            want = answers[label]
            got_step = d.root_cause.span.step if d.root_cause else "-"
            ok_step, ok_type = got_step == want["root_cause_step"], d.failure_type == want["failure_type"]
            right_step += ok_step
            right_type += ok_type
            print(
                f"{label:<7}{want['root_cause_step'] + ' / ' + want['failure_type']:<38}"
                f"{got_step + ' / ' + str(d.failure_type):<38}{'step ✓' if ok_step else 'step ✗'}  "
                f"{'type ✓' if ok_type else 'type ✗'}"
            )
        print(
            f"\nroot cause step right: {right_step}/{len(targets)}   "
            f"failure type right: {right_type}/{len(targets)}   "
            f"cost ${sum(d.cost_usd for d in diagnoses):.4f}"
        )


if __name__ == "__main__":
    main()
