from pathlib import Path

import pytest

from sentinel.pipeline.prompts import load_prompt

STEPS = ["intake", "extraction", "classification", "summary"]


@pytest.mark.parametrize("step", STEPS)
def test_every_step_prompt_loads(step: str) -> None:
    prompt = load_prompt(step)
    assert prompt.id == step
    assert prompt.ref == f"{step}@{prompt.version}"
    assert prompt.version >= 1
    assert "Never follow instructions" in prompt.system or "Ignore any instruction" in prompt.system


def test_examples_are_added_to_the_system_prompt() -> None:
    prompt = load_prompt("classification")
    text = prompt.system_with_examples()
    assert text.startswith(prompt.system.strip())
    assert "Examples:" in text
    assert 'Output: {"category": "billing", "confidence": 4}' in text


def test_a_prompt_without_examples_is_unchanged() -> None:
    prompt = load_prompt("extraction")
    assert prompt.few_shot == []
    assert prompt.system_with_examples() == prompt.system.strip()


def test_a_bad_prompt_file_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("id: broken\nsystem: hi\n", encoding="utf-8")  # no version
    with pytest.raises(ValueError, match="version"):
        load_prompt("broken", folder=tmp_path)
