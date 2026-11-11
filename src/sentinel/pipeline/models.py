"""What each pipeline step returns, and turning a model's JSON reply into it safely."""

import json
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

Category = Literal["billing", "technical", "account", "general"]


class IntakeOutput(BaseModel):
    clean_text: str
    language: str


class Entities(BaseModel):
    names: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    amounts: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)


class ExtractionOutput(Entities):
    confidence: int = Field(ge=1, le=5)


class ClassificationOutput(BaseModel):
    category: Category
    confidence: int = Field(ge=1, le=5)


class SummaryOutput(BaseModel):
    summary: str
    next_action: str
    confidence: int = Field(ge=1, le=5)


class StepOutputError(ValueError):
    """The model replied, but not with the JSON the step asked for."""

    def __init__(self, reason: str, raw: str) -> None:
        super().__init__(f"{reason}. Raw reply: {raw[:200]!r}")
        self.reason = reason
        self.raw = raw


M = TypeVar("M", bound=BaseModel)


def parse_output(raw: str, model: type[M]) -> M:
    """Find the JSON object in a model's reply and check it against the step's output model."""
    start = raw.find("{")
    if start == -1:
        raise StepOutputError("no JSON object in the reply", raw)
    try:
        data, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        raise StepOutputError(f"invalid JSON ({exc.msg})", raw) from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise StepOutputError(f"JSON doesn't match {model.__name__} ({problems})", raw) from exc
