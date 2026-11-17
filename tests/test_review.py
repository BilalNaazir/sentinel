from sentinel.feedback.review import check_answer, draft_answer

TICKET = "I was charged $49.99 twice for order BD-48213. Thanks, Maria Lopez"


def good_answer() -> dict:
    return {
        "category": "billing",
        "entities": {
            "names": ["Maria Lopez"],
            "emails": [],
            "products": [],
            "reference_ids": ["BD-48213"],
            "amounts": ["$49.99"],
            "dates": [],
        },
        "summary": "Maria Lopez was charged $49.99 twice for order BD-48213.",
        "next_action": "Refund the duplicate charge on BD-48213.",
    }


def test_a_good_answer_has_no_problems() -> None:
    assert check_answer(good_answer(), TICKET) == []


def test_an_entity_not_in_the_ticket_is_caught() -> None:
    answer = good_answer()
    answer["entities"]["reference_ids"] = ["BD-99999"]
    assert check_answer(answer, TICKET) == [
        "reference_ids: 'BD-99999' does not appear word for word in the ticket"
    ]


def test_an_invalid_category_and_blank_summary_are_caught() -> None:
    answer = good_answer() | {"category": "account/billing", "summary": " "}
    problems = check_answer(answer, TICKET)
    assert any("account/billing" in p for p in problems)
    assert "summary is empty" in problems


def test_missing_entity_fields_are_caught() -> None:
    answer = good_answer()
    del answer["entities"]["dates"]
    assert any("exactly these keys" in p for p in check_answer(answer, TICKET))


def test_the_draft_is_what_the_pipeline_said() -> None:
    outputs = {
        "classification": {"category": "technical", "confidence": 2},
        "extraction": {"names": [], "reference_ids": ["BD-48213"], "confidence": 4},
        "summary": {"summary": "Charged twice.", "next_action": "Refund.", "confidence": 4},
    }
    draft = draft_answer(outputs)
    assert draft["category"] == "technical"
    assert draft["entities"]["reference_ids"] == ["BD-48213"]
    assert draft["entities"]["dates"] == []  # every field present, even when the step left it out
    assert (draft["summary"], draft["next_action"]) == ("Charged twice.", "Refund.")
