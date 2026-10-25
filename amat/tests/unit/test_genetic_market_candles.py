"""Research worlds preserve observed candle geometry and declare simulated opens."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.domain.types import Symbol
from amat.validation.montecarlo import bootstrap_panel
from autoresearch.market_scenarios import SCENARIOS, panel_digest, scenario_metadata, scenario_panel

FIRST = 80
COUNT = 230
SERIES = (
    "adjusted_closes",
    "raw_closes",
    "window_vwaps",
    "window_volumes",
    "adjusted_highs",
    "adjusted_lows",
    "session_volumes",
    "adjusted_opens",
)


@pytest.fixture
def source():
    t = np.arange(COUNT)[:, None]
    closes = 100 * np.exp(t * np.array([0.0002, -0.0001]) + 0.01 * np.sin(t / 3))
    opens = closes * (1 + 0.004 * np.sin(t * np.array([0.9, 0.7])))
    return MarketPanel(
        sessions=tuple(dt.date(2024, 1, 1) + dt.timedelta(days=int(i)) for i in range(COUNT)),
        symbols=(Symbol("SPY"), Symbol("TLT")),
        adjusted_closes=closes,
        raw_closes=closes * 2,
        window_vwaps=closes * 1.997,
        window_volumes=np.full_like(closes, 1000),
        source="observed-candle-fixture",
        adjusted_highs=np.maximum(closes, opens) * 1.005,
        adjusted_lows=np.minimum(closes, opens) * 0.994,
        session_volumes=np.full_like(closes, 10000),
        adjusted_opens=opens,
    )


def assert_coherent(panel):
    opens, highs, lows, closes = panel.candle_inputs()
    assert all(
        np.isfinite(array).all() and (array > 0).all() for array in (opens, highs, lows, closes)
    )
    assert (highs >= np.maximum(opens, closes)).all()
    assert (lows <= np.minimum(opens, closes)).all()


@pytest.mark.parametrize("kind", SCENARIOS)
def test_modeled_candles_are_coherent_causal_and_prefix_consistent(source, kind):
    world = scenario_panel(source, first=FIRST, kind=kind, seed=12)
    assert_coherent(world)
    assert "version=3" in world.source
    for name in ("adjusted_opens", "adjusted_highs", "adjusted_lows", "adjusted_closes"):
        np.testing.assert_array_equal(getattr(world, name)[:FIRST], getattr(source, name)[:FIRST])
    corrupt = {name: getattr(source, name).copy() for name in SERIES}
    for array in corrupt.values():
        array[FIRST:] = np.nan
    future_changed = scenario_panel(replace(source, **corrupt), first=FIRST, kind=kind, seed=12)
    assert panel_digest(world) == panel_digest(future_changed)
    short_source = replace(
        source,
        sessions=source.sessions[:160],
        **{name: getattr(source, name)[:160] for name in SERIES},
    )
    short = scenario_panel(short_source, first=FIRST, kind=kind, seed=12)
    for name in SERIES:
        np.testing.assert_array_equal(getattr(world, name)[:160], getattr(short, name))
    legacy = scenario_panel(replace(source, adjusted_opens=None), first=FIRST, kind=kind, seed=12)
    for name in (
        "adjusted_closes",
        "raw_closes",
        "window_vwaps",
        "session_volumes",
        "window_volumes",
    ):
        np.testing.assert_array_equal(getattr(world, name), getattr(legacy, name))
    assert legacy.adjusted_opens is None
    assert "version=2" in legacy.source


def test_metadata_and_digest_bind_candle_model_and_observations(source):
    assert scenario_metadata("bull", count=150)["generator_version"] == 2
    metadata = scenario_metadata("bull", count=150, with_candles=True)
    assert metadata["generator_version"] == 3
    assert "Gaussian displacement" in metadata["candle_open_model"]
    changed = source.adjusted_opens.copy()
    changed[0, 0] *= 1.001
    assert panel_digest(source) != panel_digest(replace(source, adjusted_opens=changed))
    assert panel_digest(source) != panel_digest(replace(source, adjusted_opens=None))


def test_scenario_does_not_fabricate_observed_or_unsupported_opens(source):
    opens = source.adjusted_opens.copy()
    opens[:, 0] = np.nan
    opens[5, 1] = np.nan
    world = scenario_panel(replace(source, adjusted_opens=opens), first=FIRST, kind="bull", seed=4)
    assert np.isnan(world.adjusted_opens[:, 0]).all()
    assert np.isnan(world.adjusted_opens[5, 1])
    assert np.isfinite(world.adjusted_opens[FIRST:, 1]).all()
    assert np.isfinite(world.adjusted_highs).all()
    assert not np.array_equal(world.adjusted_opens[FIRST:, 1], world.window_vwaps[FIRST:, 1])


def test_bootstrap_samples_joint_ohlc_geometry_and_preserves_initial_open(source):
    world = bootstrap_panel(source, seed=34, mean_block_sessions=5)
    assert_coherent(world)
    np.testing.assert_array_equal(world.adjusted_opens[0], source.adjusted_opens[0])
    original = np.concatenate(
        [
            source.adjusted_closes[1:] / source.adjusted_closes[:-1],
            source.adjusted_opens[1:] / source.adjusted_closes[1:],
            source.adjusted_highs[1:] / source.adjusted_closes[1:],
            source.adjusted_lows[1:] / source.adjusted_closes[1:],
        ],
        axis=1,
    )
    sampled = np.concatenate(
        [
            world.adjusted_closes[1:] / world.adjusted_closes[:-1],
            world.adjusted_opens[1:] / world.adjusted_closes[1:],
            world.adjusted_highs[1:] / world.adjusted_closes[1:],
            world.adjusted_lows[1:] / world.adjusted_closes[1:],
        ],
        axis=1,
    )
    for row in sampled:
        assert np.abs(original - row).max(axis=1).min() < 1e-12
    assert "candle_version=1" in world.source
    assert panel_digest(world) == panel_digest(
        bootstrap_panel(source, seed=34, mean_block_sessions=5)
    )
    legacy = bootstrap_panel(replace(source, adjusted_opens=None), seed=34, mean_block_sessions=5)
    assert legacy.adjusted_opens is None
    assert "candle_version" not in legacy.source
    for name in SERIES[:-1]:
        np.testing.assert_array_equal(getattr(world, name), getattr(legacy, name))


def test_bootstrap_preserves_unavailable_opens_and_split_adjustment(source):
    opens = source.adjusted_opens.copy()
    opens[::2] = np.nan
    factors = np.ones_like(source.raw_closes)
    factors[:100] = 0.5
    changed = replace(
        source,
        adjusted_opens=opens,
        raw_closes=source.adjusted_closes / factors,
        window_vwaps=source.adjusted_closes * 0.999 / factors,
    )
    world = bootstrap_panel(changed, seed=6)
    assert np.isnan(world.adjusted_opens[0]).all()
    assert np.isnan(world.adjusted_opens[1:]).any()
    assert np.isfinite(world.adjusted_opens[1:]).any()
    np.testing.assert_allclose(world.window_vwaps[1:] / world.raw_closes[1:], 0.999)
