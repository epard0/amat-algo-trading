"""Causal regime classification, per-regime slices, and the vol-state scalars.

Everything here runs on hand-built price paths whose regimes are known by
construction, so a label is checked against an intended state rather than
against the classifier's own output.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.data.synthetic import trading_sessions
from amat.errors import DataIntegrityError
from amat.validation.regimes import (
    REGIME_LABELS,
    classify_sessions,
    regime_performance,
    volatility_state_scalars,
)


def _panel_from_closes(closes: np.ndarray, *, symbol: str = "SPY") -> MarketPanel:
    """A one-symbol panel around a hand-built close series."""
    total = closes.shape[0]
    column = closes.reshape(-1, 1).astype(float)
    return MarketPanel(
        sessions=trading_sessions(dt.date(2020, 1, 6), total),
        symbols=(symbol,),
        adjusted_closes=column,
        raw_closes=column.copy(),
        window_vwaps=column.copy(),
        window_volumes=np.full((total, 1), 1.0e6),
        source="synthetic",
    )


def _calm_then_stormy(total: int = 400, *, storm_from: int = 300) -> np.ndarray:
    """Gently rising and calm, then falling with large alternating swings.

    The first regime is bull-quiet by construction; from ``storm_from`` the
    trailing 63-session return turns negative and realized volatility jumps
    far above its expanding median, so the tail is bear-volatile.
    """
    closes = [100.0]
    for step in range(1, total):
        if step < storm_from:
            closes.append(closes[-1] * 1.0005)
        else:
            swing = 1.03 if step % 2 == 0 else 0.925
            closes.append(closes[-1] * swing)
    return np.array(closes)


class TestClassifySessions:
    def test_early_sessions_are_warmup(self) -> None:
        labels = classify_sessions(_panel_from_closes(_calm_then_stormy()))
        assert set(labels[:63]) == {"warmup"}

    def test_a_calm_rise_is_bull_quiet_and_a_violent_fall_is_bear_volatile(self) -> None:
        labels = classify_sessions(_panel_from_closes(_calm_then_stormy()))
        assert labels[250] == "bull-quiet"
        assert labels[395] == "bear-volatile"
        assert set(labels) <= set(REGIME_LABELS)

    def test_labels_are_causal_under_future_mutation(self) -> None:
        closes = _calm_then_stormy()
        cut = 200
        mutated = closes.copy()
        mutated[cut:] = closes[cut:] * np.linspace(1.0, 3.0, closes.shape[0] - cut)
        original = classify_sessions(_panel_from_closes(closes))
        rewritten = classify_sessions(_panel_from_closes(mutated))
        assert original[:cut] == rewritten[:cut]

    def test_an_absent_reference_symbol_fails_closed(self) -> None:
        panel = _panel_from_closes(_calm_then_stormy(), symbol="QQQ")
        with pytest.raises(DataIntegrityError, match="reference symbol"):
            classify_sessions(panel, reference="SPY")


class TestRegimePerformance:
    def test_a_return_is_booked_under_the_prior_sessions_label(self) -> None:
        """The crash that makes a session 'volatile' stays in the quiet bucket.

        Session 2's -10% move is exactly the kind of move that flips its own
        label to volatile (the move sits inside the trailing volatility that
        labels it); booking it under session 1's label is what stops large
        moves from selecting themselves into the volatile slice.
        """
        panel = _panel_from_closes(np.full(3, 100.0))
        labels = ("bull-quiet", "bull-quiet", "bear-volatile")
        equity = [100.0, 102.0, 91.8]  # +2%, then -10%
        slices = regime_performance(equity, panel.sessions, panel, labels)
        by_label = {entry.label: entry for entry in slices}
        assert "bear-volatile" not in by_label, "the volatile label received no returns"
        quiet = by_label["bull-quiet"]
        assert quiet.sessions == 2
        assert quiet.hit_rate == 0.5
        # Realized CAGR of the slice's own compounded growth -- the platform
        # convention -- not the arithmetic mean compounded 252 times.
        growth = 1.02 * 0.90
        assert quiet.annualized_return == pytest.approx(growth ** (252 / 2) - 1.0)
        deviation = float(np.std([0.02, -0.10], ddof=1))
        assert quiet.annualized_volatility == pytest.approx(deviation * np.sqrt(252))

    def test_iid_noise_shows_no_quiet_volatile_separation(self) -> None:
        """On label-free iid returns every slice must measure ~the same vol.

        Under the contemporaneous convention (book ``t``'s return under
        ``t``'s own label) this measured a ~1.27x volatile/quiet separation on
        pure noise -- the volatile slices ran ~13% hot and the quiet slices
        ~11% cold. The lagged convention must keep every slice within a band
        that convention could not satisfy.
        """
        rng = np.random.default_rng(11)
        sigma = 0.01
        closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0, sigma, size=6000)))
        panel = _panel_from_closes(closes)
        labels = classify_sessions(panel)
        slices = regime_performance([float(v) for v in closes], panel.sessions, panel, labels)
        true_vol = sigma * np.sqrt(252)
        checked = 0
        for entry in slices:
            if entry.label == "warmup" or entry.sessions < 300:
                continue
            checked += 1
            ratio = entry.annualized_volatility / true_vol
            assert 0.92 <= ratio <= 1.08, f"{entry.label}: vol ratio {ratio:.3f}"
        assert checked >= 2, "the noise sample must populate several regimes"

    def test_label_coverage_must_match_the_panel(self) -> None:
        panel = _panel_from_closes(np.full(6, 100.0))
        with pytest.raises(DataIntegrityError, match="labels cover"):
            regime_performance([100.0, 101.0], panel.sessions[:2], panel, ("warmup",))


class TestVolatilityStateScalars:
    def test_values_are_binary_and_warmup_is_full_size(self) -> None:
        scalars = volatility_state_scalars(
            _panel_from_closes(_calm_then_stormy()), min_history_sessions=252
        )
        assert set(np.unique(scalars)) <= {0.5, 1.0}
        assert np.all(scalars[:252] == 1.0)

    def test_the_storm_is_detected_and_the_calm_mostly_is_not(self) -> None:
        scalars = volatility_state_scalars(
            _panel_from_closes(_calm_then_stormy(storm_from=300)), min_history_sessions=252
        )
        # The calm phase's realized vols are numerically near-identical, so an
        # occasional float-level tie can cross the strict ">" -- a knife-edge
        # this hand-built series creates and real data does not. What must
        # hold is the *rate*: calm sessions are overwhelmingly full-size, and
        # every established storm session is cut.
        calm_flagged = float(np.mean(scalars[252:300] < 1.0))
        assert calm_flagged <= 0.10, f"{calm_flagged:.0%} of calm sessions were de-risked"
        assert np.all(scalars[330:] == 0.5), "the volatility spike must be detected"

    def test_scalars_are_causal_under_future_mutation(self) -> None:
        closes = _calm_then_stormy()
        cut = 280
        mutated = closes.copy()
        mutated[cut:] *= np.linspace(1.0, 0.2, closes.shape[0] - cut)
        original = volatility_state_scalars(_panel_from_closes(closes))
        rewritten = volatility_state_scalars(_panel_from_closes(mutated))
        assert np.array_equal(original[:cut], rewritten[:cut])

    def test_unusable_parameters_fail_closed(self) -> None:
        panel = _panel_from_closes(_calm_then_stormy())
        with pytest.raises(DataIntegrityError, match="quantile"):
            volatility_state_scalars(panel, quantile=1.0)
        with pytest.raises(DataIntegrityError, match="reduced_scalar"):
            volatility_state_scalars(panel, reduced_scalar=0.0)
        with pytest.raises(DataIntegrityError, match="vol_window"):
            volatility_state_scalars(panel, vol_window=1)
