import pytest

from sentinel.gateway import client
from sentinel.pipeline import steps
from sentinel.pipeline.models import StepOutputError
from sentinel.tracing.core import start_trace

TICKET = "Hi,\n\nI was charged $49.99 twice. Order BD-48213.\n\n--\nMaria\nSent from my iPhone"


def replies(monkeypatch: pytest.MonkeyPatch, *texts: str) -> list[str]:
    """Make the fake model answer with these texts, in order. Returns the system prompts it received."""
    queue = list(texts)
    systems: list[str] = []

    def fake_call(model, prompt, system, max_tokens):
        systems.append(system)
        return queue.pop(0), 100, 20

    monkeypatch.setitem(client.PROVIDERS, "anthropic", fake_call)
    return systems


def test_intake_returns_clean_text_and_language(monkeypatch: pytest.MonkeyPatch) -> None:
    systems = replies(
        monkeypatch, '{"clean_text": "I was charged $49.99 twice. Order BD-48213.", "language": "en"}'
    )
    with start_trace() as trace:
        out = steps.intake(TICKET)
    assert out.language == "en"
    assert out.clean_text.startswith("I was charged")
    assert systems[0] == steps._prompt("intake").system_with_examples()
    assert trace.spans[0].step == "intake"
    assert trace.spans[0].confidence is None  # the intake prompt doesn't ask for one


def test_extraction_returns_entities_and_records_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    replies(monkeypatch, '{"amounts": ["$49.99"], "reference_ids": ["BD-48213"], "confidence": 4}')
    with start_trace() as trace:
        out = steps.extract("I was charged $49.99 twice. Order BD-48213.")
    assert out.amounts == ["$49.99"]
    assert out.reference_ids == ["BD-48213"]
    assert out.names == []
    assert trace.spans[0].step == "extraction"
    assert trace.spans[0].confidence == 4


def test_a_bad_reply_marks_the_span_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    replies(monkeypatch, "Sorry, I can't do that.")
    with start_trace() as trace, pytest.raises(StepOutputError):
        steps.extract("anything")
    assert trace.spans[0].error == "StepOutputError: no JSON object in the reply"
    assert trace.status == "failed"


def test_the_ticket_is_sent_as_the_user_message(monkeypatch: pytest.MonkeyPatch) -> None:
    replies(monkeypatch, '{"clean_text": "x", "language": "en"}')
    with start_trace() as trace:
        steps.intake("My invoice is wrong")
    assert trace.spans[0].input["prompt"] == "Ticket:\nMy invoice is wrong"
