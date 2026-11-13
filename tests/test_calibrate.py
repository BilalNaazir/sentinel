import pytest

from sentinel.evals.calibrate import agreement, confusion


def test_perfect_agreement_is_trusted() -> None:
    result = agreement([(5, 5), (3, 3), (1, 1)])
    assert result.exact == 1.0
    assert result.bias == 0.0
    assert result.trusted


def test_a_lenient_judge_shows_positive_bias_and_is_not_trusted() -> None:
    # The judge gives every summary 1 point more than the human did, and 2 more on one.
    result = agreement([(1, 2), (2, 3), (3, 4), (4, 5), (3, 5)])
    assert result.bias == pytest.approx(1.2)
    assert result.within_one == pytest.approx(0.8)
    assert not result.trusted


def test_off_by_one_counts_as_close_but_not_exact() -> None:
    result = agreement([(4, 5), (4, 3), (4, 4), (4, 4)])
    assert result.exact == 0.5
    assert result.within_one == 1.0
    assert result.bias == 0.0
    assert result.trusted


def test_confusion_table_puts_counts_in_the_right_cells() -> None:
    table = confusion([(5, 5), (5, 5), (1, 3)])
    rows = table.splitlines()
    assert rows[1].split() == ["1", ".", ".", "1", ".", "."]  # human 1, judge 3
    assert rows[5].split() == ["5", ".", ".", ".", ".", "2"]  # human 5, judge 5 twice
