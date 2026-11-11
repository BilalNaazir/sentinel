import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from pydantic import BaseModel, Field


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Span(BaseModel):
    """One step inside a trace: what went in, what came out, how long, how much."""

    span_id: str = Field(default_factory=_new_id)
    trace_id: str
    step: str
    step_order: int
    started_at: datetime = Field(default_factory=_now)
    latency_ms: int = 0
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: str | None = None
    # filled in when the step is an LLM call
    model_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    confidence: int | None = None


class Trace(BaseModel):
    """Everything that happened while handling one request."""

    trace_id: str = Field(default_factory=_new_id)
    source: str = "dev"
    created_at: datetime = Field(default_factory=_now)
    spans: list[Span] = Field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.spans), 8)

    @property
    def total_latency_ms(self) -> int:
        return sum(s.latency_ms for s in self.spans)

    @property
    def status(self) -> str:
        return "failed" if any(s.error for s in self.spans) else "success"


# The trace being recorded right now, if any.
_current_trace: ContextVar[Trace | None] = ContextVar("current_trace", default=None)


def current_trace() -> Trace | None:
    return _current_trace.get()


def last_span() -> Span | None:
    """The most recent span on the current trace, if a trace is running."""
    trace = _current_trace.get()
    return trace.spans[-1] if trace and trace.spans else None


@contextmanager
def start_trace(source: str = "dev") -> Iterator[Trace]:
    """Record every span that happens inside this `with` block into one trace."""
    trace = Trace(source=source)
    token = _current_trace.set(trace)
    try:
        yield trace
    finally:
        _current_trace.reset(token)


@contextmanager
def span(step: str, **inputs: Any) -> Iterator[Span | None]:
    """Time a step and record it on the current trace. Does nothing if no trace is running."""
    trace = _current_trace.get()
    if trace is None:
        yield None
        return
    s = Span(trace_id=trace.trace_id, step=step, step_order=len(trace.spans), input=inputs)
    trace.spans.append(s)
    start = time.perf_counter()
    try:
        yield s
    except Exception as exc:
        s.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        s.latency_ms = int((time.perf_counter() - start) * 1000)


P = ParamSpec("P")
R = TypeVar("R")


def traced(step: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: record every call of the function as a span named `step`."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with span(
                step, args=[repr(a) for a in args], kwargs={k: repr(v) for k, v in kwargs.items()}
            ) as s:
                result = func(*args, **kwargs)
                if s is not None:
                    s.output = repr(result)
                return result

        return wrapper

    return decorator
