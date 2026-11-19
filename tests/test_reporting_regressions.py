from sentinel.evals.compare import Comparison, Flip
from sentinel.reporting.regressions import flips_table, issue_rows


def test_issue_rows_give_status_history_and_fixing_run() -> None:
    hist = {
        "FL-0001": [("baseline v6", True), ("baseline v7", True), ("ci PR 3", False)],
        "GT-035": [("baseline v6", False), ("baseline v7", True)],
    }
    table = issue_rows(
        [("FL-0001", "flag: context_loss"), ("GT-035", "label review"), ("FL-0009", "flag")], hist
    )
    rows = table.set_index("case")
    assert (rows.loc["FL-0001", "status"], rows.loc["FL-0001", "history"]) == ("regressed", "✓✓✗")
    assert (rows.loc["GT-035", "status"], rows.loc["GT-035", "fixed in"]) == ("fixed", "baseline v7")
    assert rows.loc["FL-0009", "status"] == "not run yet"


def test_flips_table_lists_regressions_first() -> None:
    cmp = Comparison(
        baseline="a",
        candidate="b",
        pass_rate_change_pct=-1.0,
        category_changes_pct={},
        summary_score_change=None,
        regressions=[Flip(case_id="GT-011", reasons=["entity F1 0.67"])],
        improvements=[Flip(case_id="GT-028", reasons=["summary scored 3"])],
        still_failing=["GT-050"],
        status="pass",
        status_reasons=[],
    )
    table = flips_table(cmp)
    assert list(table["case"]) == ["GT-011", "GT-028"]
    assert list(table["change"]) == ["regressed", "improved"]
    assert table.iloc[1]["reasons"] == "was: summary scored 3"
