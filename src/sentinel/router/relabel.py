"""Re-label the router's training data from evidence instead of rules.

A request is tier 1 if the tier 1 model's answer agrees with the tier 3 model's answer.
If not, it is tier 2 if the tier 2 model agrees, and tier 3 otherwise. Models come from routing.yaml.

python -m sentinel.router.relabel --limit 5    # try a few tickets first
python -m sentinel.router.relabel              # all of them; safe to stop and re-run, it resumes
python -m sentinel.router.relabel --tickets data/hard_tickets.jsonl   # every step of every ticket in a file
"""

import argparse
import csv
import difflib
import json
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sentinel.evals.entities import case_f1, score_case
from sentinel.evals.judge import judge_summary
from sentinel.evals.run import PassCriteria, load_criteria
from sentinel.gateway.client import GatewayError
from sentinel.gateway.routing import TierNumber, get_routing
from sentinel.pipeline import steps
from sentinel.pipeline.models import Entities, StepOutputError
from sentinel.tracing.core import start_trace

LABELS_IN = Path("data") / "complexity_labels.csv"
LABELS_OUT = Path("data") / "complexity_labels.v2.csv"
TICKETS = Path("data") / "load_test_tickets.jsonl"
CACHE = Path("reports") / "relabel_cache.jsonl"

STEPS_TO_LABEL = ("intake", "extraction", "classification", "summary")
MIN_INTAKE_SIMILARITY = 0.9
ENTITY_FIELDS = ("names", "emails", "products", "reference_ids", "amounts", "dates")


@dataclass
class Upstream:
    """The inputs a step needs, produced once per ticket by the reference model."""

    ticket_text: str
    clean_text: str = ""
    language: str = ""
    entities: Entities | None = None
    category: str = ""


@dataclass
class Verdict:
    agrees: bool
    detail: str


def run_at(step: str, model_id: str, up: Upstream) -> BaseModel:
    """Run one pipeline step with one model, on the inputs in `up`."""
    if step == "intake":
        return steps.intake(up.ticket_text, model_id)
    if step == "extraction":
        return steps.extract(up.clean_text, model_id)
    if step == "classification":
        return steps.classify(up.clean_text, model_id)
    if step == "summary":
        assert up.entities is not None
        return steps.summarise(up.clean_text, up.entities, up.category, up.language, model_id)
    raise ValueError(f"cannot label step {step!r}")


def compare(step: str, up: Upstream, candidate: Any, reference: Any, criteria: PassCriteria) -> Verdict:
    """Does the candidate's answer agree with the reference model's answer? Same tests as the eval."""
    if step == "intake":
        ratio = difflib.SequenceMatcher(None, candidate.clean_text, reference.clean_text).ratio()
        same_lang = candidate.language.casefold() == reference.language.casefold()
        ok = same_lang and ratio >= MIN_INTAKE_SIMILARITY
        return Verdict(ok, f"similarity {ratio:.2f}, language {candidate.language} vs {reference.language}")
    if step == "extraction":
        ref = {f: getattr(reference, f) for f in ENTITY_FIELDS}
        cand = {f: getattr(candidate, f) for f in ENTITY_FIELDS}
        f1 = case_f1(score_case(ref, cand))
        return Verdict(f1 >= criteria.min_entity_f1, f"entity F1 {f1:.2f}")
    if step == "classification":
        ok = candidate.category == reference.category
        return Verdict(ok, f"{candidate.category} vs {reference.category}")
    if step == "summary":
        verdict = judge_summary(up.ticket_text, reference.summary, candidate.summary)
        return Verdict(
            verdict.score >= criteria.min_summary_score, f"judge {verdict.score}: {verdict.reason}"
        )
    raise ValueError(f"cannot compare step {step!r}")


Runner = Callable[[str, str, Upstream], BaseModel]
Comparer = Callable[[str, Upstream, Any, Any, PassCriteria], Verdict]


def label_request(
    step: str,
    up: Upstream,
    criteria: PassCriteria,
    run: Runner = run_at,
    check: Comparer = compare,
) -> dict[str, Any]:
    """Find the lowest tier whose model agrees with the tier 3 model on this request."""
    routing = get_routing()
    models = {t: routing.model_for(step, t) for t in (1, 2, 3)}
    reference = run(step, models[3], up)
    tried: dict[str, str] = {}
    tier: TierNumber
    for tier in (1, 2):
        try:
            candidate = run(step, models[tier], up)
            verdict = check(step, up, candidate, reference, criteria)
        except (GatewayError, StepOutputError) as exc:  # a broken answer is a disagreement
            verdict = Verdict(False, f"failed: {exc}")
        tried[models[tier]] = verdict.detail
        if verdict.agrees:
            return {"tier": tier, "reason": f"{models[tier]} agrees with {models[3]}", "tried": tried}
    return {"tier": 3, "reason": f"only {models[3]} gets it right", "tried": tried}


def build_upstream(ticket_text: str, need: str) -> Upstream:
    """Reference-model inputs for a step: none for intake, clean text for most, everything for summary."""
    up = Upstream(ticket_text=ticket_text)
    if need == "intake":
        return up
    routing = get_routing()
    cleaned = steps.intake(ticket_text, routing.model_for("intake", 3))
    up.clean_text = cleaned.clean_text.strip() or ticket_text  # same fallback as the pipeline
    up.language = cleaned.language
    if need == "summary":
        up.entities = steps.extract(up.clean_text, routing.model_for("extraction", 3))
        up.category = steps.classify(up.clean_text, routing.model_for("classification", 3)).category
    return up


def needs(ticket_steps: list[str]) -> str:
    if "summary" in ticket_steps:
        return "summary"
    return "clean" if any(s != "intake" for s in ticket_steps) else "intake"


def label_ticket(
    ticket_text: str, rows: list[dict[str, str]], criteria: PassCriteria
) -> tuple[list[dict[str, Any]], float]:
    """Label every requested step of one ticket. Returns one result per row, and what it cost."""
    results = []
    with start_trace(source="dev") as trace:
        try:
            up = build_upstream(ticket_text, needs([r["step"] for r in rows]))
        except (GatewayError, StepOutputError) as exc:
            failed = [
                {"request_id": r["request_id"], "tier": None, "reason": f"upstream failed: {exc}"}
                for r in rows
            ]
            return failed, trace.total_cost_usd
        for row in rows:
            try:
                out = label_request(row["step"], up, criteria)
            except (GatewayError, StepOutputError) as exc:  # the reference model itself failed
                out = {"tier": None, "reason": f"reference failed: {exc}"}
            results.append({"request_id": row["request_id"], **out})
    return results, trace.total_cost_usd


def rows_for_tickets(tickets: list[dict[str, str]]) -> list[dict[str, str]]:
    """One request row per pipeline step, for tickets that have no labels file of their own."""
    return [
        {
            "request_id": f"{t['ticket_id']}-{step}",
            "source_ticket_id": t["ticket_id"],
            "step": step,
            "tier": "",
            "label_reason": "",
            "label_source": "",
            "stress_type": t.get("stress_type", ""),
        }
        for t in tickets
        for step in STEPS_TO_LABEL
    ]


def read_cache(path: Path = CACHE) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    return {r["request_id"]: r for r in map(json.loads, lines) if r.get("tier") is not None}


def write_labels(
    rows: list[dict[str, str]], done: dict[str, dict[str, Any]], path: Path = LABELS_OUT
) -> None:
    """The original file with evidence-based tiers. Rows without evidence keep their rule label."""
    fields = [*rows[0].keys(), "rule_tier"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            new = dict(row, rule_tier=row["tier"])
            if row["request_id"] in done:
                d = done[row["request_id"]]
                new.update(tier=str(d["tier"]), label_reason=d["reason"], label_source="evidence")
            writer.writerow(new)


def print_comparison(rows: list[dict[str, str]], done: dict[str, dict[str, Any]]) -> None:
    by_step: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for r in rows:
        if r["request_id"] in done:
            by_step[r["step"]][(r["tier"], str(done[r["request_id"]]["tier"]))] += 1
    print(f"\n{'step':<16}{'rows':>5}   rule tiers 1/2/3   evidence tiers 1/2/3")
    for step in STEPS_TO_LABEL:
        c = by_step[step]
        if not c:
            continue
        rule = [sum(n for (old, _), n in c.items() if old == t) for t in "123"]
        evid = [sum(n for (_, new), n in c.items() if new == t) for t in "123"]
        print(f"{step:<16}{sum(c.values()):>5}   {'/'.join(map(str, rule)):<19}{'/'.join(map(str, evid))}")


def print_by_stress(rows: list[dict[str, str]], done: dict[str, dict[str, Any]]) -> None:
    """For ticket files with a stress_type: which kinds of ticket are actually hard."""
    by_stress: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        if r["request_id"] in done:
            by_stress[r["stress_type"]][str(done[r["request_id"]]["tier"])] += 1
    print(f"\n{'stress type':<24}{'rows':>5}   evidence tiers 1/2/3")
    for stress, c in sorted(by_stress.items()):
        print(f"{stress:<24}{sum(c.values()):>5}   {'/'.join(str(c[t]) for t in '123')}")
    above = [r for r in rows if r["request_id"] in done and done[r["request_id"]]["tier"] != 1]
    if above:
        print("\nabove tier 1:")
        for r in above:
            d = done[r["request_id"]]
            print(f"  {r['request_id']:<28} tier {d['tier']}  {d['tried']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, help="only this many tickets (for a trial run)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tickets", type=Path, help="label every step of every ticket in this JSONL file")
    args = parser.parse_args()

    if args.tickets:  # e.g. data/hard_tickets.jsonl -> data/complexity_labels.hard.csv
        name = args.tickets.stem.removesuffix("_tickets")
        raw = [json.loads(line) for line in args.tickets.read_text(encoding="utf-8").splitlines()]
        rows = rows_for_tickets(raw)
        out_path = LABELS_OUT.with_name(f"complexity_labels.{name}.csv")
        cache_path = CACHE.with_name(f"relabel_cache.{name}.jsonl")
    else:
        with LABELS_IN.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        raw = [json.loads(line) for line in TICKETS.read_text(encoding="utf-8").splitlines()]
        out_path, cache_path = LABELS_OUT, CACHE
    tickets = {t["ticket_id"]: t["ticket_text"] for t in raw}
    criteria = load_criteria()
    done = read_cache(cache_path)

    todo: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        if r["step"] in STEPS_TO_LABEL and r["request_id"] not in done:
            todo[r["source_ticket_id"]].append(r)
    ticket_ids = sorted(todo)[: args.limit] if args.limit else sorted(todo)
    n_rows = sum(len(todo[t]) for t in ticket_ids)
    print(f"{len(done)} requests already labelled; labelling {n_rows} more from {len(ticket_ids)} tickets")

    CACHE.parent.mkdir(exist_ok=True)
    spent = 0.0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(label_ticket, tickets[t], todo[t], criteria): t for t in ticket_ids}
        for i, fut in enumerate(as_completed(futures), 1):
            results, cost = fut.result()
            with cache_path.open("a", encoding="utf-8") as cache:
                for res in results:
                    cache.write(json.dumps(res) + "\n")  # saved as it goes: a stopped run loses nothing
            spent += cost
            tiers = " ".join(f"{r['request_id']}={r['tier'] or 'ERR'}" for r in results)
            print(f"[{i:>3}/{len(ticket_ids)}] {futures[fut]}  {tiers}   ${spent:.4f} so far")

    done = read_cache(cache_path)
    write_labels(rows, done, out_path)
    if args.tickets:
        print_by_stress(rows, done)
    else:
        print_comparison(rows, done)
    print(f"\nspent ${spent:.4f} this run; wrote {out_path} ({len(done)} rows with evidence)")


if __name__ == "__main__":
    main()
