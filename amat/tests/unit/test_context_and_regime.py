"""Causality and correctness of the context and regime layers.

Support/resistance, reversal patterns, and regime labels are the classic places
look-ahead enters a research codebase, because the natural implementations use
centred windows or full-sample statistics. These tests exist to make that
impossible to introduce silently.
"""

from __future__ import annotations

import numpy as np
import pytest

from amat.context import (
    build_context,
    distance_to_level,
    market_feature_history,
    mta_alignment,
    resistance_levels,
    reversal_score,
    rolling_extreme,
    support_levels,
    trend_state,
    volume_context,
)
from amat.errors import CausalityError, DataIntegrityError
from amat.regime import MIN_FIT_ROWS, CausalRegimeModel


@pytest.fixture
def panel() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A deterministic 600x4 panel: closes, highs, lows, volumes."""
    rng = np.random.default_rng(17)
    steps = rng.normal(0.0003, 0.012, size=(600, 4))
    closes = 100 * np.exp(np.cumsum(steps, axis=0))
    highs = closes * (1 + np.abs(rng.normal(0, 0.004, size=closes.shape)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.004, size=closes.shape)))
    volumes = rng.uniform(1e6, 5e6, size=closes.shape)
    return closes, highs, lows, volumes


class TestTrailingWindowsAreCausal:
    """Every context primitive must ignore rows after the one it reports on."""

    @pytest.mark.parametrize("mode", ["max", "min"])
    def test_rolling_extreme_is_causal(self, panel: tuple[np.ndarray, ...], mode: str) -> None:
        closes = panel[0]
        baseline = rolling_extreme(closes, 63, mode=mode)
        mutated = closes.copy()
        mutated[400:] *= 9.0
        after = rolling_extreme(mutated, 63, mode=mode)
        np.testing.assert_array_equal(baseline[:400], after[:400])

    def test_support_and_resistance_are_causal(self, panel: tuple[np.ndarray, ...]) -> None:
        _, highs, lows, _ = panel
        base_support = support_levels(lows, 63)
        base_resistance = resistance_levels(highs, 63)

        mutated_lows, mutated_highs = lows.copy(), highs.copy()
        mutated_lows[400:] *= 0.1
        mutated_highs[400:] *= 9.0

        np.testing.assert_array_equal(base_support[:400], support_levels(mutated_lows, 63)[:400])
        np.testing.assert_array_equal(
            base_resistance[:400], resistance_levels(mutated_highs, 63)[:400]
        )

    def test_trend_state_is_causal(self, panel: tuple[np.ndarray, ...]) -> None:
        closes = panel[0]
        baseline = trend_state(closes, 63)
        mutated = closes.copy()
        mutated[400:] *= 5.0
        after = trend_state(mutated, 63)
        np.testing.assert_array_equal(baseline.trend[:400], after.trend[:400])
        np.testing.assert_array_equal(baseline.location[:400], after.location[:400])

    def test_volume_context_is_causal(self, panel: tuple[np.ndarray, ...]) -> None:
        volumes = panel[3]
        base_z, base_trend = volume_context(volumes, 63)
        mutated = volumes.copy()
        mutated[400:] *= 50.0
        after_z, after_trend = volume_context(mutated, 63)
        np.testing.assert_array_equal(base_z[:400], after_z[:400])
        np.testing.assert_array_equal(base_trend[:400], after_trend[:400])

    def test_reversal_score_is_causal(self, panel: tuple[np.ndarray, ...]) -> None:
        closes, highs, lows, _ = panel
        baseline = reversal_score(closes, highs, lows)
        c2, h2, l2 = closes.copy(), highs.copy(), lows.copy()
        c2[400:] *= 3.0
        h2[400:] *= 3.0
        l2[400:] *= 0.3
        np.testing.assert_array_equal(baseline[:400], reversal_score(c2, h2, l2)[:400])

    def test_market_feature_history_is_causal(self, panel: tuple[np.ndarray, ...]) -> None:
        closes, highs, lows, volumes = panel
        baseline = market_feature_history(closes, highs, lows, volumes)
        c2, h2, l2, v2 = (array.copy() for array in panel)
        c2[400:] *= 4.0
        h2[400:] *= 4.0
        l2[400:] *= 0.25
        v2[400:] *= 20.0
        np.testing.assert_array_equal(baseline[:400], market_feature_history(c2, h2, l2, v2)[:400])

    def test_slicing_equals_recomputing_on_a_truncated_panel(
        self, panel: tuple[np.ndarray, ...]
    ) -> None:
        """The equivalence the regime model's precomputation depends on.

        Computing the history once over the full panel and slicing must equal
        recomputing it on the truncated panel. If this ever fails, precomputation
        becomes a leak and the runner must go back to per-row computation.
        """
        closes, highs, lows, volumes = panel
        cut = 450
        full = market_feature_history(closes, highs, lows, volumes)[:cut]
        truncated = market_feature_history(closes[:cut], highs[:cut], lows[:cut], volumes[:cut])
        np.testing.assert_allclose(full, truncated, rtol=1e-12, atol=1e-15, equal_nan=True)


class TestContextSemantics:
    def test_location_is_zero_at_the_low_and_one_at_the_high(self) -> None:
        prices = np.array([[10.0], [20.0], [30.0]])
        view = trend_state(prices, 2)
        assert view.location[2] == pytest.approx(1.0)

        falling = np.array([[30.0], [20.0], [10.0]])
        assert trend_state(falling, 2).location[2] == pytest.approx(0.0)

    def test_distance_to_level_sign(self) -> None:
        prices = np.array([[100.0]])
        below = distance_to_level(prices, np.array([[90.0]]))
        above = distance_to_level(prices, np.array([[110.0]]))
        assert below[0, 0] > 0
        assert above[0, 0] < 0

    def test_alignment_rewards_agreement_over_magnitude(self) -> None:
        """Unanimous-but-weak must outrank mixed-but-strong."""
        unanimous = trend_state(np.array([[1.0]]), 2)
        agreed = mta_alignment(
            (
                _view(0.3),
                _view(0.3),
                _view(0.3),
            )
        )
        mixed = mta_alignment((_view(0.9), _view(-0.8), _view(0.8)))
        assert agreed[0] > mixed[0]
        assert unanimous is not None

    def test_alignment_is_bounded(self) -> None:
        result = mta_alignment((_view(1.0), _view(1.0)))
        assert -1.0 <= result[0] <= 1.0

    def test_alignment_requires_at_least_one_view(self) -> None:
        with pytest.raises(DataIntegrityError, match="at least one"):
            mta_alignment(())

    def test_volume_z_is_positive_on_a_surge(self) -> None:
        volumes = np.full((70, 1), 1e6)
        volumes[-1, 0] = 5e6
        z, _ = volume_context(volumes, 63)
        assert z[-1, 0] > 3.0

    def test_build_context_matrix_matches_its_feature_names(
        self, panel: tuple[np.ndarray, ...]
    ) -> None:
        closes, highs, lows, volumes = panel
        snapshot = build_context(closes, highs, lows, volumes)
        assert snapshot.as_matrix().shape[1] == len(snapshot.feature_names)

    @pytest.mark.parametrize("bad_window", [0, -5])
    def test_invalid_windows_fail_closed(self, bad_window: int) -> None:
        with pytest.raises(DataIntegrityError):
            rolling_extreme(np.zeros((10, 1)), bad_window, mode="max")

    def test_invalid_mode_fails_closed(self) -> None:
        with pytest.raises(DataIntegrityError, match="mode must be"):
            rolling_extreme(np.zeros((10, 1)), 3, mode="median")


class TestRegimeCausality:
    @pytest.fixture
    def features_and_volatility(self) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(23)
        rows = 900
        calm = rng.normal(0.0, 1.0, size=(rows // 2, 6))
        stressed = rng.normal(2.5, 2.0, size=(rows - rows // 2, 6))
        features = np.vstack([calm, stressed])
        volatility = np.concatenate([np.full(rows // 2, 0.10), np.full(rows - rows // 2, 0.35)])
        return features, volatility

    def test_fit_ignores_rows_after_the_index(
        self, features_and_volatility: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """The decisive regime leakage test."""
        features, volatility = features_and_volatility
        cut = 600

        first = CausalRegimeModel(fit_window=504, seed=1)
        first.fit_through(features, volatility, index=cut)
        baseline = first.assess(features[cut])

        mutated = features.copy()
        mutated[cut + 1 :] = 999.0
        second = CausalRegimeModel(fit_window=504, seed=1)
        second.fit_through(mutated, volatility, index=cut)
        after = second.assess(mutated[cut])

        assert baseline.label == after.label
        assert baseline.confidence == pytest.approx(after.confidence)

    def test_labels_are_ordered_by_volatility(
        self, features_and_volatility: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """Label 0 must always mean the calmest state, across refits."""
        features, volatility = features_and_volatility
        model = CausalRegimeModel(fit_window=504, n_regimes=3, seed=3)
        model.fit_through(features, volatility, index=800)
        volatilities = [
            state.mean_volatility for state in sorted(model.states, key=lambda item: item.label)
        ]
        assert volatilities == sorted(volatilities)

    def test_label_ordering_is_stable_across_seeds(
        self, features_and_volatility: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """Without stable ordering, 'regime changed' would fire on every refit."""
        features, volatility = features_and_volatility
        labels = []
        for seed in (0, 1, 2, 7):
            model = CausalRegimeModel(fit_window=504, n_regimes=2, seed=seed)
            model.fit_through(features, volatility, index=800)
            labels.append(model.assess(features[800]).label)
        assert len(set(labels)) == 1

    def test_index_outside_the_panel_is_a_causality_error(
        self, features_and_volatility: tuple[np.ndarray, np.ndarray]
    ) -> None:
        features, volatility = features_and_volatility
        with pytest.raises(CausalityError, match="outside panel"):
            CausalRegimeModel().fit_through(features, volatility, index=10_000)

    def test_short_history_fails_closed(self) -> None:
        features = np.random.default_rng(0).normal(size=(300, 6))
        volatility = np.full(300, 0.2)
        with pytest.raises(DataIntegrityError, match="regime fit needs"):
            CausalRegimeModel(fit_window=504).fit_through(features, volatility, index=100)

    def test_unfitted_model_reports_unknown_at_the_unknown_scalar(self) -> None:
        """Updated deliberately: this previously asserted the risk *floor*.

        Tying the unfitted model's multiplier to the floor made "no regime
        estimate" indistinguishable from "worst measured regime", which held
        every run at quarter size for its entire 504-session warm-up. The two
        values are now separate settings; see
        ``TestUnknownRegimeIsNotAVerdict``.
        """
        model = CausalRegimeModel()
        assessment = model.assess(np.zeros(6))
        assert assessment.label is None
        assert assessment.risk_scalar == model.unknown_scalar

    def test_non_finite_features_report_unknown(
        self, features_and_volatility: tuple[np.ndarray, np.ndarray]
    ) -> None:
        features, volatility = features_and_volatility
        model = CausalRegimeModel(fit_window=504)
        model.fit_through(features, volatility, index=800)
        assessment = model.assess(np.full(6, np.nan))
        assert assessment.label is None

    def test_risk_scalar_never_exceeds_one(
        self, features_and_volatility: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """The regime layer conditions risk; it must never amplify it."""
        features, volatility = features_and_volatility
        model = CausalRegimeModel(fit_window=504)
        model.fit_through(features, volatility, index=800)
        for row in range(700, 900):
            assessment = model.assess(features[row])
            assert model.risk_floor <= assessment.risk_scalar <= 1.0

    def test_higher_volatility_regimes_take_less_risk(
        self, features_and_volatility: tuple[np.ndarray, np.ndarray]
    ) -> None:
        features, volatility = features_and_volatility
        model = CausalRegimeModel(fit_window=504, n_regimes=3, seed=5)
        model.fit_through(features, volatility, index=800)
        # Hold every other penalty constant so the comparison isolates the label
        # effect. Confidence, stability, and the transition penalty are all
        # legitimate independent inputs to the scalar, and mixing them in would
        # test their interaction rather than the volatility ordering.
        scalars: dict[int, float] = {}
        for row in range(300, 900):
            assessment = model.assess(features[row])
            # assess() is a pure query; history advances only via observe().
            model.observe(assessment)
            if (
                assessment.label is not None
                and assessment.stability == 1.0
                and not assessment.changed
                and assessment.confidence >= model.min_confidence
            ):
                scalars.setdefault(assessment.label, assessment.risk_scalar)

        assert len(scalars) >= 2, f"expected several settled regimes, saw {scalars}"
        ordered = [scalars[label] for label in sorted(scalars)]
        assert ordered == sorted(ordered, reverse=True), (
            f"risk scalar must fall as regime volatility rises, got {scalars}"
        )

    def test_refit_schedule(self) -> None:
        model = CausalRegimeModel(refit_every=21)
        assert model.needs_refit(0) is True

    @pytest.mark.parametrize(
        ("regimes", "window", "floor"),
        [(1, 504, 0.25), (3, 100, 0.25), (3, 504, 0.0), (3, 504, 1.5)],
    )
    def test_invalid_configuration_fails_closed(
        self, regimes: int, window: int, floor: float
    ) -> None:
        with pytest.raises(DataIntegrityError):
            CausalRegimeModel(n_regimes=regimes, fit_window=window, risk_floor=floor)

    def test_min_fit_rows_is_enforced(self) -> None:
        assert MIN_FIT_ROWS >= 252


def _view(value: float):
    from amat.context import TimeframeView

    return TimeframeView(
        horizon_sessions=21,
        trend=np.array([value]),
        location=np.array([0.5]),
        slope=np.array([value]),
    )


class TestRegimeAuditRegressions:
    """Regressions for defects confirmed by the 2026-08-08 v2 risk audit."""

    @pytest.fixture
    def fitted(self) -> CausalRegimeModel:
        rng = np.random.default_rng(23)
        rows = 900
        features = np.vstack(
            [
                rng.normal(0.0, 1.0, size=(rows // 2, 6)),
                rng.normal(2.5, 2.0, size=(rows - rows // 2, 6)),
            ]
        )
        volatility = np.concatenate([np.full(rows // 2, 0.10), np.full(rows - rows // 2, 0.35)])
        model = CausalRegimeModel(fit_window=504, seed=1)
        model.fit_through(features, volatility, index=800)
        model._features_for_test = features  # type: ignore[attr-defined]
        return model

    def test_assess_is_idempotent(self, fitted: CausalRegimeModel) -> None:
        """Audit: assess() mutated label history, so re-reading changed the answer."""
        row = fitted._features_for_test[800]  # type: ignore[attr-defined]
        first = fitted.assess(row)
        second = fitted.assess(row)
        third = fitted.assess(row)
        assert first.label == second.label == third.label
        assert first.stability == second.stability == third.stability
        assert first.risk_scalar == second.risk_scalar == third.risk_scalar

    def test_observe_is_what_advances_history(self, fitted: CausalRegimeModel) -> None:
        features = fitted._features_for_test  # type: ignore[attr-defined]
        row = features[800]
        before = fitted.assess(row)
        for _ in range(5):
            fitted.observe(fitted.assess(row))
        after = fitted.assess(row)
        # Stability rises only because observations were committed explicitly.
        assert after.stability >= before.stability

    def test_first_assessment_is_not_reported_as_fully_stable(
        self, fitted: CausalRegimeModel
    ) -> None:
        """Audit: a one-observation history reported stability 1.0."""
        row = fitted._features_for_test[800]  # type: ignore[attr-defined]
        assert fitted.assess(row).stability == 0.0


class TestUnknownRegimeIsNotAVerdict:
    """An unfitted regime model must not apply its most punitive multiplier.

    ``unknown()`` returned ``risk_floor`` -- the same value the model uses for
    its worst measured regime. That encodes "I have no estimate" as "I have the
    worst possible estimate". It applies for the whole ``fit_window``, which is
    504 sessions by default: a fifth of a ten-year backtest, and half of a
    four-year one, runs at a quarter size for a reason unrelated to any regime.

    Measured on real data this was the single largest contributor to the v2
    book's shortfall against its volatility target: with every veto, the Kelly
    cap, and the correlation cap disabled, average gross exposure was 41.85%
    against v1's 86.66%; neutralizing the regime scalar alone restored it to
    86.54%.

    The scalar is now an explicit setting rather than a reused floor. The other
    guards -- grade, expectancy, correlation, the risk caps -- are all still
    active during warm-up, so neutral here does not mean unguarded.
    """

    def test_an_unknown_regime_is_neutral_by_default(self) -> None:
        model = CausalRegimeModel()
        assert model.unknown("warming up").risk_scalar == pytest.approx(1.0)

    def test_the_unknown_scalar_is_configurable(self) -> None:
        model = CausalRegimeModel(unknown_scalar=0.25)
        assert model.unknown("warming up").risk_scalar == pytest.approx(0.25)

    def test_the_unknown_scalar_is_independent_of_the_risk_floor(self) -> None:
        """The bug was that these two were the same value by accident."""
        model = CausalRegimeModel(risk_floor=0.25, unknown_scalar=1.0)
        assert model.unknown("warming up").risk_scalar == pytest.approx(1.0)
        assert model.risk_floor == pytest.approx(0.25)

    def test_an_unknown_regime_still_reports_no_label(self) -> None:
        """Neutral sizing must not be mistaken for a positive regime call."""
        assessment = CausalRegimeModel().unknown("warming up")
        assert assessment.label is None
        assert assessment.confidence == 0.0

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.1])
    def test_an_out_of_range_unknown_scalar_fails_closed(self, bad: float) -> None:
        with pytest.raises(DataIntegrityError):
            CausalRegimeModel(unknown_scalar=bad)
