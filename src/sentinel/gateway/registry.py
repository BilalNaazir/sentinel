from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

Provider = Literal["anthropic", "openai", "ollama"]
Tier = Literal["low", "medium", "high"]

DEFAULT_PATH = Path("config") / "models.yaml"


class LongContextPricing(BaseModel):
    threshold_tokens: int
    input_price: float
    output_price: float


class ModelConfig(BaseModel):
    id: str
    provider: Provider
    tier: Tier
    input_price: float
    output_price: float
    long_context: LongContextPricing | None = None
    avg_latency_ms: int | None = None
    model_tag: str | None = None
    notes: str | None = None

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        """Cost in USD. Prices in models.yaml are per million tokens."""
        input_price, output_price = self.input_price, self.output_price
        if self.long_context and tokens_in > self.long_context.threshold_tokens:
            input_price = self.long_context.input_price
            output_price = self.long_context.output_price
        return round((tokens_in * input_price + tokens_out * output_price) / 1_000_000, 8)


class Registry(BaseModel):
    models: dict[str, ModelConfig]
    savings_baseline_model: str

    def get(self, model_id: str) -> ModelConfig:
        if model_id not in self.models:
            raise KeyError(f"Unknown model {model_id!r}. Known: {sorted(self.models)}")
        return self.models[model_id]

    def by_provider(self, provider: Provider) -> list[ModelConfig]:
        return [m for m in self.models.values() if m.provider == provider]


def load_registry(path: Path = DEFAULT_PATH) -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    models = {m["id"]: ModelConfig(**m) for m in raw["models"]}
    return Registry(models=models, savings_baseline_model=raw["savings_baseline_model"])
