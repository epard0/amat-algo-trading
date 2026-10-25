"""Latest user-chosen default and historical daily-group evidence stay explicit."""

import json
import math
from pathlib import Path

import pytest

from autoresearch.baseline import CoreV13Config, load_research_config
from autoresearch.loop import incumbent_return_floor


def test_native_daily_group_profile_loads_without_learned_v6_overlays():
    profile = load_research_config(Path("configs/strategy/amat_group_selection_daily.toml"))
    assert isinstance(profile, CoreV13Config)
    assert profile.strategy.adaptive_rotation.group_selection
    assert profile.strategy.cross_sectional.selection_interval_sessions == 1
    assert not profile.risk_learning.enabled
    assert not profile.asset_regime.capital_control_enabled


def test_return_floor_requires_matching_both_cost_cases_and_fails_closed():
    reference = {"train": {"total_return": 0.2}, "stress": {"total_return": 0.15}}
    assert incumbent_return_floor(reference, reference)["passes"]
    higher = {"train": {"total_return": 0.3}, "stress": {"total_return": 0.16}}
    assert incumbent_return_floor(higher, reference)["passes"]
    # Lower return at either cost fails, regardless of a better primary score.
    for case in ["train", "stress"]:
        for value in [-0.1, None, math.nan, math.inf]:
            candidate = {**higher, case: {"total_return": value}}
            assert not incumbent_return_floor(candidate, reference)["passes"]
    assert not incumbent_return_floor({}, reference)["passes"]


def test_new_start_uses_current_volatility_default_and_ignores_old_promotions(
    tmp_path, monkeypatch
):
    from autoresearch import cli

    class CapturedError(Exception):
        pass

    def initialize(_root, _source, config, *_args, **kwargs):
        assert config.name == "amat_group_selection_volatility.toml"
        assert kwargs["promotion"] is None
        raise CapturedError

    monkeypatch.setattr(cli, "initialize", initialize)
    # An old promotion must never be loaded as the new reference.
    monkeypatch.setattr(cli, "active_promotion", lambda _: pytest.fail("old promotion consulted"))
    args = cli.argument_parser().parse_args(
        [
            "start",
            "1",
            "--source",
            str(Path.cwd()),
            "--workspace",
            str(tmp_path / "job"),
            "--built-in",
            "--no-open",
        ]
    )
    with pytest.raises(CapturedError):
        cli.start_session(args)


def test_rejected_catalog_entries_are_below_the_recorded_incumbent():
    decisions = json.loads(
        Path("configs/experiments/candidate-decisions-2026-10-19.json").read_text()
    )
    assert decisions["benchmark"] == "core_daily"
    assert len(decisions["rejected"]) == 6
    for row in decisions["rejected"]:
        assert row["full_net_return"] < decisions["benchmark_returns"]["full"]
        assert row["walk_forward_net_return"] < decisions["benchmark_returns"]["walk_forward"]
    for name in decisions["removed_profiles"]:
        assert not Path("configs/strategy", name).exists()
    assert Path("configs/strategy/amat_group_selection_daily.toml").exists()
