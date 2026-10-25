"""Adaptive rotation must remain causal, calendar-aware, bounded and opt-in."""

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.errors import DataIntegrityError
from amat.portfolio import decide, precompute_decision_inputs
from amat.portfolio.adaptive import rotation_inputs
from amat.portfolio.cross_section import select_on_schedule


@pytest.fixture
def fixture():
    base = load_strategy_config(Path("configs/strategy/amat_v13_return_target.toml")).strategy
    dates = []
    date = dt.date(2020, 1, 6)
    while len(dates) < 390:
        if date.weekday() < 5:
            dates.append(date)
        date += dt.timedelta(days=1)
    rng = np.random.default_rng(26)
    prices = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.008, (390, 11)), axis=0))
    return base, dates, prices


def config(base, **flags):
    value = base.model_dump(mode="json")
    value["adaptive_rotation"] = {"enabled": True, **flags}
    return StrategyConfig.model_validate(value)


def test_neutral_adaptive_selector_exactly_matches_v13_membership(fixture):
    base, dates, prices = fixture
    cache = precompute_decision_inputs(config(base), prices, sessions=dates)
    for t in range(252, len(prices)):
        expected = select_on_schedule(
            cache.long_scores,
            cache.sigma_panel,
            base.cross_sectional,
            decision_index=t,
            first_decision_index=252,
            horizon_scores=cache.horizon_scores,
        )
        np.testing.assert_array_equal(cache.adaptive_scores[t], expected)
    assert np.all(cache.adaptive_scalars == 1)
    assert "adaptive_rotation" not in base.model_dump(mode="json")


def test_future_mutation_and_prefix_cache_are_identical(fixture):
    base, dates, prices = fixture
    strategy = config(
        base, group_selection=True, residual_ranking=True, regime_overlay=True, hysteresis=True
    )
    original = precompute_decision_inputs(strategy, prices, sessions=dates)
    prefix = precompute_decision_inputs(strategy, prices[:343], sessions=dates[:343])
    changed = prices.copy()
    changed[343:] *= np.linspace(1, 4, len(changed) - 343)[:, None]
    mutated = precompute_decision_inputs(strategy, changed, sessions=dates)
    np.testing.assert_array_equal(original.adaptive_scores[:343], prefix.adaptive_scores)
    np.testing.assert_array_equal(original.adaptive_scores[:343], mutated.adaptive_scores[:343])
    np.testing.assert_array_equal(original.adaptive_scalars[:343], mutated.adaptive_scalars[:343])


def test_partial_week_price_spike_does_not_enter_completed_week_ranking(fixture):
    base, dates, prices = fixture
    value = config(base, residual_ranking=True).model_dump(mode="json")
    value["cross_sectional"]["selection_interval_sessions"] = 1
    strategy = StrategyConfig.model_validate(value)
    t = next(i for i in range(310, 330) if dates[i].weekday() == 2)
    scores = np.ones_like(prices)
    sigma = np.ones_like(prices)
    horizons = np.ones((3, *prices.shape))
    before, _ = rotation_inputs(
        strategy,
        prices[: t + 1],
        scores[: t + 1],
        sigma[: t + 1],
        horizons[:, : t + 1],
        sessions=dates[: t + 1],
    )
    changed = prices[: t + 1].copy()
    changed[t, 0] *= 10
    after, _ = rotation_inputs(
        strategy,
        changed,
        scores[: t + 1],
        sigma[: t + 1],
        horizons[:, : t + 1],
        sessions=dates[: t + 1],
    )
    np.testing.assert_array_equal(before, after)


def test_shock_reduces_after_sizing_without_waiting_for_refresh(fixture):
    base, dates, prices = fixture
    benchmark = list(base.universe.symbols).index("QQQ")
    prices[333:, benchmark] *= 0.85
    strategy = config(base, regime_overlay=True)
    cache = precompute_decision_inputs(strategy, prices, sessions=dates)
    neutral = config(base)
    neutral_cache = precompute_decision_inputs(neutral, prices, sessions=dates)
    actual = decide(strategy, prices, strategy.to_universe(), decision_index=333, precomputed=cache)
    reference = decide(
        neutral, prices, neutral.to_universe(), decision_index=333, precomputed=neutral_cache
    )
    assert cache.adaptive_scalars[333] == 0.3
    np.testing.assert_allclose(actual.weights, reference.weights * 0.3, rtol=0, atol=1e-15)
    assert actual.cash_weight == pytest.approx(1 - actual.weights.sum())


def test_groups_positions_and_cash_stay_bounded(fixture):
    base, dates, prices = fixture
    strategy = config(base, group_selection=True, residual_ranking=True, hysteresis=True)
    cache = precompute_decision_inputs(strategy, prices, sessions=dates)
    symbols = list(base.universe.symbols)
    for t in range(252, 390):
        chosen = {symbols[i] for i in np.flatnonzero(cache.adaptive_scores[t])}
        active = [g for g in strategy.adaptive_rotation.groups if chosen.intersection(g.symbols)]
        assert len(chosen) <= 4 and len(active) <= 2
        assert all(len(chosen.intersection(g.symbols)) <= 2 for g in active)
    allocation = decide(
        strategy, prices, strategy.to_universe(), decision_index=389, precomputed=cache
    )
    assert 0 <= allocation.gross_exposure <= 1 and 0 <= allocation.cash_weight <= 1
    assert np.all(allocation.weights <= 0.45 + 1e-12)


def test_missing_calendar_and_bad_prices_are_refused(fixture):
    base, dates, prices = fixture
    strategy = config(base, group_selection=True)
    with pytest.raises(DataIntegrityError, match="sessions"):
        precompute_decision_inputs(strategy, prices)
    prices[310, 0] = np.nan
    scores = np.ones_like(prices)
    with pytest.raises(DataIntegrityError, match="aligned"):
        rotation_inputs(strategy, prices, scores, scores, None, sessions=dates)


def test_holiday_week_waits_until_the_following_exchange_week(fixture):
    base, dates, prices = fixture
    # Simulate a Friday holiday by removing it from the exchange calendar.
    holiday = next(i for i in range(310, 330) if dates[i].weekday() == 4)
    keep = [i for i in range(len(dates)) if i != holiday]
    dates = [dates[i] for i in keep]
    prices = prices[keep]
    strategy = config(base, residual_ranking=True)
    values = strategy.model_dump(mode="json")
    values["cross_sectional"]["selection_interval_sessions"] = 1
    strategy = StrategyConfig.model_validate(values)
    scores = np.ones_like(prices)
    horizons = np.ones((3, *prices.shape))
    thursday = holiday - 1
    original, _ = rotation_inputs(
        strategy,
        prices[: thursday + 1],
        scores[: thursday + 1],
        scores[: thursday + 1],
        horizons[:, : thursday + 1],
        sessions=dates[: thursday + 1],
    )
    changed = prices[: thursday + 1].copy()
    changed[-1, 0] *= 3
    altered, _ = rotation_inputs(
        strategy,
        changed,
        scores[: thursday + 1],
        scores[: thursday + 1],
        horizons[:, : thursday + 1],
        sessions=dates[: thursday + 1],
    )
    np.testing.assert_array_equal(original, altered)


def test_misdeclared_groups_and_missing_benchmark_are_refused(fixture):
    from pydantic import ValidationError

    base, _, _ = fixture
    with pytest.raises(ValidationError, match="partition"):
        config(base, benchmark="MISSING")
    with pytest.raises(ValidationError, match="partition"):
        config(base, groups=[{"name": "incomplete", "symbols": ["SPY"]}])


def test_archived_combined_recipe_keeps_all_components(fixture):
    from autoresearch.improvements.adaptive_study import recipes

    base, _, _ = fixture
    value = recipes(base)["combined"]["adaptive_rotation"]
    assert all(
        value[k]
        for k in ["enabled", "group_selection", "residual_ranking", "regime_overlay", "hysteresis"]
    )
    # Rejected profiles are removed from the selectable strategy catalog.
    assert not Path("configs/strategy/amat_v14_adaptive_rotation.toml").exists()
    assert not Path("configs/strategy/amat_group_selection_daily_quality.toml").exists()
