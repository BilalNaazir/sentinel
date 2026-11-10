"""Run a tiny 3-step trace on one ticket and print it.

python -m sentinel.tracing.demo
"""

from sentinel.gateway.client import send_request
from sentinel.tracing.core import Trace, start_trace, traced

TICKET = (
    "Hi, I was charged $49.99 twice on October 3 for my Pro plan. "
    "Order BD-48213. Can you refund the duplicate? Thanks, Maria Lopez"
)


@traced("intake")
def clean(text: str) -> str:
    return " ".join(text.split())


def run() -> Trace:
    with start_trace(source="demo") as trace:
        text = clean(TICKET)
        send_request(
            text,
            "claude-haiku-5-5",
            system="Classify this support ticket as billing, technical, account or general. "
            "Reply with the one word only.",
            max_tokens=10,
            step="classification",
        )
        send_request(
            text,
            "claude-sonnet-5-5",
            system="Summarise this support ticket in one sentence.",
            max_tokens=100,
            step="summary",
        )
    return trace


def print_trace(trace: Trace) -> None:
    print(f"trace {trace.trace_id}  status={trace.status}")
    print(f"{'#':<3}{'step':<16}{'model':<20}{'in':>6}{'out':>6}{'cost $':>12}{'ms':>7}  output")
    for s in trace.spans:
        output = str(s.output or s.error or "").replace("\n", " ")[:60]
        print(
            f"{s.step_order:<3}{s.step:<16}{s.model_id or '-':<20}{s.tokens_in:>6}{s.tokens_out:>6}"
            f"{s.cost_usd:>12.6f}{s.latency_ms:>7}  {output}"
        )
    print(f"{'total':<45}{trace.total_cost_usd:>12.6f}{trace.total_latency_ms:>7}")


if __name__ == "__main__":
    print_trace(run())
