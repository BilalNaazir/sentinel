from typing import Any

from sentinel.forensics.analyzer import evidence_chain, find_root_cause, output_json, rule_type
from sentinel.forensics.faithfulness import StepScore, TraceSpan


def scored(
    step: str, score: int | None, *, output: str = "{}", tier: int | None = 1, error: str | None = None
) -> StepScore:
    span = TraceSpan(f"span-{step}", step, "claude-haiku-5-5", 4, tier, "input", output, error)
    return StepScore(span, score, f"{step} problem", 0.0)


def trace(*scores: int | None) -> list[StepScore]:
    steps = ["intake", "extraction", "classification", "summary"]
    return [scored(step, s) for step, s in zip(steps, scores, strict=True)]


class FakeConn:
    """Stands in for a database connection: answers the verifications query with `row`."""

    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row

    def execute(self, query: str, params: tuple[Any, ...]) -> "FakeConn":
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


def test_the_earliest_low_step_is_the_root_cause_not_the_worst() -> None:
    # FX-01: extraction invents an ID (1), the summary repeats it (2)
    assert find_root_cause(trace(5, 1, 5, 2)).span.step == "extraction"
    # FX-10: classification and summary both score 1; the earlier one is the cause
    assert find_root_cause(trace(5, 5, 1, 1)).span.step == "classification"


def test_a_minor_problem_early_on_is_not_the_root_cause() -> None:
    # FX-05: intake 3 (left a sign-off in), summary 1 (reversed the plan change)
    assert find_root_cause(trace(3, 5, 5, 1)).span.step == "summary"


def test_a_failed_step_counts_as_a_root_cause() -> None:
    scores = trace(5, 5, 5, 5)
    scores[1] = scored("extraction", None, error="StepOutputError: bad JSON")
    assert find_root_cause(scores).span.step == "extraction"


def test_no_root_cause_when_every_step_is_faithful() -> None:
    assert find_root_cause(trace(5, 4, 5, 3)) is None


def test_output_json_reads_invalid_values_and_fences() -> None:
    assert output_json('```json\n{"category": "account/billing"}\n```') == {"category": "account/billing"}
    assert output_json("no json") == {}


def test_rule_an_invalid_category_is_a_prompt_failure() -> None:
    root = scored("classification", 1, output='{"category": "account/billing", "confidence": 2}')
    assert rule_type(FakeConn(None), root)[0] == "prompt_failure"


def test_rule_a_verifier_disagreement_on_tier_1_is_under_routed() -> None:
    root = scored("classification", 1, output='{"category": "technical", "confidence": 2}', tier=1)
    assert rule_type(FakeConn(("gpt-5.6-sol",)), root)[0] == "under_routed"


def test_rule_no_verifier_disagreement_leaves_it_to_the_judge() -> None:
    root = scored("classification", 1, output='{"category": "technical", "confidence": 2}', tier=1)
    assert rule_type(FakeConn(None), root) is None


def test_evidence_lists_the_root_cause_then_what_later_steps_did() -> None:
    scores = trace(5, 1, 5, 2)
    text = evidence_chain(scores[1], scores)
    assert text.splitlines() == [
        "Extraction (score 1): extraction problem",
        "Then summary (score 2): summary problem",
    ]
