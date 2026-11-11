import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
CATEGORIES = {"billing", "technical", "account", "general"}


def load_golden() -> list[dict]:
    return json.loads((DATA / "eval_dataset.v2.json").read_text(encoding="utf-8"))["cases"]


def test_golden_has_100_unique_cases() -> None:
    cases = load_golden()
    assert len(cases) == 100
    assert len({c["case_id"] for c in cases}) == 100


def test_every_category_is_valid() -> None:
    for c in load_golden():
        assert c["expected"]["category"] in CATEGORIES, c["case_id"]


def test_entities_appear_word_for_word_in_the_ticket() -> None:
    for c in load_golden():
        text = c["input"]["ticket_text"]
        for field, values in c["expected"]["entities"].items():
            for value in values:
                assert value in text, f"{c['case_id']}: {field}={value!r} not in ticket"
