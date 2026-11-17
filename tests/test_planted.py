import json

from sentinel.forensics.planted import PIPELINE_VERSION, load_cases, step_inputs, to_trace


def test_all_ten_planted_cases_load() -> None:
    cases = load_cases()
    assert [c["case_id"] for c in cases] == [f"FX-{i:02d}" for i in range(1, 11)]


def test_a_case_becomes_a_four_step_trace() -> None:
    case = load_cases()[0]  # FX-01: extraction invents BD-48213
    trace = to_trace(case)
    assert trace.ticket_id == "FX-01"
    assert trace.pipeline_version == PIPELINE_VERSION
    assert [s.step for s in trace.spans] == ["intake", "extraction", "classification", "summary"]
    assert [s.step_order for s in trace.spans] == [1, 2, 3, 4]
    extraction = trace.spans[1]
    assert json.loads(extraction.output)["reference_ids"] == ["BD-48213"]
    assert extraction.model_id == "claude-haiku-5-5"


def test_each_step_input_is_built_from_the_step_before() -> None:
    inputs = step_inputs(load_cases()[7])  # FX-08: intake dropped the quoted request
    assert "BD-51877" in inputs["intake"]  # the original ticket has it
    assert "BD-51877" not in inputs["extraction"]  # intake's clean text doesn't
    assert "Category: billing" in inputs["summary"]
    assert inputs["summary"].startswith("Ticket:\n")
