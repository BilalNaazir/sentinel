"""Load the versioned step prompts from prompts/*.yaml."""

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

PROMPTS_DIR = Path("prompts")


class Example(BaseModel):
    input: str
    output: dict[str, Any]


class Prompt(BaseModel):
    id: str
    version: int
    system: str
    few_shot: list[Example] = Field(default_factory=list)

    @property
    def ref(self) -> str:
        """Short name recorded on every trace, e.g. 'classification@1'."""
        return f"{self.id}@{self.version}"

    def system_with_examples(self) -> str:
        """The system prompt, followed by any worked examples."""
        if not self.few_shot:
            return self.system.strip()
        parts = [self.system.strip(), "", "Examples:"]
        for ex in self.few_shot:
            parts += ["", f"Input: {ex.input}", f"Output: {json.dumps(ex.output, ensure_ascii=False)}"]
        return "\n".join(parts)


def load_prompt(step: str, folder: Path = PROMPTS_DIR) -> Prompt:
    raw = yaml.safe_load((folder / f"{step}.yaml").read_text(encoding="utf-8"))
    return Prompt(**raw)
