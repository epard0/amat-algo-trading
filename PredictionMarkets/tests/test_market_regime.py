"""Regime classification and its effect on the probability model.

The fixtures below are the shapes described on a one-minute crypto chart: a
steep efficient run, the flat shelf that follows it, the turn out of a spike,
and the high-variance chop after a drop.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.strategy_brain.strategies.candles import PriceCandle
from core.strategy_brain.strategies.crypto_15min_predictor import (
    Crypto15MinutePredictor,
)
from core.strategy_brain.strategies.market_regime import (
    CONSOLIDATION_EFFICIENCY,
    CONSOLIDATION,
    REVERSAL,
    TREND,
    VOLATILE_CHOP,
    classify,
    effective_drift_minutes,
    efficiency_ratio,
    lag_one_autocorrelation,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make(prices: list[float], wicks: dict[int, float] | None = None):
    """Build candles from a close series.

    ``wicks[i]`` extends bar ``i``'s high above its body, which is how a push
    that got sold back before the close appears in real data.
    """
    candles = []
    previous = prices[0]
    for index, price in enumerate(prices):
        overshoot = abs((wicks or {}).get(index, 0.0))
        candles.append(
            PriceCandle(
                timestamp=START + timedelta(minutes=index),
                open=Decimal(str(previous)),
                high=Decimal(str(max(price, previous) + overshoot)),
                low=Decimal(str(min(price, previous))),
                close=Decimal(str(price)),
                volume=Decimal("10"),
            )
        )
        previous = price
    return candles


def trend_series() -> list[float]:
    return [100 + index * 0.06 for index in range(30)]


def shelf_series() -> list[float]:
    """A steep run that stalls into a tight range - the stagnation phase."""
    climb = [100 + index * 0.10 for index in range(12)]
    shelf = [101.2 + (0.008 if index % 2 else -0.008) for index in range(18)]
    return climb + shelf


def reversal_series() -> list[float]:
    """A spike that is then handed back over the following three minutes."""
    base = [100 + index * 0.02 for index in range(18)]
    spike = [100.36, 100.7, 101.1, 101.4]
    give_back = [101.1, 100.8, 100.55]
    return base + spike + give_back


def chop_series() -> list[float]:
    quiet = [100 + (0.015 if index % 2 else -0.015) for index in range(15)]
    drop = [99.5, 99.0, 98.6]
    thrash = [
        98.6 + (0.45 if index % 2 == 0 else -0.45) for index in range(12)
    ]
    return quiet + drop + thrash


def test_steep_efficient_run_is_a_trend() -> None:
    assessment = classify(make(trend_series()))

    assert assessment.regime == TREND
    assert assessment.drift_multiplier == 1.0
    # Momentum is allowed to persist here and nowhere else.
    assert assessment.momentum_half_life_minutes >= 6.0


def test_flat_shelf_after_a_run_is_consolidation() -> None:
    assessment = classify(make(shelf_series()))

    assert assessment.regime == CONSOLIDATION
    assert assessment.features.range_compression < 0.65
    assert assessment.drift_multiplier < 1.0
    # Never narrower than the raw estimate: the base volatility is already the
    # compressed fast window, and a shelf resolves into a breakout.
    assert assessment.volatility_multiplier >= 1.0


def test_handed_back_spike_is_a_reversal() -> None:
    assessment = classify(make(reversal_series()))

    assert assessment.regime == REVERSAL
    assert assessment.features.reversal_retracement > 0.35
    assert assessment.features.minutes_since_impulse <= 8
    # The three responses that matter after a turn.
    assert assessment.drift_multiplier <= 0.25
    assert assessment.momentum_half_life_minutes <= 1.0
    assert assessment.volatility_multiplier > 1.0
    assert assessment.extra_shrinkage >= 0.25


def test_thrashing_after_a_drop_is_volatile_chop() -> None:
    assessment = classify(make(chop_series()))

    assert assessment.regime == VOLATILE_CHOP
    assert assessment.features.return_autocorrelation < 0
    assert assessment.volatility_multiplier > 1.0


def test_efficient_push_rejected_by_its_wick_is_not_a_trend() -> None:
    """The case close-to-close momentum gets wrong.

    Closes rise in a perfectly straight line - efficiency 1.0, which is as
    trend-like as a series can look - but each of the last bars spiked higher
    and closed back near its open. That is distribution, not trend.
    """
    prices = [100 + index * 0.01 for index in range(20)] + [
        100.25,
        100.55,
        100.9,
    ]
    # Wicks sit on the last SETTLED bars; index 22 is the forming minute and
    # is deliberately ignored by the classifier.
    rejected = make(prices, wicks={19: 0.30, 20: 0.35, 21: 0.40})

    assessment = classify(rejected)

    assert assessment.features.efficiency_ratio > 0.9
    assert assessment.features.wick_rejection > 0.55
    assert assessment.regime == REVERSAL

    # Same closes, no wicks, and it is read as the trend it appears to be.
    assert classify(make(prices)).regime == TREND


def test_stalled_shelf_is_consolidation_from_the_first_bar() -> None:
    """A steep impulse that stops dead must not read as a live trend.

    The efficiency ratio cannot see this: a bar where price does not move adds
    zero to both the numerator and the denominator, so efficiency stays pinned
    near 1.0 until the impulse ages out of the 15-bar window ~14 bars later.
    For those 14 bars the old code applied full momentum trust and a six-minute
    half-life to an impulse that had already ended.
    """
    impulse = [100 * (1.0008**index) for index in range(30)]

    # A shelf shorter than STALL_WINDOW is not yet distinguishable from a
    # pause inside a trend, and is deliberately not claimed here. From the
    # window length onward - well before efficiency could react - it must be
    # recognised.
    for shelf_bars in (5, 6, 8, 10, 12, 14):
        shelf = [impulse[-1]] * shelf_bars
        assessment = classify(make(impulse + shelf))

        assert assessment.regime == CONSOLIDATION, (
            f"{shelf_bars}-bar shelf classified {assessment.regime}"
        )
        # Efficiency is still saturated here; only the stall test can see it.
        assert assessment.features.efficiency_ratio > CONSOLIDATION_EFFICIENCY
        assert assessment.momentum_half_life_minutes <= 1.5
        assert assessment.drift_multiplier <= 0.40


def test_trend_direction_check_ignores_a_stale_opposing_impulse() -> None:
    """The close-location guard must normalise by the move being trusted.

    ``impulse_direction`` is the largest move anywhere in the last 20 bars and
    can point opposite to the leg whose drift the model would use. Comparing
    against it silently inverts the guard.
    """
    # An up-spike, then a sustained decline whose final bar is bought back
    # hard - a long lower wick closing near the high. That rejection is the
    # thing the guard exists to catch.
    spike = [100.0, 100.2, 100.4, 100.6]
    decline = [100.6 - index * 0.05 for index in range(1, 17)]
    prices = spike + decline
    bought_back = make(prices)
    # Put the rejection on the last SETTLED bar: it dipped far lower and then
    # closed back at its high. Index -1 is the forming minute and is ignored.
    settled = bought_back[-2]
    bought_back[-2] = PriceCandle(
        timestamp=settled.timestamp,
        open=settled.open,
        high=settled.close,
        low=Decimal(str(float(settled.close) - 0.20)),
        close=settled.close,
        volume=settled.volume,
    )

    assessment = classify(bought_back)

    assert assessment.features.travel_direction == -1
    assert assessment.features.close_location > 0
    # Direction of travel is down while the bar closed at its high, so this is
    # a rejected push and must not earn full momentum trust.
    assert assessment.regime != TREND


def test_bar_shape_ignores_the_in_progress_minute() -> None:
    """Kraken's last OHLC row is the minute currently forming.

    Seconds into a minute its high, low, and close have barely separated. If
    shape were read from that bar, the regime label - and the probability, and
    therefore whether an order is placed - would flicker within a single
    minute as the partial bar filled in.
    """
    prices = [100 + index * 0.01 for index in range(20)] + [
        100.25,
        100.55,
        100.9,
    ]
    settled_wicks = {19: 0.30, 20: 0.35, 21: 0.40}

    # Two snapshots of the same market: one where the forming bar has barely
    # moved, one where it has already spiked. The settled history is identical.
    barely_started = make(prices, wicks=settled_wicks)
    already_spiked = make(prices, wicks={**settled_wicks, 22: 0.9})

    early = classify(barely_started)
    late = classify(already_spiked)

    assert early.regime == late.regime
    assert early.features.close_location == late.features.close_location
    assert early.features.wick_rejection == late.features.wick_rejection


def test_drift_decay_saturates_instead_of_extrapolating() -> None:
    """A short half-life must not accumulate drift across a long horizon."""
    short = [effective_drift_minutes(minutes, 0.75) for minutes in (1, 5, 15)]

    assert short[0] < short[1] < short[2]
    assert short[2] < 0.80  # never more than ~one half-life of carry
    # A very long half-life reproduces the old linear behaviour.
    assert effective_drift_minutes(10.0, 1e6) == pytest.approx(10.0, rel=1e-3)
    assert effective_drift_minutes(0.0, 5.0) == 0.0
    assert effective_drift_minutes(10.0, 0.0) == 0.0


def test_efficiency_ratio_separates_a_line_from_a_round_trip() -> None:
    straight = [100 + index for index in range(16)]
    round_trip = [100 + (1 if index % 2 else 0) for index in range(16)]

    assert efficiency_ratio(straight, 15) > 0.99
    assert efficiency_ratio(round_trip, 15) < 0.15
    # Degenerate input must not raise or divide by zero.
    assert efficiency_ratio([100.0] * 16, 15) == 0.0
    assert efficiency_ratio([100.0], 15) == 0.0


def test_autocorrelation_signs_follow_through_versus_retracement() -> None:
    following = [0.01, 0.011, 0.012, 0.011, 0.012, 0.013]
    alternating = [0.01, -0.01, 0.01, -0.01, 0.01, -0.01]

    assert lag_one_autocorrelation(following) > 0
    assert lag_one_autocorrelation(alternating) < 0
    assert lag_one_autocorrelation([]) == 0.0
    assert lag_one_autocorrelation([0.0] * 8) == 0.0


def test_reversal_pulls_the_probability_toward_a_coin_flip() -> None:
    """The behavioural point of the whole change.

    Given identical candles ending just after a turn, the regime-aware model
    must be less confident than the momentum-only model it replaced.
    """
    candles = make(reversal_series())
    now = START + timedelta(minutes=len(candles))
    close_time = now + timedelta(minutes=10)
    # Target sits above spot, so the model is pricing "does the rally resume".
    target = Decimal("100.90")

    aware = Crypto15MinutePredictor(regime_aware=True).predict(
        candles, target, close_time, now
    )
    naive = Crypto15MinutePredictor(regime_aware=False).predict(
        candles, target, close_time, now
    )

    assert aware.regime == REVERSAL
    distance_from_even = abs(aware.probability_yes - Decimal("0.5"))
    naive_distance = abs(naive.probability_yes - Decimal("0.5"))
    assert distance_from_even < naive_distance
    # Drift is kept but heavily discounted, never sign-flipped.
    assert abs(aware.momentum_return) < abs(aware.raw_momentum_return)
    assert (aware.momentum_return >= 0) == (aware.raw_momentum_return >= 0)
    assert aware.effective_volatility > aware.volatility_per_minute
    assert aware.applied_shrinkage > naive.applied_shrinkage


def test_prediction_carries_its_full_reasoning() -> None:
    candles = make(reversal_series())
    now = START + timedelta(minutes=len(candles))

    prediction = Crypto15MinutePredictor().predict(
        candles, Decimal("100.90"), now + timedelta(minutes=10), now
    )

    assert prediction.regime_reasons
    assert prediction.regime_features["efficiency_ratio"] is not None
    assert prediction.regime_features["bars_available"] == len(candles)
    assert Decimal("0") < prediction.raw_probability_yes < Decimal("1")


def test_short_history_still_predicts_without_raising() -> None:
    """Eight flat-ish bars is the documented minimum; regime code must cope."""
    candles = make([100 + index * 0.01 for index in range(8)])
    now = START + timedelta(minutes=8)

    prediction = Crypto15MinutePredictor().predict(
        candles, Decimal("100"), now + timedelta(minutes=5), now
    )

    assert Decimal("0.01") <= prediction.probability_yes <= Decimal("0.99")
    assert prediction.regime_features["bars_available"] == 8
