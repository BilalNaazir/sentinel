from pathlib import Path

import pytest
import yaml

from sentinel.gateway.registry import load_registry
from sentinel.gateway.routing import RoutingError, check, load_routing


def write(tmp_path: Path, **changes: object) -> Path:
    """Copy the real routing.yaml with some keys changed, so each test breaks exactly one thing."""
    raw = yaml.safe_load(Path("config/routing.yaml").read_text(encoding="utf-8"))
    raw.update(changes)
    path = tmp_path / "routing.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_real_routing_file_is_valid() -> None:
    routing = load_routing()
    assert routing.version >= 2
    assert set(routing.steps) >= {"intake", "extraction", "classification", "summary"}


def test_model_for_returns_the_model_at_each_tier() -> None:
    routing = load_routing()
    tiers = routing.steps["summary"]
    assert routing.model_for("summary", 1) == tiers.tier_1
    assert routing.model_for("summary", 3) == tiers.tier_3


def test_unknown_step_gives_a_clear_error() -> None:
    with pytest.raises(KeyError, match="No routing for step 'translate'"):
        load_routing().model_for("translate", 1)


def test_unknown_model_is_reported(tmp_path: Path) -> None:
    steps = {
        "summary": {"tier_1": "claude-haiku-5-5", "tier_2": "no-such-model", "tier_3": "claude-opus-5-5"}
    }
    with pytest.raises(RoutingError, match="summary tier_2: unknown model 'no-such-model'"):
        load_routing(write(tmp_path, steps=steps))


def test_all_problems_are_reported_together(tmp_path: Path) -> None:
    steps = {"summary": {"tier_1": "bad-1", "tier_2": "bad-2", "tier_3": "claude-opus-5-5"}}
    with pytest.raises(RoutingError, match=r"2 problem\(s\)"):
        load_routing(write(tmp_path, steps=steps))


def test_verifier_must_not_be_in_the_tier_map(tmp_path: Path) -> None:
    path = write(tmp_path, verifier_reference_model="claude-opus-5-5")
    with pytest.raises(RoutingError, match="checking its own work"):
        load_routing(path)


def test_missing_tier_is_rejected(tmp_path: Path) -> None:
    steps = {"summary": {"tier_1": "claude-haiku-5-5", "tier_2": "claude-sonnet-5-5"}}
    with pytest.raises(ValueError, match="tier_3"):
        load_routing(write(tmp_path, steps=steps))


def test_check_on_the_real_file_finds_nothing() -> None:
    assert check(load_routing(), load_registry()) == []
