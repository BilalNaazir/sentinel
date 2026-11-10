import pytest

from sentinel.tracing.core import current_trace, start_trace, traced


@traced("clean_text")
def clean(text: str) -> str:
    return text.strip()


@traced("divide")
def divide(a: int, b: int) -> float:
    return a / b


def test_no_trace_means_nothing_is_recorded() -> None:
    assert current_trace() is None
    assert clean("  hi ") == "hi"  # the function still works normally


def test_each_call_becomes_a_span_in_order() -> None:
    with start_trace(source="test") as trace:
        clean("  hello ")
        divide(10, 4)

    assert [s.step for s in trace.spans] == ["clean_text", "divide"]
    assert [s.step_order for s in trace.spans] == [0, 1]
    assert all(s.trace_id == trace.trace_id for s in trace.spans)
    assert trace.spans[1].input == {"args": ["10", "4"], "kwargs": {}}
    assert trace.spans[1].output == "2.5"
    assert current_trace() is None  # the trace ends with the `with` block


def test_a_failing_step_is_recorded_and_still_raises() -> None:
    with start_trace() as trace, pytest.raises(ZeroDivisionError):
        divide(1, 0)
    assert trace.spans[0].error == "ZeroDivisionError: division by zero"
    assert trace.status == "failed"
