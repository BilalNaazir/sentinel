from sentinel.feedback.issues import first_fixed, marks, status_of


def test_status_of_each_history() -> None:
    assert status_of([]) == "not run yet"
    assert status_of([True, True]) == "passing"
    assert status_of([False, True]) == "fixed"
    assert status_of([False, False]) == "failing"
    assert status_of([True, False]) == "regressed"  # the one to act on
    assert status_of([False, True, False]) == "regressed"


def test_marks_and_first_fixed() -> None:
    history = [("baseline v5", False), ("pipeline v4", False), ("intake@3", True), ("baseline v6", True)]
    assert marks(history) == "✗✗✓✓"
    assert first_fixed(history) == "intake@3"
    assert first_fixed([("a", True)]) == "-"
    assert marks([]) == "-"
