"""Review proposed diagnoses: a person decides what each one really is.

  confirm       the pipeline really got it wrong. Needs the corrected answer; becomes an eval case later.
  override      the diagnosis (or the verifier) was wrong; nothing was broken.
  label-review  the trace failed its eval but no step was at fault: the expected answer needs checking.

python -m sentinel.feedback.review list                      # proposed diagnoses
python -m sentinel.feedback.review show 3f2a                 # details, and writes a draft answer to edit
python -m sentinel.feedback.review confirm 3f2a --by bilal   # reads reports/review/<id>.json
python -m sentinel.feedback.review override 3f2a --by bilal --note "Asana is a competitor, Haiku was right"
python -m sentinel.feedback.review label-review 3f2a --by bilal --note "label keeps the typo"
"""

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg

from sentinel.forensics.analyzer import CATEGORIES, output_json
from sentinel.settings import get_settings

DRAFTS = Path("reports") / "review"
ENTITY_FIELDS = ("names", "emails", "products", "reference_ids", "amounts", "dates")
PROMPT_PREFIX = "Ticket:\n"


def find_diagnosis(conn: psycopg.Connection[Any], prefix: str) -> str:
    rows = conn.execute(
        "SELECT diagnosis_id::text FROM diagnoses WHERE diagnosis_id::text LIKE %s", (prefix + "%",)
    ).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"{len(rows)} diagnoses start with {prefix!r}; give more characters")
    return str(rows[0][0])


def final_outputs(conn: psycopg.Connection[Any], trace_id: str) -> dict[str, dict[str, Any]]:
    """Each step's final output, and the ticket as the pipeline received it (intake's input)."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (step) step, input_json->>'prompt', raw_response
        FROM spans WHERE trace_id = %s ORDER BY step, step_order DESC
        """,
        (trace_id,),
    ).fetchall()
    outputs = {str(step): output_json(raw) for step, _, raw in rows}
    intake_input = next((p for step, p, _ in rows if step == "intake"), "") or ""
    outputs["ticket"] = {"text": intake_input.removeprefix(PROMPT_PREFIX)}
    return outputs


def draft_answer(outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What the pipeline said, in eval-case shape. The reviewer corrects the wrong parts."""
    extraction = outputs.get("extraction", {})
    summary = outputs.get("summary", {})
    return {
        "category": outputs.get("classification", {}).get("category", ""),
        "entities": {f: extraction.get(f, []) for f in ENTITY_FIELDS},
        "summary": summary.get("summary", ""),
        "next_action": summary.get("next_action", ""),
    }


def check_answer(answer: dict[str, Any], ticket_text: str) -> list[str]:
    """The same rules as the golden set: a valid category, entities copied word for word, no blanks."""
    problems = []
    if answer.get("category") not in CATEGORIES:
        problems.append(f"category {answer.get('category')!r} is not one of {sorted(CATEGORIES)}")
    entities = answer.get("entities", {})
    if set(entities) != set(ENTITY_FIELDS):
        problems.append(f"entities must have exactly these keys: {', '.join(ENTITY_FIELDS)}")
    for field, values in entities.items():
        for value in values:
            if value not in ticket_text:
                problems.append(f"{field}: {value!r} does not appear word for word in the ticket")
    for key in ("summary", "next_action"):
        if not str(answer.get(key, "")).strip():
            problems.append(f"{key} is empty")
    return problems


def list_diagnoses(conn: psycopg.Connection[Any], status: str) -> None:
    rows = conn.execute(
        """
        SELECT d.diagnosis_id::text, t.ticket_id, s.step, d.failure_type, d.type_source, d.created_at
        FROM diagnoses d JOIN traces t USING (trace_id) LEFT JOIN spans s ON s.span_id = d.root_cause_span_id
        WHERE d.status = %s ORDER BY d.created_at
        """,
        (status,),
    ).fetchall()
    print(f"{len(rows)} {status} diagnoses\n")
    print(f"{'id':<10}{'ticket':<10}{'root cause':<16}{'failure type':<26}decided by")
    for did, ticket, step, ftype, source, _ in rows:
        print(f"{did[:8]:<10}{ticket or '-':<10}{step or '-':<16}{ftype:<26}{source}")


def show(conn: psycopg.Connection[Any], diagnosis_id: str) -> Path:
    row = conn.execute(
        """
        SELECT d.trace_id::text, t.ticket_id, d.failure_type, d.type_source, d.evidence_text, d.step_scores,
               d.flag_note, d.status
        FROM diagnoses d JOIN traces t USING (trace_id) WHERE d.diagnosis_id = %s
        """,
        (diagnosis_id,),
    ).fetchone()
    assert row is not None
    trace_id, ticket_id, ftype, source, evidence, scores, note, status = row
    outputs = final_outputs(conn, trace_id)
    print(f"diagnosis {diagnosis_id}  ({status})\nticket {ticket_id}  trace {trace_id}\n")
    print(f"TICKET\n{outputs['ticket']['text']}\n")
    print(
        "STEP SCORES  "
        + "  ".join(f"{s['step']} {s['score'] if s['score'] is not None else 'x'}" for s in scores)
    )
    print(f"\nDIAGNOSIS  {ftype}  ({source})\n{evidence}")
    if note:
        print(f"\nFLAG NOTE  {note}")
    DRAFTS.mkdir(parents=True, exist_ok=True)
    path = DRAFTS / f"{diagnosis_id}.json"
    if not path.exists():  # never overwrite a draft someone is editing
        path.write_text(
            json.dumps(draft_answer(outputs), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(f"\nDraft answer (what the pipeline said): {path}\nEdit it to the correct answer, then confirm.")
    return path


def set_status(
    conn: psycopg.Connection[Any],
    diagnosis_id: str,
    status: str,
    by: str,
    note: str | None,
    corrected: dict[str, Any] | None = None,
) -> None:
    from psycopg.types.json import Jsonb

    current = conn.execute("SELECT status FROM diagnoses WHERE diagnosis_id = %s", (diagnosis_id,)).fetchone()
    if current is None or current[0] != "proposed":
        raise SystemExit(
            f"diagnosis is {current[0] if current else 'missing'}; only proposed ones can be reviewed"
        )
    conn.execute(
        """
        UPDATE diagnoses SET status = %s, reviewed_by = %s, reviewed_at = now(), review_note = %s,
                             corrected_output = %s
        WHERE diagnosis_id = %s
        """,
        (status, by, note, Jsonb(corrected) if corrected is not None else None, diagnosis_id),
    )


def confirm(conn: psycopg.Connection[Any], diagnosis_id: str, by: str, note: str | None) -> None:
    path = DRAFTS / f"{diagnosis_id}.json"
    if not path.exists():
        raise SystemExit(f"no draft answer at {path}: run 'show' first, then edit it")
    trace_id = conn.execute(
        "SELECT trace_id::text FROM diagnoses WHERE diagnosis_id = %s", (diagnosis_id,)
    ).fetchone()
    assert trace_id is not None
    outputs = final_outputs(conn, trace_id[0])
    ticket = outputs["ticket"]["text"]
    answer = json.loads(path.read_text(encoding="utf-8"))
    if answer == draft_answer(outputs):
        raise SystemExit(
            "The draft is unchanged: this is what the pipeline said. Correct it first, or use "
            "'override' if the pipeline was actually right."
        )
    problems = check_answer(answer, ticket)
    if problems:
        raise SystemExit("The corrected answer has problems:\n- " + "\n- ".join(problems))
    set_status(conn, diagnosis_id, "confirmed", by, note, answer)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument(
        "--status", default="proposed", choices=["proposed", "confirmed", "overridden", "label_review"]
    )
    sub.add_parser("show").add_argument("id")
    for name in ("confirm", "override", "label-review"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--by", required=True, help="who reviewed it")
        p.add_argument("--note", required=name != "confirm", help="why")
    args = parser.parse_args()

    with psycopg.connect(get_settings().database_url) as conn:
        if args.command == "list":
            list_diagnoses(conn, args.status)
            return
        diagnosis_id = find_diagnosis(conn, args.id)
        if args.command == "show":
            show(conn, diagnosis_id)
        elif args.command == "confirm":
            confirm(conn, diagnosis_id, args.by, args.note)
            print(f"confirmed {diagnosis_id[:8]}: it will become an eval case in the next dataset build")
        elif args.command == "override":
            set_status(conn, diagnosis_id, "overridden", args.by, args.note)
            print(f"overridden {diagnosis_id[:8]}: no eval case will be made")
        else:
            set_status(conn, diagnosis_id, "label_review", args.by, args.note)
            print(f"{diagnosis_id[:8]} sent to label review: check the expected answer for this ticket")


if __name__ == "__main__":
    main()
