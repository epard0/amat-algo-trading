"""Universe/factor isolation and an identical flat-account execution boundary."""

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.domain.types import Symbol
from amat.errors import DataIntegrityError
from autoresearch.improvements.group_study import recipes


def test_factorial_recipes_preserve_caps_and_isolate_the_requested_changes():
    base = load_strategy_config(Path("configs/strategy/amat_v13_return_target.toml")).strategy
    design = json.loads(Path("configs/experiments/group-selection-expansion.json").read_text())
    values = {k: StrategyConfig.model_validate(v) for k, v in recipes(base, design).items()}
    assert len(values) == 12
    for name, strategy in values.items():
        assert len(strategy.universe.symbols) == (38 if name.startswith("wide_") else 11)
        assert strategy.constraints == base.constraints
        assert strategy.allocation == base.allocation
        assert strategy.execution == base.execution
        assert strategy.cross_sectional.max_positions == 4
    assert values["baseline"] == base
    for prefix in ["core", "wide"]:
        for daily, quality, suffix in [
            (False, False, "groups"),
            (True, False, "daily"),
            (False, True, "quality"),
            (True, True, "both"),
        ]:
            key = "groups" if prefix == "core" and suffix == "groups" else prefix + "_" + suffix
            s = values[key]
            assert s.adaptive_rotation.group_selection
            assert s.cross_sectional.selection_interval_sessions == (1 if daily else 5)
            assert s.structure.enabled == quality
            assert not s.adaptive_rotation.regime_overlay
            assert not s.adaptive_rotation.residual_ranking
    broad = values["wide_groups"]
    assert {s for g in broad.adaptive_rotation.groups for s in g.symbols} == set(
        broad.universe.symbols
    )


def source(lag=1):
    n = 390
    dates = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(n))
    rng = np.random.default_rng(77)
    prices = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.002, (n, 2)), axis=0))
    windows = prices.copy()
    windows[:320] = np.nan
    panel = MarketPanel(
        sessions=dates,
        symbols=(Symbol("A"), Symbol("B")),
        adjusted_closes=prices,
        raw_closes=prices,
        window_vwaps=windows,
        window_volumes=np.full_like(prices, 1e6),
        source="test",
    )
    strategy = StrategyConfig.model_validate(
        {
            "name": "start-test",
            "version": "0.0.1",
            "universe": {"clusters": [{"name": "all", "symbols": ["A", "B"], "gross_cap": 1}]},
            "execution": {"signal_to_execution_lag_sessions": lag},
        }
    )
    return strategy, panel


@pytest.mark.parametrize("lag", [1, 2])
def test_execution_boundary_keeps_history_but_creates_no_earlier_fills_or_misses(lag):
    strategy, panel = source(lag)
    result = run_backtest(
        strategy, panel, strategy.to_universe(), execution_start=panel.sessions[320]
    )
    assert result.sessions[0].session == panel.sessions[320 - lag]
    assert result.sessions[0].equity == 100000
    assert all(
        not r.fills and not r.missed for r in result.sessions if r.session < panel.sessions[320]
    )
    assert sum(len(r.fills) for r in result.sessions) > 0
    assert all(
        fill.execution_session >= panel.sessions[320] for r in result.sessions for fill in r.fills
    )


def test_missing_or_premature_execution_start_is_rejected():
    strategy, panel = source()
    for start in [dt.date(1999, 1, 1), panel.sessions[100]]:
        with pytest.raises(DataIntegrityError, match="execution_start"):
            run_backtest(strategy, panel, strategy.to_universe(), execution_start=start)


def test_retained_profile_matches_the_registered_core_recipe():
    base = load_strategy_config(Path("configs/strategy/amat_v13_return_target.toml")).strategy
    design = json.loads(Path("configs/experiments/group-selection-expansion.json").read_text())
    expected = recipes(base, design)["core_daily"]
    actual = load_strategy_config(
        Path("configs/strategy/amat_group_selection_daily.toml")
    ).strategy.model_dump(mode="json")
    for item in [actual, expected]:
        item.pop("name")
        item.pop("version")
    assert actual == expected
