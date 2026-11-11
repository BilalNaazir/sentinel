import pytest

from sentinel.pipeline.models import (
    ClassificationOutput,
    ExtractionOutput,
    StepOutputError,
    SummaryOutput,
    parse_output,
)


def test_plain_json() -> None:
    out = parse_output('{"category": "billing", "confidence": 5}', ClassificationOutput)
    assert out.category == "billing"
    assert out.confidence == 5


def test_json_inside_a_code_fence_with_chatter_around_it() -> None:
    raw = 'Sure! Here it is:\n```json\n{"category": "account", "confidence": 3}\n```\nHope that helps.'
    assert parse_output(raw, ClassificationOutput).category == "account"


def test_missing_entity_lists_default_to_empty() -> None:
    out = parse_output('{"amounts": ["$49.99"], "confidence": 4}', ExtractionOutput)
    assert out.amounts == ["$49.99"]
    assert out.names == []


def test_a_reply_with_no_json_is_rejected() -> None:
    with pytest.raises(StepOutputError, match="no JSON object"):
        parse_output("This ticket is about billing.", ClassificationOutput)


def test_broken_json_is_rejected() -> None:
    with pytest.raises(StepOutputError, match="invalid JSON"):
        parse_output('{"category": "billing", "confidence": }', ClassificationOutput)


def test_a_category_outside_the_four_is_rejected() -> None:
    # This is planted failure FX-06 from the data pack: two labels instead of one.
    with pytest.raises(StepOutputError, match="category"):
        parse_output('{"category": "account/billing", "confidence": 2}', ClassificationOutput)


def test_confidence_must_be_1_to_5() -> None:
    with pytest.raises(StepOutputError, match="confidence"):
        parse_output('{"summary": "x", "next_action": "y", "confidence": 9}', SummaryOutput)


def test_the_error_keeps_the_raw_reply() -> None:
    with pytest.raises(StepOutputError) as info:
        parse_output("I can't help with that.", SummaryOutput)
    assert info.value.raw == "I can't help with that."
