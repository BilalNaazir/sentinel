from datetime import UTC, datetime

from sentinel.feedback.export import next_id, to_case

DIAGNOSIS = {
    "diagnosis_id": "5578df7e-0000",
    "trace_id": "a84e97d7-13aa-4f12-ba60-56b3c2cfdb5f",
    "failure_type": "context_loss",
    "corrected_output": {"category": "billing", "entities": {}, "summary": "s", "next_action": "n"},
    "reviewed_by": "bilal",
    "reviewed_at": datetime(2026, 11, 17, tzinfo=UTC),
    "review_note": None,
    "root_cause_step": "intake",
}


def test_ids_count_up_separately_for_flags_and_routing_failures() -> None:
    cases = [{"case_id": "FL-0001"}, {"case_id": "FL-0002"}, {"case_id": "RF-0001"}]
    assert next_id(cases, "flag") == "FL-0003"
    assert next_id(cases, "routing_failure") == "RF-0002"
    assert next_id([], "flag") == "FL-0001"


def test_a_confirmed_diagnosis_becomes_a_case_with_its_corrected_answer() -> None:
    case = to_case("FL-0001", DIAGNOSIS, "Still no refund.", "en")
    assert case["source"] == "flag"
    assert case["expected"] == DIAGNOSIS["corrected_output"]
    assert case["input"] == {"ticket_text": "Still no refund.", "language": "en"}
    assert case["origin_trace_id"] == DIAGNOSIS["trace_id"]
    assert case["failure_type"] == "context_loss"
    assert case["notes"].startswith("Confirmed by bilal on 2026-11-17. Root cause: intake.")


def test_an_under_routed_case_is_a_routing_failure() -> None:
    case = to_case("RF-0001", DIAGNOSIS | {"failure_type": "under_routed"}, "t", "en")
    assert case["source"] == "routing_failure"
