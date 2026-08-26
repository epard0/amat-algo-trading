"""Isolation, causality, and fail-safe behavior of the v6 candidate."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import numpy as np
import pytest

from amat.backtest.runner import DecisionOverlay, run_backtest
from amat.config.loading import load_strategy_config
from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from amat.regime.asset import build_asset_regime_history


@pytest.fixture(scope="module")
def v6():
    return load_v6_config("configs/strategy/amat_v6.toml").config


@pytest.fixture(scope="module")
def panel(v6):
    return generate_panel(v6.strategy.to_universe(), sessions=460, seed=61, trend_strength=0.4)


def test_v6_universe_is_the_v1_universe_minus_tlt(v6) -> None:
    """v6 dropped TLT on 2026-08-15; every other cluster must be untouched.

    This replaces an earlier assertion that the two universes were equal. The
    inequality is now intentional, so the test pins the *exact* difference
    rather than being relaxed to accommodate it: one symbol leaves one cluster,
    nothing else moves, and no cap changes. A second unreviewed universe edit
    would fail here, which is the point.
    """
    v1 = load_strategy_config("configs/strategy/amat_v1.toml").strategy
    assert v6.strategy.version == "6.0.0"
    assert v6.strategy.constraints.target_annualized_volatility == 0.06
    assert not v6.asset_regime.capital_control_enabled

    v1_symbols = [str(symbol) for symbol in v1.universe.symbols]
    v6_symbols = [str(symbol) for symbol in v6.strategy.universe.symbols]
    assert "TLT" in v1_symbols
    assert "TLT" not in v6_symbols
    assert v6_symbols == [symbol for symbol in v1_symbols if symbol != "TLT"]

    v1_clusters = {cluster.name: cluster for cluster in v1.universe.clusters}
    v6_clusters = {cluster.name: cluster for cluster in v6.strategy.universe.clusters}
    assert v6_clusters.keys() == v1_clusters.keys()
    for name, cluster in v6_clusters.items():
        assert cluster.gross_cap == v1_clusters[name].gross_cap
        expected = tuple(s for s in v1_clusters[name].symbols if str(s) != "TLT")
        assert cluster.symbols == expected


def test_asset_regimes_are_causal(v6, panel) -> None:
    cut = 390
    baseline = build_asset_regime_history(panel, v6.asset_regime, v6.liquidity)
    mutated = replace(
        panel,
        adjusted_closes=np.vstack([panel.adjusted_closes[:cut], panel.adjusted_closes[cut:] * 7.0]),
        raw_closes=np.vstack([panel.raw_closes[:cut], panel.raw_closes[cut:] * 7.0]),
        adjusted_highs=np.vstack([panel.adjusted_highs[:cut], panel.adjusted_highs[cut:] * 7.0]),
        adjusted_lows=np.vstack([panel.adjusted_lows[:cut], panel.adjusted_lows[cut:] * 7.0]),
    )
    after = build_asset_regime_history(mutated, v6.asset_regime, v6.liquidity)
    np.testing.assert_array_equal(baseline.labels[:cut], after.labels[:cut])
    np.testing.assert_array_equal(
        baseline.overlay.allow_increase[:cut], after.overlay.allow_increase[:cut]
    )
    np.testing.assert_allclose(
        baseline.overlay.risk_multipliers[:cut], after.overlay.risk_multipliers[:cut]
    )


def test_overlay_never_adds_risk(v6, panel) -> None:
    history = build_asset_regime_history(panel, v6.asset_regime, v6.liquidity)
    assert np.all(history.overlay.risk_multipliers >= 0.0)
    assert np.all(history.overlay.risk_multipliers <= 1.0)
    assert history.overlay.increase_multipliers is not None
    assert np.all(history.overlay.increase_multipliers <= 1.0)
    result = run_backtest(
        v6.strategy,
        panel,
        v6.strategy.to_universe(),
        initial_cash=Decimal("100000"),
        decision_overlay=history.overlay,
    )
    assert result.sessions


def test_misaligned_overlay_fails_closed(v6, panel) -> None:
    bad = DecisionOverlay(
        risk_multipliers=np.ones((len(panel) - 1, len(panel.symbols))),
        allow_increase=np.ones((len(panel) - 1, len(panel.symbols)), dtype=bool),
    )
    with pytest.raises(DataIntegrityError, match="risk multipliers have shape"):
        run_backtest(v6.strategy, panel, v6.strategy.to_universe(), decision_overlay=bad)


def test_low_liquidity_blocks_increases_without_zeroing_the_risk_matrix(v6, panel) -> None:
    illiquid = replace(panel, session_volumes=np.ones_like(panel.session_volumes))
    history = build_asset_regime_history(illiquid, v6.asset_regime, v6.liquidity)
    assert not np.any(history.overlay.allow_increase[-50:])
    assert np.any(history.overlay.risk_multipliers[-50:] > 0.0)
