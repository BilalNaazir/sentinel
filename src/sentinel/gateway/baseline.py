"""Baseline run: send the same 10 tickets to every connected model and record real speed and cost.

python -m sentinel.gateway.baseline            # run, print a table, update models.yaml
python -m sentinel.gateway.baseline --no-write # run and print only
"""

import argparse
import json
import re
import statistics
from pathlib import Path

from pydantic import BaseModel

from sentinel.gateway.client import PROVIDERS, GatewayError, send_request
from sentinel.gateway.registry import DEFAULT_PATH, ModelConfig, load_registry
from sentinel.settings import get_settings
from sentinel.tracing.core import start_trace
from sentinel.tracing.store import save_trace

GOLDEN = Path("data") / "eval_dataset.v2.json"
SYSTEM = "Summarise this customer support ticket in one sentence for the agent who will handle it."


class ModelResult(BaseModel):
    model_id: str
    calls: int
    failed: str | None = None
    median_latency_ms: int | None = None
    avg_tokens_in: float = 0
    avg_tokens_out: float = 0
    total_cost_usd: float = 0


def load_tickets(n: int = 10) -> list[str]:
    """Every 10th golden ticket: a spread of categories, lengths and languages."""
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    return [c["input"]["ticket_text"] for c in cases[::10][:n]]


def connected_models() -> list[ModelConfig]:
    """Models whose provider is wired up in the gateway and has an API key in .env."""
    s = get_settings()
    keys = {"anthropic": s.anthropic_api_key, "openai": s.openai_api_key}
    return [m for m in load_registry().models.values() if m.provider in PROVIDERS and keys.get(m.provider)]


def run_model(model: ModelConfig, tickets: list[str]) -> ModelResult:
    latencies: list[int] = []
    tokens_in: list[int] = []
    tokens_out: list[int] = []
    cost = 0.0
    for ticket in tickets:
        try:
            with start_trace(source="dev") as trace:
                resp = send_request(ticket, model.id, system=SYSTEM, max_tokens=300, step="baseline")
            save_trace(trace)
        except GatewayError as exc:
            # Stop this model at the first failure: a missing credit or a bad key won't fix itself.
            return ModelResult(model_id=model.id, calls=len(latencies), failed=exc.reason)
        latencies.append(resp.latency_ms)
        tokens_in.append(resp.tokens_in)
        tokens_out.append(resp.tokens_out)
        cost += resp.cost_usd
    return ModelResult(
        model_id=model.id,
        calls=len(latencies),
        median_latency_ms=int(statistics.median(latencies)),
        avg_tokens_in=statistics.mean(tokens_in),
        avg_tokens_out=statistics.mean(tokens_out),
        total_cost_usd=round(cost, 8),
    )


def update_latencies(path: Path, latencies: dict[str, int]) -> None:
    """Set avg_latency_ms for the given models in models.yaml, keeping every comment in the file."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    current_id = None
    for i, line in enumerate(lines):
        id_match = re.match(r"\s*- id:\s*(\S+)", line)
        if id_match:
            current_id = id_match.group(1)
        elif current_id in latencies and re.match(r"\s*avg_latency_ms:", line):
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f"{indent}avg_latency_ms: {latencies[current_id]}\n"
    path.write_text("".join(lines), encoding="utf-8")


def print_results(results: list[ModelResult]) -> None:
    print(f"{'model':<20}{'calls':>6}{'median ms':>11}{'avg in':>8}{'avg out':>9}{'total $':>12}")
    for r in results:
        if r.failed:
            print(f"{r.model_id:<20}{r.calls:>6}   FAILED: {r.failed[:70]}")
        else:
            print(
                f"{r.model_id:<20}{r.calls:>6}{r.median_latency_ms:>11}{r.avg_tokens_in:>8.0f}"
                f"{r.avg_tokens_out:>9.0f}{r.total_cost_usd:>12.6f}"
            )
    print(f"{'total':<54}{sum(r.total_cost_usd for r in results):>12.6f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-write", action="store_true", help="don't update models.yaml")
    args = parser.parse_args()

    tickets = load_tickets()
    models = connected_models()
    print(f"{len(tickets)} tickets x {len(models)} models: {', '.join(m.id for m in models)}\n")
    results = [run_model(m, tickets) for m in models]
    print_results(results)

    measured = {r.model_id: r.median_latency_ms for r in results if r.median_latency_ms is not None}
    if measured and not args.no_write:
        update_latencies(DEFAULT_PATH, measured)
        print(f"\nupdated avg_latency_ms for {len(measured)} models in {DEFAULT_PATH}")


if __name__ == "__main__":
    main()
