"""What each pipeline step returns, and turning a model's JSON reply into it safely."""

import json
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, Field, ValidationError

Category = Literal["billing", "technical", "account", "general"]


def _one_score(value: Any) -> Any:
    """Models sometimes give one confidence per field ({"names": 5, "products": 3}).
    Keep the lowest: the step is only as sure as its least sure part."""
    if isinstance(value, dict):
        scores = [v for v in value.values() if isinstance(v, int | float) and not isinstance(v, bool)]
        return min(scores) if scores else value
    return value


Confidence = Annotated[int, BeforeValidator(_one_score), Field(ge=1, le=5)]


class IntakeOutput(BaseModel):
    clean_text: str
    language: str
    sender: str = ""  # who wrote it: the sign-off intake removed (since intake@2)


class Entities(BaseModel):
    names: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    amounts: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)


class ExtractionOutput(Entities):
    confidence: Confidence


class ClassificationOutput(BaseModel):
    category: Category
    confidence: Confidence


class SummaryOutput(BaseModel):
    summary: str
    next_action: str
    confidence: Confidence


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
