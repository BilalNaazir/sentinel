"""Read and check config/routing.yaml: which model runs each pipeline step at each tier."""

from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from sentinel.gateway.registry import Registry, load_registry

DEFAULT_PATH = Path("config") / "routing.yaml"

TierNumber = Literal[1, 2, 3]
TIERS: tuple[TierNumber, ...] = (1, 2, 3)


class RoutingError(ValueError):
    """routing.yaml is broken. The message lists every problem found, not just the first."""


class StepTiers(BaseModel):
    tier_1: str
    tier_2: str
    tier_3: str

    def model(self, tier: TierNumber) -> str:
        return {1: self.tier_1, 2: self.tier_2, 3: self.tier_3}[tier]


class Escalation(BaseModel):
    enabled: bool
    confidence_at_or_below: int = Field(ge=1, le=5)
    max_escalations_per_request: int = Field(ge=0)


class Routing(BaseModel):
    version: int
    default_provider_preference: list[str]
    steps: dict[str, StepTiers]
    escalation: Escalation
    judge_model: str
    verifier_reference_model: str

    def model_for(self, step: str, tier: TierNumber) -> str:
        if step not in self.steps:
            raise KeyError(f"No routing for step {step!r}. Known: {sorted(self.steps)}")
        return self.steps[step].model(tier)

    def models_in_map(self) -> set[str]:
        return {tiers.model(t) for tiers in self.steps.values() for t in TIERS}


def check(routing: Routing, registry: Registry) -> list[str]:
    """Every problem with this routing, as readable sentences. An empty list means it is fine."""
    problems = []
    for step, tiers in routing.steps.items():
        for tier in TIERS:
            model_id = tiers.model(tier)
            if model_id not in registry.models:
                problems.append(f"{step} tier_{tier}: unknown model {model_id!r}")
    for role in ("judge_model", "verifier_reference_model"):
        model_id = getattr(routing, role)
        if model_id not in registry.models:
            problems.append(f"{role}: unknown model {model_id!r}")
    if routing.verifier_reference_model in routing.models_in_map():
        problems.append(
            f"verifier_reference_model {routing.verifier_reference_model!r} is also in the tier map, "
            "so the verifier would be checking its own work"
        )
    return problems


def load_routing(path: Path = DEFAULT_PATH, registry: Registry | None = None) -> Routing:
    routing = Routing(**yaml.safe_load(path.read_text(encoding="utf-8")))
    problems = check(routing, registry or load_registry())
    if problems:
        raise RoutingError(f"{path} has {len(problems)} problem(s):\n- " + "\n- ".join(problems))
    return routing


@cache
def get_routing() -> Routing:
    """The routing the app uses. Cached: read once per process."""
    return load_routing()


def main() -> None:
    routing = load_routing()
    registry = load_registry()
    print(f"routing.yaml version {routing.version}  (prices in $ per million tokens, in / out)\n")
    print(f"{'step':<16}" + "".join(f"{f'tier {t}':<34}" for t in TIERS))
    for step, tiers in routing.steps.items():
        cells = []
        for t in TIERS:
            m = registry.get(tiers.model(t))
            cells.append(f"{m.id} ({m.input_price:g} / {m.output_price:g})")
        print(f"{step:<16}" + "".join(f"{c:<34}" for c in cells))
    e = routing.escalation
    print(
        f"\nescalation: {'on' if e.enabled else 'off'}, when confidence <= {e.confidence_at_or_below}, "
        f"at most {e.max_escalations_per_request} per request"
    )
    print(f"judge: {routing.judge_model}   verifier reference: {routing.verifier_reference_model}")


if __name__ == "__main__":
    main()
