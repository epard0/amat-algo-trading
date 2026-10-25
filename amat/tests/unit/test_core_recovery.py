"""Native recovery recipes change only their declared selection parameters."""

from copy import deepcopy
from pathlib import Path

from amat.config.loading import load_strategy_config
from autoresearch.improvements.core_quality_recovery_study import recipes as quality_recipes
from autoresearch.improvements.core_recovery_study import recipes


def test_native_group_recipes_preserve_signal_risk_and_execution():
    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    for _arm, value in recipes(base).items():
        restored = deepcopy(value)
        restored["adaptive_rotation"] = base.model_dump(mode="json")["adaptive_rotation"]
        assert restored == base.model_dump(mode="json")
    assert recipes(base)["all_groups"]["adaptive_rotation"]["max_active_groups"] == 3
    for _arm, value in quality_recipes(base).items():
        restored = deepcopy(value)
        restored["trend"] = base.model_dump(mode="json")["trend"]
        restored["cross_sectional"] = base.model_dump(mode="json")["cross_sectional"]
        assert restored == base.model_dump(mode="json")


def test_retained_profile_is_exact_canonical_native_recipe():
    base = load_strategy_config(Path("configs/strategy/amat_group_selection_daily.toml")).strategy
    variant = load_strategy_config(
        Path("configs/strategy/amat_group_selection_all_groups.toml")
    ).strategy.model_dump(mode="json")
    expected = recipes(base)["all_groups"]
    variant["name"] = expected["name"]
    variant["version"] = expected["version"]
    assert variant == expected


def test_retained_native_recipe_is_registered_for_backtests():
    from amat.cli.main import _BACKTEST_VERSION_CONFIGS, build_parser

    args = build_parser().parse_args(["backtest", "--strategy-version", "daily-all-groups"])
    assert args.strategy_version == "daily-all-groups"
    assert (
        _BACKTEST_VERSION_CONFIGS["daily-all-groups"].name == "amat_group_selection_all_groups.toml"
    )
    assert build_parser().parse_args(["backtest"]).strategy_version == "daily-volatility"
    # Daily core remains an explicit historical comparator after the user chose
    # the verified volatility-switching strategy as the overall default.
    assert (
        build_parser().parse_args(["backtest", "--strategy-version", "daily-core"]).strategy_version
        == "daily-core"
    )
    assert _BACKTEST_VERSION_CONFIGS["daily-core"].name == "amat_group_selection_daily.toml"
