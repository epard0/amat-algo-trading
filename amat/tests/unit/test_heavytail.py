"""The heavy-tailed regime-switching generator and its study.

These tests assert the properties the study's conclusions depend on. A
generator that quietly produced Gaussian tails, lost the calibrated
volatilities, or ignored its seed would still "run" and still print a table --
and the table would be worthless. Each test below pins one of those.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from amat.validation.heavytail import (
    HEAVY_TAIL_SOURCE,
    REGIMES,
    MarketCalibration,
    _pareto_magnitudes,
    calibrate_market,
    generate_heavy_tail_panel,
    run_heavy_tail_study,
)

SESSIONS = 520


@pytest.fixture(scope="module")
def strategy():
    return load_strategy_config("configs/strategy/amat_v1.toml").strategy


@pytest.fixture(scope="module")
def source_panel(strategy):
    """A stand-in for the real snapshot: only its statistics are used."""
    return generate_panel(strategy.to_universe(), sessions=800, seed=11, trend_strength=0.3)


@pytest.fixture(scope="module")
def calibration(source_panel) -> MarketCalibration:
    return calibrate_market(source_panel)


class TestCalibration:
    def test_it_measures_one_parameter_set_per_symbol(self, calibration, strategy) -> None:
        expected = tuple(str(symbol) for symbol in strategy.universe.symbols)
        assert calibration.symbols == expected
        assert calibration.last_prices.shape == (len(expected),)
        assert calibration.annualized_volatility.shape == (len(expected),)
        assert calibration.market_betas.shape == (len(expected),)
        assert calibration.correlation.shape == (len(expected), len(expected))

    def test_starting_prices_are_the_panels_own_last_closes(
        self, calibration, source_panel
    ) -> None:
        """The "realistic levels" claim, asserted rather than assumed."""
        np.testing.assert_allclose(calibration.last_prices, source_panel.adjusted_closes[-1])

    def test_the_correlation_matrix_is_usable_for_cholesky(self, calibration) -> None:
        # A correlation that cannot be factored would break the generator at
        # run time; repairing it is the whole reason _psd_correlation exists.
        np.linalg.cholesky(calibration.correlation)
        np.testing.assert_allclose(np.diag(calibration.correlation), 1.0)
        assert np.all(np.abs(calibration.correlation) <= 1.0 + 1e-12)

    def test_a_short_panel_is_refused(self, strategy) -> None:
        short = generate_panel(strategy.to_universe(), sessions=100, seed=3)
        with pytest.raises(DataIntegrityError, match="at least 252 sessions"):
            calibrate_market(short)


class TestPareto:
    def test_magnitudes_respect_the_minimum_and_get_heavier_as_alpha_falls(self) -> None:
        rng = np.random.default_rng(7)
        heavy = _pareto_magnitudes(rng, (200_000,), tail_index=2.0)
        rng = np.random.default_rng(7)
        light = _pareto_magnitudes(rng, (200_000,), tail_index=6.0)

        assert heavy.min() >= 1.0
        assert light.min() >= 1.0
        # Same uniforms, different alpha: the heavier tail must dominate
        # pointwise, which is a stronger statement than comparing moments.
        assert np.all(heavy >= light - 1e-12)
        assert np.percentile(heavy, 99.9) > np.percentile(light, 99.9) * 3

    def test_the_tail_is_far_heavier_than_a_gaussian(self) -> None:
        """The point of the module: 6-sigma sessions must be reachable.

        Under a standard normal a 6-sigma draw has probability ~1e-9, so it
        does not occur in a sample of this size. The Pareto jumps must produce
        them readily, otherwise the "worse than anything in the sample"
        premise is false.
        """
        rng = np.random.default_rng(20260815)
        magnitudes = _pareto_magnitudes(rng, (100_000,), tail_index=2.5)
        assert np.mean(magnitudes > 6.0) > 0.005
        assert magnitudes.max() > 20.0


class TestGeneratedPanel:
    def test_it_is_deterministic_in_the_seed(self, calibration) -> None:
        first, regimes_a, jumps_a = generate_heavy_tail_panel(
            calibration, seed=5, sessions=SESSIONS
        )
        second, regimes_b, jumps_b = generate_heavy_tail_panel(
            calibration, seed=5, sessions=SESSIONS
        )
        np.testing.assert_array_equal(first.adjusted_closes, second.adjusted_closes)
        np.testing.assert_array_equal(regimes_a, regimes_b)
        np.testing.assert_array_equal(jumps_a, jumps_b)

    def test_a_different_seed_gives_a_different_market(self, calibration) -> None:
        first, _, _ = generate_heavy_tail_panel(calibration, seed=5, sessions=SESSIONS)
        other, _, _ = generate_heavy_tail_panel(calibration, seed=6, sessions=SESSIONS)
        assert not np.allclose(first.adjusted_closes, other.adjusted_closes)

    def test_the_panel_declares_its_synthetic_origin(self, calibration) -> None:
        panel, _, _ = generate_heavy_tail_panel(calibration, seed=5, sessions=SESSIONS)
        assert panel.source == HEAVY_TAIL_SOURCE
        assert "synthetic" in panel.source

    def test_prices_are_positive_and_start_at_the_calibrated_level(self, calibration) -> None:
        panel, _, _ = generate_heavy_tail_panel(calibration, seed=9, sessions=SESSIONS)
        assert np.all(panel.adjusted_closes > 0.0)
        assert np.all(np.isfinite(panel.adjusted_closes))
        # First session is one step from the calibrated level, so it must be
        # the same order of magnitude -- this is what keeps prices "realistic"
        # rather than an arbitrary index starting at 1.0.
        ratio = panel.adjusted_closes[0] / calibration.last_prices
        assert np.all(ratio > 0.5)
        assert np.all(ratio < 2.0)

    def test_the_session_range_brackets_the_close(self, calibration) -> None:
        panel, _, _ = generate_heavy_tail_panel(calibration, seed=9, sessions=SESSIONS)
        assert panel.adjusted_highs is not None
        assert panel.adjusted_lows is not None
        assert np.all(panel.adjusted_highs >= panel.adjusted_closes - 1e-9)
        assert np.all(panel.adjusted_lows <= panel.adjusted_closes + 1e-9)

    def test_per_symbol_volatility_is_reproduced_not_flattened(self, calibration) -> None:
        """A calm symbol must stay calm and a violent one stay violent.

        This is the "weighted" requirement, and it is tested against a
        hand-built calibration with a deliberate 6x volatility spread rather
        than against the module fixture: ``generate_panel`` draws every symbol
        from one ``annual_volatility``, so a calibration derived from it has
        almost no dispersion to preserve and the assertion would pass or fail
        on noise. Here the target dispersion is known exactly.
        """
        count = len(calibration.symbols)
        targets = np.linspace(0.05, 0.30, count)
        spread = MarketCalibration(
            symbols=calibration.symbols,
            last_prices=np.full(count, 100.0),
            annualized_volatility=targets,
            market_betas=np.ones(count),
            correlation=np.eye(count),
            source_snapshot="unit-test",
            first_session=calibration.first_session,
            last_session=calibration.last_session,
            sessions_observed=calibration.sessions_observed,
        )
        # A light tail isolates the diffusion: jumps are what the other tests
        # measure, and at alpha 6 they cannot dominate the realized estimate.
        panel, _, _ = generate_heavy_tail_panel(spread, seed=4, sessions=4000, tail_index=6.0)
        realized = np.diff(np.log(panel.adjusted_closes), axis=0).std(axis=0) * np.sqrt(252.0)

        # Ordering is preserved across the whole cross-section, not just at the
        # extremes: Spearman-style monotonicity via rank equality.
        assert list(np.argsort(realized)) == list(np.argsort(targets))
        # And the levels land near their targets rather than merely ordering
        # correctly -- 25% tolerance absorbs the regime multipliers and jumps.
        np.testing.assert_allclose(realized, targets, rtol=0.25)

    def test_regimes_visit_more_than_one_state_and_only_declared_ones(self, calibration) -> None:
        _, regimes, _ = generate_heavy_tail_panel(calibration, seed=2, sessions=3000)
        assert set(np.unique(regimes)).issubset(set(range(len(REGIMES))))
        assert len(np.unique(regimes)) > 1

    def test_jump_sessions_are_sparse_but_present(self, calibration) -> None:
        _, _, jumps = generate_heavy_tail_panel(calibration, seed=2, sessions=3000)
        share = float(np.mean(jumps != 0.0))
        assert 0.0 < share < 0.25

    def test_too_few_sessions_is_refused(self, calibration) -> None:
        with pytest.raises(DataIntegrityError, match="exceed one year of warm-up"):
            generate_heavy_tail_panel(calibration, seed=1, sessions=100)

    def test_a_non_positive_tail_index_is_refused(self, calibration) -> None:
        with pytest.raises(DataIntegrityError, match="tail_index must be strictly positive"):
            generate_heavy_tail_panel(calibration, seed=1, sessions=SESSIONS, tail_index=0.0)

    def test_the_calendar_is_strictly_increasing(self, calibration) -> None:
        panel, _, _ = generate_heavy_tail_panel(
            calibration, seed=1, sessions=SESSIONS, start=dt.date(2020, 1, 2)
        )
        assert list(panel.sessions) == sorted(set(panel.sessions))
        assert len(panel.sessions) == SESSIONS


class TestStudy:
    def test_it_runs_the_strategy_and_reports_a_passive_control(
        self, strategy, calibration
    ) -> None:
        study = run_heavy_tail_study(
            strategy,
            calibration,
            scenarios=2,
            sessions=SESSIONS,
            initial_cash=Decimal("100000"),
            seed=31,
        )
        assert len(study.outcomes) == 2
        for outcome in study.outcomes:
            assert outcome.strategy.sessions > 0
            # The control must be a real number, not a placeholder: every
            # scenario's verdict is read against it.
            assert np.isfinite(outcome.buy_and_hold_total_return)
            assert outcome.buy_and_hold_max_drawdown <= 0.0
            assert outcome.strategy.max_drawdown <= 0.0

    def test_the_artifact_carries_the_synthetic_caveat(self, strategy, calibration) -> None:
        study = run_heavy_tail_study(strategy, calibration, scenarios=1, sessions=SESSIONS, seed=41)
        payload = study.as_dict()
        assert any("SYNTHETIC" in caveat for caveat in payload["caveats"])
        assert payload["process"]["tail_index"] == study.tail_index
        assert payload["calibration"]["symbols"] == list(calibration.symbols)
        assert payload["summary"]["scenarios"] == 1

    def test_a_universe_mismatch_fails_closed(self, strategy, calibration) -> None:
        """Calibration and strategy must describe the same universe.

        Running v6's eleven symbols against a twelve-symbol calibration would
        silently misalign every column, so it is refused by name.
        """
        trimmed = MarketCalibration(
            symbols=calibration.symbols[:-1],
            last_prices=calibration.last_prices[:-1],
            annualized_volatility=calibration.annualized_volatility[:-1],
            market_betas=calibration.market_betas[:-1],
            correlation=calibration.correlation[:-1, :-1],
            source_snapshot=calibration.source_snapshot,
            first_session=calibration.first_session,
            last_session=calibration.last_session,
            sessions_observed=calibration.sessions_observed,
        )
        with pytest.raises(DataIntegrityError, match="do not match the strategy universe"):
            run_heavy_tail_study(strategy, trimmed, scenarios=1, sessions=SESSIONS)

    def test_zero_scenarios_is_refused(self, strategy, calibration) -> None:
        with pytest.raises(DataIntegrityError, match="scenarios must be strictly positive"):
            run_heavy_tail_study(strategy, calibration, scenarios=0, sessions=SESSIONS)

    def test_the_study_is_reproducible_from_its_seed(self, strategy, calibration) -> None:
        first = run_heavy_tail_study(strategy, calibration, scenarios=2, sessions=SESSIONS, seed=77)
        second = run_heavy_tail_study(
            strategy, calibration, scenarios=2, sessions=SESSIONS, seed=77
        )
        assert [o.strategy.total_return for o in first.outcomes] == [
            o.strategy.total_return for o in second.outcomes
        ]
