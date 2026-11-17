"""Known issues: every flagged case and every open label review, with its pass/fail history.

Built from eval runs already in the database, so it costs nothing to run.

python -m sentinel.feedback.issues            # the last 8 runs that included each case
python -m sentinel.feedback.issues --runs 20
"""

import argparse
from itertools import pairwise
from typing import Any

import psycopg

from sentinel.settings import get_settings


def status_of(history: list[bool]) -> str:
    """history is oldest first. 'regressed' is the one to act on: it passed, then broke again."""
    if not history:
        return "not run yet"
    if history[-1]:
        return "fixed" if not all(history) else "passing"
    return "regressed" if any(history) else "failing"


def histories(
    conn: psycopg.Connection[Any], case_ids: list[str], runs: int
) -> dict[str, list[tuple[str, bool]]]:
    """For each case, (run label, passed) for the last `runs` eval runs that included it, oldest first."""
    rows = conn.execute(
        """
        SELECT case_id, label, passed FROM (
            SELECT r.case_id, coalesce(e.label, left(e.run_id::text, 8)) AS label, r.passed, e.created_at,
                   row_number() OVER (PARTITION BY r.case_id ORDER BY e.created_at DESC) AS n
            FROM eval_results r JOIN eval_runs e USING (run_id)
            WHERE r.case_id = ANY(%s)
        ) recent
        WHERE n <= %s ORDER BY case_id, created_at
        """,
        (case_ids, runs),
    ).fetchall()
    out: dict[str, list[tuple[str, bool]]] = {c: [] for c in case_ids}
    for case_id, label, passed in rows:
        out[case_id].append((label, passed))
    return out


def flagged_cases(conn: psycopg.Connection[Any]) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT case_id, source, coalesce(failure_type, '-') FROM eval_cases "
        "WHERE source IN ('flag', 'routing_failure') ORDER BY case_id"
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def label_reviews(conn: psycopg.Connection[Any]) -> list[tuple[str, str, str]]:
    """Open label reviews, as (case_id, diagnosis id, note)."""
    rows = conn.execute(
        """
        SELECT t.ticket_id, d.diagnosis_id::text, coalesce(d.review_note, '')
        FROM diagnoses d JOIN traces t USING (trace_id)
        WHERE d.status = 'label_review' ORDER BY t.ticket_id
        """
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def marks(history: list[tuple[str, bool]]) -> str:
    return "".join("✓" if passed else "✗" for _, passed in history) or "-"


def first_fixed(history: list[tuple[str, bool]]) -> str:
    """The run where a case started passing after failing (or '-')."""
    for (_, before), (label, now) in pairwise(history):
        if not before and now:
            return label
    return "-"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--runs", type=int, default=8, help="how many recent runs to show per case")
    args = parser.parse_args()

    with psycopg.connect(get_settings().database_url) as conn:
        flagged = flagged_cases(conn)
        reviews = label_reviews(conn)
        hist = histories(conn, [c for c, _, _ in flagged] + [c for c, _, _ in reviews], args.runs)

    print(f"FLAGGED CASES ({len(flagged)})   history: oldest -> newest run\n")
    print(f"{'case':<9}{'failure type':<26}{'status':<13}{'history':<{args.runs + 3}}fixed in")
    for case_id, _, ftype in flagged:
        h = hist[case_id]
        status = status_of([p for _, p in h])
        print(f"{case_id:<9}{ftype:<26}{status:<13}{marks(h):<{args.runs + 3}}{first_fixed(h)}")

    print(f"\nOPEN LABEL REVIEWS ({len(reviews)})\n")
    for case_id, diagnosis_id, note in reviews:
        h = hist.get(case_id, [])
        print(
            f"{case_id:<9}{status_of([p for _, p in h]):<13}{marks(h):<{args.runs + 3}}"
            f"diagnosis {diagnosis_id[:8]}: {note}"
        )

    regressed = [c for c, _, _ in flagged if status_of([p for _, p in hist[c]]) == "regressed"]
    if regressed:
        print(f"\nREGRESSED: {', '.join(regressed)}. These passed before and fail now: look at them first.")


if __name__ == "__main__":
    main()
