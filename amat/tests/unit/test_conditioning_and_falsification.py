"""Regime conditioning: causal by construction, and falsifiable by design.

The v2 overlay could not be tested this way. Its conditioning lived inside the
session loop alongside guard state, a Kelly estimate and a trade ledger, so
"what did the regime component do on 2020-03-16" had no answer that did not
involve replaying from inception, and the regime part could not be separated
from the guard part when the whole thing lost 94% of v1's return.

v3 conditions through a precomputed ``(T,)`` array. That single change is what
makes both tests below possible: a leak test can mutate future rows and compare
arrays directly, and a control can substitute a series that is worthless by
construction while holding everything else identical.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from amat.validation.conditioning import regime_scalars, session_features
from amat.validation.falsification import falsify_conditioning, matched_controls

SESSIONS = 700


@pytest.fixture(scope="module")
def strategy(canonical_config_path):
    return load_strategy_config(canonical_config_path).strategy


@pytest.fixture(scope="module")
def panel(strategy):
    return generate_panel(strategy.to_universe(), sessions=SESSIONS, seed=3)


@pytest.fixture(scope="module")
def scalars(panel):
    return regime_scalars(panel, fit_window=252, refit_every=21)


class TestTheScalarSeriesIsCausal:
    def test_mutating_future_rows_leaves_earlier_scalars_identical(self, panel) -> None:
        """The leak test the v2 overlay could not support.

        Rows after the cut are multiplied by three. Every scalar at or before the
        cut must be bit-identical, because none of them is entitled to see those
        rows. A single full-sample fit would fail this through the
        standardization alone, which is the most common way a regime study leaks
        without anyone noticing.
        """
        cut = 500
        mutated_closes = panel.adjusted_closes.copy()
        mutated_closes[cut + 1 :] *= 3.0
        mutated_highs = panel.adjusted_highs.copy()
        mutated_highs[cut + 1 :] *= 3.0
        mutated_lows = panel.adjusted_lows.copy()
        mutated_lows[cut + 1 :] *= 3.0

        mutated = type(panel)(
            sessions=panel.sessions,
            symbols=panel.symbols,
            adjusted_closes=mutated_closes,
            raw_closes=panel.raw_closes,
            window_vwaps=panel.window_vwaps,
            window_volumes=panel.window_volumes,
            source=panel.source,
            adjusted_highs=mutated_highs,
            adjusted_lows=mutated_lows,
            session_volumes=panel.session_volumes,
        )

        original = regime_scalars(panel, fit_window=252, refit_every=21)
        perturbed = regime_scalars(mutated, fit_window=252, refit_every=21)
        assert np.array_equal(original[: cut + 1], perturbed[: cut + 1])

    def test_the_series_is_deterministic(self, panel) -> None:
        first = regime_scalars(panel, fit_window=252, refit_every=21)
        second = regime_scalars(panel, fit_window=252, refit_every=21)
        assert np.array_equal(first, second)

    def test_every_scalar_is_a_reduction_never_an_addition(self, scalars) -> None:
        """The contract forbids re-levering past the gross cap. A conditioning
        multiplier above 1.0 would do exactly that through the back door."""
        assert np.all(scalars > 0.0)
        assert np.all(scalars <= 1.0)

    def test_unknown_means_full_size_not_smallest_size(self, panel) -> None:
        """Before a regime can be established there is no estimate. Treating "no
        estimate" as "worst estimate" held the whole warm-up at quarter size in
        an earlier design, which is a large unforced cost."""
        early = regime_scalars(panel, fit_window=252, refit_every=21)[:100]
        assert np.all(early == 1.0)

    def test_a_panel_without_context_inputs_fails_closed(self, strategy, panel) -> None:
        bare = type(panel)(
            sessions=panel.sessions,
            symbols=panel.symbols,
            adjusted_closes=panel.adjusted_closes,
            raw_closes=panel.raw_closes,
            window_vwaps=panel.window_vwaps,
            window_volumes=panel.window_volumes,
            source=panel.source,
        )
        with pytest.raises(DataIntegrityError, match="highs, lows and volume"):
            regime_scalars(bare)


class TestSessionFeatures:
    def test_both_moments_are_kept(self, panel) -> None:
        """Mean and dispersion. A market where everything rises together and one
        where half the universe is falling can share a mean and are different
        states; keeping only the mean would merge them."""
        features = session_features(panel)
        assert features.shape[0] == len(panel)
        assert features.shape[1] % 2 == 0
        half = features.shape[1] // 2
        finite = np.isfinite(features).all(axis=1)
        assert finite.any(), "no session produced a complete feature row"
        means, spreads = features[finite, :half], features[finite, half:]
        assert not np.allclose(means, spreads)


class TestTheRunnerAppliesTheScalar:
    def test_a_series_of_ones_reproduces_v1_exactly(self, strategy, panel) -> None:
        """v1 is v3 with a scalar of 1. If this drifts, every v1-vs-v3
        comparison is measuring the plumbing rather than the conditioning."""
        universe = strategy.to_universe()
        plain = run_backtest(strategy, panel, universe, initial_cash=Decimal("1200000"))
        ones = run_backtest(
            strategy,
            panel,
            universe,
            initial_cash=Decimal("1200000"),
            weight_scalars=np.ones(len(panel)),
        )
        assert plain.final_equity == ones.final_equity
        assert plain.total_fills == ones.total_fills

    def test_a_constant_half_scalar_reduces_gross_exposure(self, strategy, panel) -> None:
        universe = strategy.to_universe()
        full = run_backtest(strategy, panel, universe, initial_cash=Decimal("1200000"))
        halved = run_backtest(
            strategy,
            panel,
            universe,
            initial_cash=Decimal("1200000"),
            weight_scalars=np.full(len(panel), 0.5),
        )
        full_gross = np.mean([record.gross_exposure for record in full.sessions])
        half_gross = np.mean([record.gross_exposure for record in halved.sessions])
        assert half_gross < full_gross

    def test_a_misaligned_series_is_refused(self, strategy, panel) -> None:
        """The dangerous case: a wrong-length series conditions every session on
        some other session's regime and still produces a plausible curve."""
        with pytest.raises(DataIntegrityError, match="expected"):
            run_backtest(
                strategy,
                panel,
                strategy.to_universe(),
                weight_scalars=np.ones(len(panel) - 5),
            )

    def test_a_scalar_above_one_is_refused(self, strategy, panel) -> None:
        with pytest.raises(DataIntegrityError, match=r"\[0, 1\]"):
            run_backtest(
                strategy,
                panel,
                strategy.to_universe(),
                weight_scalars=np.full(len(panel), 1.5),
            )


class TestTheControlsAreActuallyMatched:
    def test_the_shuffled_control_preserves_the_distribution(self, scalars) -> None:
        """It must differ from the candidate only in *timing*. If it also
        differed in mean, beating it would prove nothing about timing."""
        control = matched_controls(scalars, seed=1)["shuffled"]
        assert np.allclose(np.sort(control), np.sort(scalars))
        assert control.mean() == pytest.approx(scalars.mean())

    def test_the_static_control_costs_no_conditioning_turnover(self, scalars) -> None:
        control = matched_controls(scalars, seed=1)["static_mean"]
        assert len(np.unique(control)) == 1
        assert control.mean() == pytest.approx(scalars.mean())

    def test_the_random_control_uses_the_candidates_own_levels(self, scalars) -> None:
        control = matched_controls(scalars, seed=1)["random_matched_turnover"]
        assert set(np.unique(control)).issubset({float(scalars.min()), float(scalars.max())})

    def test_controls_are_reproducible_for_a_seed(self, scalars) -> None:
        first = matched_controls(scalars, seed=7)
        second = matched_controls(scalars, seed=7)
        for name, series in first.items():
            assert np.array_equal(series, second[name])


class TestTheVerdictCannotFlatter:
    def test_failing_to_beat_the_baseline_is_a_rejection(self, strategy, panel) -> None:
        """A conditioning series that only ever cuts must lose to v1 on this
        synthetic panel, and the verdict must say so rather than hedging."""
        universe = strategy.to_universe()
        always_half = np.full(len(panel), 0.5)
        result = falsify_conditioning(
            strategy,
            panel,
            universe,
            always_half,
            initial_cash=Decimal("1200000"),
            seed=2,
        )
        assert result.candidate.mean_scalar == pytest.approx(0.5)
        # Whatever the direction, the verdict must be consistent with the
        # measured Sharpes rather than asserted independently of them.
        if result.candidate.sharpe <= result.baseline.sharpe:
            assert result.verdict.startswith("REJECTED")
        beaten = [c for c in result.controls if c.sharpe >= result.candidate.sharpe]
        if beaten and result.candidate.sharpe > result.baseline.sharpe:
            assert result.verdict.startswith("REJECTED")

    def test_survival_is_not_described_as_success(self, strategy, panel) -> None:
        """Language control. "Survives the controls" is not "works", and the
        promotion gates require walk-forward evidence this suite cannot give."""
        universe = strategy.to_universe()
        result = falsify_conditioning(
            strategy,
            panel,
            universe,
            np.full(len(panel), 0.9),
            initial_cash=Decimal("1200000"),
            seed=4,
        )
        verdict = result.verdict.lower()
        for banned in ("proven", "profitable", "works", "edge confirmed"):
            assert banned not in verdict
