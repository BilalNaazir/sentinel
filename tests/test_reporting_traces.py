from sentinel.reporting.traces import compare_answers, step_colour


def span(**overrides: object) -> dict:
    return {"error": None, "escalated_from": None, "confidence": 5} | overrides


def test_step_colours() -> None:
    assert step_colour(span(), None) == "green"
    assert step_colour(span(), 4) == "green"
    assert step_colour(span(error="StepOutputError"), None) == "red"
    assert step_colour(span(), 2) == "red"  # the forensics judge found it unfaithful
    assert step_colour(span(), 3) == "yellow"
    assert step_colour(span(escalated_from="claude-haiku-5-5"), None) == "yellow"
    assert step_colour(span(confidence=2), None) == "yellow"


def test_compare_answers_marks_each_field() -> None:
    entities = {f: [] for f in ("names", "emails", "products", "reference_ids", "amounts", "dates")}
    expected = {
        "category": "billing",
        "entities": entities | {"reference_ids": ["BD-51877"]},
        "summary": "s",
        "next_action": "n",
    }
    actual = {"category": "billing", "entities": dict(entities), "summary": "s2", "next_action": "n2"}
    table = compare_answers(expected, actual)
    assert table.loc["category", "match"] is True
    assert table.loc["reference_ids", "match"] is False
    assert (table.loc["reference_ids", "expected"], table.loc["reference_ids", "pipeline"]) == (
        "BD-51877",
        "-",
    )
    assert table.loc["names", "match"] is True  # nothing expected, nothing given
    assert table.loc["summary", "match"] is None  # summaries are judged, not matched
