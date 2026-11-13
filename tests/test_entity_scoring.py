import pytest

from sentinel.evals.entities import aggregate, case_f1, count_matches, f1, score_case


def test_lenient_fields_accept_partial_names() -> None:
    assert count_matches("products", ["Pro plan"], ["Pro"]).matched == 1
    assert count_matches("products", ["plan Starter"], ["Starter"]).matched == 1
    assert count_matches("names", ["Maria Lopez"], ["maria lopez"]).matched == 1


def test_strict_fields_need_an_exact_match() -> None:
    assert count_matches("amounts", ["$49.99"], ["49.99"]).matched == 0
    assert count_matches("reference_ids", ["BD-48213"], ["BD-4821"]).matched == 0
    assert count_matches("dates", ["monday"], ["Monday"]).matched == 1  # dates ignore case only


def test_each_prediction_can_only_match_once() -> None:
    count = count_matches("amounts", ["$49.99", "$49.99"], ["$49.99"])
    assert count.matched == 1


def test_f1() -> None:
    assert count_matches("names", [], []).matched == 0
    assert f1(count_matches("names", [], [])) == 1.0  # nothing to find, nothing found
    assert f1(count_matches("names", ["Maria Lopez"], [])) == 0.0  # missed it
    half = count_matches("amounts", ["$396", "$495"], ["$396"])
    assert f1(half) == pytest.approx(2 * 1 / 3)


def test_case_f1_pools_all_fields() -> None:
    counts = score_case(
        {"names": ["Maria Lopez"], "amounts": ["$49.99"], "reference_ids": ["BD-48213"]},
        {"amounts": ["$49.99"], "reference_ids": ["BD-48213"]},
    )
    assert case_f1(counts) == pytest.approx(2 * 2 / 5)  # 2 matched, 3 expected, 2 predicted


def test_aggregate_ignores_tickets_with_nothing_to_find() -> None:
    all_counts = [
        score_case({"names": ["Maria Lopez"]}, {}),  # name missed
        score_case({}, {}),  # no names expected or found: shouldn't make names look better
        score_case({}, {}),
    ]
    scores = aggregate(all_counts)
    assert scores["names"].recall == 0.0
    assert scores["names"].expected == 1
    assert scores["emails"].recall is None  # no emails anywhere: no score at all
