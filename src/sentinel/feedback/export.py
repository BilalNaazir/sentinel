"""Turn confirmed diagnoses into eval cases, in data/flagged_cases.json.

That file is to flagged cases what scripts/golden_source.py is to hand-written ones: it is committed,
it only grows, and scripts/build_golden.py merges it into the next dataset version.

python -m sentinel.feedback.export      # adds any confirmed diagnoses not exported yet
"""

import json
from pathlib import Path
from typing import Any

import psycopg

from sentinel.feedback.review import check_answer, final_outputs
from sentinel.settings import get_settings

FLAGGED = Path("data") / "flagged_cases.json"
PREFIX = {"flag": "FL", "routing_failure": "RF"}


def read_flagged(path: Path = FLAGGED) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cases: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return cases


def next_id(cases: list[dict[str, Any]], source: str) -> str:
    prefix = PREFIX[source]
    taken = [int(c["case_id"].split("-")[1]) for c in cases if c["case_id"].startswith(prefix + "-")]
    return f"{prefix}-{max(taken, default=0) + 1:04d}"


def to_case(case_id: str, diagnosis: dict[str, Any], ticket: str, language: str) -> dict[str, Any]:
    source = "routing_failure" if diagnosis["failure_type"] == "under_routed" else "flag"
    return {
        "case_id": case_id,
        "source": source,
        "input": {"ticket_text": ticket, "language": language},
        "expected": diagnosis["corrected_output"],
        "difficulty": "hard",  # the pipeline already got it wrong once
        "tags": [diagnosis["failure_type"], "from_review"],
        "failure_type": diagnosis["failure_type"],
        "origin_trace_id": diagnosis["trace_id"],
        "notes": (
            f"Confirmed by {diagnosis['reviewed_by']} on {diagnosis['reviewed_at']:%Y-%m-%d}. "
            f"Root cause: {diagnosis['root_cause_step']}. {diagnosis['review_note'] or ''}".strip()
        ),
    }


def confirmed_diagnoses(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.diagnosis_id::text, d.trace_id::text, d.failure_type, d.corrected_output, d.reviewed_by,
               d.reviewed_at, d.review_note, s.step
        FROM diagnoses d LEFT JOIN spans s ON s.span_id = d.root_cause_span_id
        WHERE d.status = 'confirmed' ORDER BY d.reviewed_at
        """
    ).fetchall()
    keys = [
        "diagnosis_id",
        "trace_id",
        "failure_type",
        "corrected_output",
        "reviewed_by",
        "reviewed_at",
        "review_note",
        "root_cause_step",
    ]
    return [dict(zip(keys, r, strict=True)) for r in rows]


def export(path: Path = FLAGGED) -> list[str]:
    """Append every confirmed diagnosis whose trace isn't exported yet. Returns the new case IDs."""
    cases = read_flagged(path)
    done = {c["origin_trace_id"] for c in cases}
    added = []
    with psycopg.connect(get_settings().database_url) as conn:
        for d in confirmed_diagnoses(conn):
            if d["trace_id"] in done:
                continue  # one eval case per trace, however many times it was diagnosed
            outputs = final_outputs(conn, d["trace_id"])
            ticket = outputs["ticket"]["text"]
            problems = check_answer(d["corrected_output"], ticket)
            if problems:  # it passed when confirmed; this catches a hand-edited database
                raise SystemExit(f"diagnosis {d['diagnosis_id'][:8]}: " + "; ".join(problems))
            source = "routing_failure" if d["failure_type"] == "under_routed" else "flag"
            case = to_case(next_id(cases, source), d, ticket, outputs.get("intake", {}).get("language", "en"))
            cases.append(case)
            done.add(d["trace_id"])
            added.append(case["case_id"])
    path.write_text(
        json.dumps(
            {"description": "Eval cases made from confirmed diagnoses.", "cases": cases},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return added


def main() -> None:
    added = export()
    total = len(read_flagged())
    if added:
        print(f"added {', '.join(added)} to {FLAGGED} ({total} flagged cases in total)")
        print("next: bump VERSION in scripts/build_golden.py and run it to build the new dataset")
    else:
        print(f"nothing new to export ({total} flagged cases already in {FLAGGED})")


if __name__ == "__main__":
    main()
