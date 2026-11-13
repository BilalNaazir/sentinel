import pytest

from sentinel.evals.judge import JudgeScore
from sentinel.evals.run import PassCriteria, score_result, summarise
from sentinel.pipeline.models import Entities
from sentinel.pipeline.runner import PipelineResult

CRITERIA = PassCriteria(category_must_match=True, min_entity_f1=0.8, min_summary_score=4)
CASE = {
    "case_id": "GT-001",
    "expected": {
        "category": "billing",
        "entities": {"names": ["Maria Lopez"], "amounts": ["$49.99"], "reference_ids": ["BD-48213"]},
        "summary": "Customer was charged twice.",
    },
}


def pipeline(
    category: str = "billing", names: list[str] | None = None, failed: bool = False
) -> PipelineResult:
    if failed:
        return PipelineResult(trace_id="t", status="failed", failed_step="classification", latency_ms=100)
    entities = Entities(names=names or [], amounts=["$49.99"], reference_ids=["BD-48213"])
    return PipelineResult(trace_id="t", status="success", category=category, entities=entities, summary="s")


def test_everything_right_passes() -> None:
    r = score_result(CASE, pipeline(names=["Maria Lopez"]), JudgeScore(score=5, reason="ok"), CRITERIA)
    assert r.passed
    assert r.reasons == []
    assert r.entity_f1 == 1.0


def test_each_failed_rule_gives_a_reason() -> None:
    r = score_result(CASE, pipeline(category="account"), JudgeScore(score=3, reason="wordy"), CRITERIA)
    assert not r.passed
    assert r.reasons == ["category account, expected billing", "summary scored 3"]


def test_a_lost_name_alone_is_still_above_the_entity_bar() -> None:
    # 2 of 3 entities found: F1 = 2*2/(3+2) = 0.8, exactly the threshold, so it passes.
    r = score_result(CASE, pipeline(names=[]), JudgeScore(score=5, reason="ok"), CRITERIA)
    assert r.entity_f1 == pytest.approx(0.8)
    assert r.passed


def test_a_failed_pipeline_run_fails_the_case() -> None:
    r = score_result(CASE, pipeline(failed=True), None, CRITERIA)
    assert not r.passed
    assert r.reasons == ["pipeline failed at classification"]
    assert not r.category_match
    assert r.entity_f1 == 0.0


def test_summary_counts_failures_as_wrong() -> None:
    results = [
        score_result(CASE, pipeline(names=["Maria Lopez"]), JudgeScore(score=5, reason="ok"), CRITERIA),
        score_result(CASE, pipeline(failed=True), None, CRITERIA),
    ]
    s = summarise(results)
    assert s.pass_rate == 0.5
    assert s.category_accuracy == 0.5  # not 100%: the failed run counts against it
    assert s.pipeline_failures == 1
    assert s.mean_summary_score == 5


def test_a_summary_the_judge_could_not_score_does_not_pass() -> None:
    r = score_result(CASE, pipeline(names=["Maria Lopez"]), None, CRITERIA)
    assert not r.passed
    assert r.reasons == ["summary could not be judged"]
