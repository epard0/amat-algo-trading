"""Regime classification for one-minute crypto candles.

Crypto microstructure on a one-minute chart is not a single process. It cycles
through a small number of recognisable states, and the mistake a naive
close-to-close momentum model makes is to treat them all the same:

* **TREND** - a steep, efficient impulse. Momentum genuinely carries here.
* **CONSOLIDATION** - the flat shelf that follows an impulse. Price barely
  moves, so realized volatility over-states how far it will travel next.
* **REVERSAL** - the turn out of a shelf, or the immediate aftermath of a
  spike that was given back. A close-to-close model reads the *new* leg as
  fresh momentum and extrapolates it at full strength. That is precisely
  backwards: direction is least reliable right after a turn.
* **VOLATILE_CHOP** - the high-variance mean-reverting mess that follows a
  sharp drop. Big one-minute moves, no net travel.

Every function here is pure and float-based. It consumes candles and returns
numbers; no clock, no network, no configuration. Callers translate the
returned multipliers into a probability.

The classifier is deliberately readable rather than fitted. Thresholds are
module constants so they can be tuned against the decision log, which records
the full feature vector behind every evaluation.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import fmean, pstdev
from typing import Sequence

from core.strategy_brain.strategies.candles import PriceCandle

TREND = "TREND"
CONSOLIDATION = "CONSOLIDATION"
REVERSAL = "REVERSAL"
VOLATILE_CHOP = "VOLATILE_CHOP"
NEUTRAL = "NEUTRAL"

# Window sizes, in one-minute bars.
FAST_WINDOW = 15
SLOW_WINDOW = 45
IMPULSE_LOOKBACK = 20
IMPULSE_SPAN = 3
COMPRESSION_FAST = 10
COMPRESSION_SLOW = 30
AUTOCORRELATION_WINDOW = 30

# Classification thresholds.
IMPULSE_SIGMA_THRESHOLD = 1.6
REVERSAL_RETRACEMENT = 0.35
REVERSAL_MAX_AGE = 8
WICK_REJECTION_THRESHOLD = 0.55
CHOP_VOL_RATIO = 1.30
CHOP_EFFICIENCY = 0.35
CHOP_AUTOCORRELATION = -0.15
CONSOLIDATION_COMPRESSION = 0.65
CONSOLIDATION_VOL_RATIO = 0.90
CONSOLIDATION_EFFICIENCY = 0.40
TREND_EFFICIENCY = 0.50
TREND_CLOSE_LOCATION = -0.20
# A market that has travelled less than this many sigma over the last
# STALL_WINDOW bars has stopped, whatever it was doing before.
STALL_WINDOW = 5
STALL_SIGMA = 0.60


@dataclass(frozen=True)
class RegimeFeatures:
    """Everything the classifier looked at, kept for offline tuning."""

    realized_vol_fast: float
    realized_vol_slow: float
    vol_ratio: float
    efficiency_ratio: float
    return_autocorrelation: float
    range_compression: float
    impulse_sigma: float
    impulse_direction: int
    minutes_since_impulse: int
    reversal_retracement: float
    close_location: float
    wick_rejection: float
    # Direction of net travel across the efficiency window. This, not
    # impulse_direction, is the direction whose momentum the model would use.
    travel_direction: int
    # Net travel over the last few bars in units of the longer-window
    # volatility. Near zero means the market has stopped moving.
    recent_travel_sigma: float
    bars_available: int

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True)
class RegimeAssessment:
    """A regime label plus the four knobs a probability model needs."""

    regime: str
    confidence: float
    # Scales the raw per-minute drift. < 1 distrusts the measured direction.
    drift_multiplier: float
    # Minutes over which drift decays to 1/e. Small values mean "this move
    # will not persist for the rest of the interval".
    momentum_half_life_minutes: float
    # Scales realized volatility. > 1 widens the settlement distribution.
    volatility_multiplier: float
    # Added to the model's base shrinkage toward 50%.
    extra_shrinkage: float
    reasons: tuple[str, ...]
    features: RegimeFeatures

    def as_dict(self) -> dict:
        return {
            "regime": self.regime,
            "confidence": self.confidence,
            "drift_multiplier": self.drift_multiplier,
            "momentum_half_life_minutes": self.momentum_half_life_minutes,
            "volatility_multiplier": self.volatility_multiplier,
            "extra_shrinkage": self.extra_shrinkage,
            "reasons": list(self.reasons),
            "features": self.features.as_dict(),
        }


def _safe_stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    return pstdev(values)


def log_returns(closes: Sequence[float]) -> list[float]:
    return [
        math.log(current / previous)
        for previous, current in zip(closes, closes[1:])
    ]


def efficiency_ratio(closes: Sequence[float], window: int) -> float:
    """Kaufman efficiency: net travel divided by path length.

    1.0 is a straight line (a clean impulse); 0.0 is pure chop that ends where
    it started. This is the single most useful discriminator between the
    "steep move" and "stagnant shelf" halves of the cycle.
    """
    span = min(window, len(closes) - 1)
    if span < 2:
        return 0.0
    segment = closes[-(span + 1):]
    net = abs(math.log(segment[-1] / segment[0]))
    path = sum(abs(step) for step in log_returns(segment))
    if path <= 0.0:
        return 0.0
    return min(1.0, net / path)


def lag_one_autocorrelation(returns: Sequence[float]) -> float:
    """Positive means moves follow through; negative means they get retraced."""
    if len(returns) < 4:
        return 0.0
    series = list(returns)
    mean = fmean(series)
    centred = [value - mean for value in series]
    denominator = sum(value * value for value in centred)
    if denominator <= 0.0:
        return 0.0
    numerator = sum(
        first * second for first, second in zip(centred, centred[1:])
    )
    return max(-1.0, min(1.0, numerator / denominator))


def true_ranges(candles: Sequence[PriceCandle]) -> list[float]:
    ranges: list[float] = []
    for index, candle in enumerate(candles):
        high = float(candle.high)
        low = float(candle.low)
        if index == 0:
            ranges.append(max(0.0, high - low))
            continue
        previous_close = float(candles[index - 1].close)
        ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    return ranges


def range_compression(candles: Sequence[PriceCandle]) -> float:
    """Recent bar range relative to the longer window.

    Below 1 means the market has gone quiet - the shelf after an impulse.
    """
    ranges = true_ranges(candles)
    if len(ranges) < 4:
        return 1.0
    fast = ranges[-min(COMPRESSION_FAST, len(ranges)):]
    slow = ranges[-min(COMPRESSION_SLOW, len(ranges)):]
    slow_mean = fmean(slow)
    if slow_mean <= 0.0:
        return 1.0
    return fmean(fast) / slow_mean


def find_impulse(
    closes: Sequence[float],
    slow_volatility: float,
) -> tuple[float, int, int]:
    """Locate the largest recent multi-bar move.

    Returns ``(size_in_sigma, direction, bars_since_it_ended)``. The move is
    measured over ``IMPULSE_SPAN`` bars and scaled by the volatility that same
    span implies, so "steep" means steep *for this asset right now*.
    """
    span = IMPULSE_SPAN
    if len(closes) < span + 2 or slow_volatility <= 0.0:
        return 0.0, 0, 0

    window = closes[-min(IMPULSE_LOOKBACK, len(closes)):]
    if len(window) < span + 1:
        return 0.0, 0, 0

    # Floor the scale with the typical bar size. On a near-perfectly smooth
    # series the standard deviation of returns collapses toward zero and every
    # move measures as hundreds of sigma, which would let a one-tick wobble
    # register as a violent reversal. For real candles the standard deviation
    # is the larger term and this floor never binds.
    steps = [abs(step) for step in log_returns(window)]
    typical_step = sorted(steps)[len(steps) // 2] if steps else 0.0
    scale = max(slow_volatility, typical_step) * math.sqrt(span)
    if scale <= 0.0:
        return 0.0, 0, 0
    best_size = 0.0
    best_direction = 0
    best_end = len(window) - 1
    for end in range(span, len(window)):
        move = math.log(window[end] / window[end - span])
        size = abs(move) / scale
        if size > best_size:
            best_size = size
            best_direction = 1 if move > 0 else -1
            best_end = end

    bars_since = (len(window) - 1) - best_end
    return best_size, best_direction, bars_since


def reversal_retracement(
    closes: Sequence[float],
    impulse_direction: int,
    minutes_since_impulse: int,
) -> float:
    """How much of the impulse has been handed back since it ended.

    0 means the move held; 1 means price is back where the impulse started.
    """
    if impulse_direction == 0:
        return 0.0
    window = closes[-min(IMPULSE_LOOKBACK, len(closes)):]
    end_index = (len(window) - 1) - minutes_since_impulse
    start_index = end_index - IMPULSE_SPAN
    if start_index < 0 or end_index <= start_index:
        return 0.0
    impulse_move = math.log(window[end_index] / window[start_index])
    if impulse_move == 0.0:
        return 0.0
    give_back = math.log(window[-1] / window[end_index])
    # Signed so that "moved against the impulse" is positive regardless of
    # whether the impulse was up or down. Continuation returns 0, not a
    # negative number, because only the retracement matters here.
    sign = 1.0 if impulse_move > 0.0 else -1.0
    return max(0.0, (-give_back * sign) / abs(impulse_move))


def net_travel(closes: Sequence[float], window: int) -> float:
    """Signed log move over the last ``window`` bars."""
    span = min(window, len(closes) - 1)
    if span < 1:
        return 0.0
    return math.log(closes[-1] / closes[-(span + 1)])


def stall_sigma(closes: Sequence[float], slow_volatility: float) -> float:
    """Recent travel measured in units of what the longer window expects.

    This is what separates "still trending" from "parked on a shelf". The
    efficiency ratio cannot do it: a bar where price does not move adds zero
    to both the numerator and the denominator, so a stalled tail leaves the
    ratio pinned at whatever the earlier impulse set it to until that impulse
    finally ages out of the window. Measuring travel directly has no such
    blind spot.
    """
    span = min(STALL_WINDOW, len(closes) - 1)
    if span < 1 or slow_volatility <= 0.0:
        return 0.0
    expected = slow_volatility * math.sqrt(span)
    if expected <= 0.0:
        return 0.0
    return abs(net_travel(closes, span)) / expected


def close_location(candle: PriceCandle) -> float:
    """Where the bar closed inside its own range, mapped to -1..+1.

    +1 closed on the high, -1 closed on the low. A spike that closes near its
    low is a rejected move, not an up-trend, however large the high was.
    """
    high = float(candle.high)
    low = float(candle.low)
    if high <= low:
        return 0.0
    close = float(candle.close)
    return ((close - low) / (high - low)) * 2.0 - 1.0


def wick_rejection(candles: Sequence[PriceCandle], direction: int) -> float:
    """Fraction of recent bar range that was rejected against ``direction``.

    For an up move this is the mean upper-wick share of the last few bars: how
    much of each push higher was sold back before the bar closed.
    """
    if direction == 0 or not candles:
        return 0.0
    recent = candles[-min(3, len(candles)):]
    shares: list[float] = []
    for candle in recent:
        high = float(candle.high)
        low = float(candle.low)
        span = high - low
        if span <= 0.0:
            continue
        body_high = max(float(candle.open), float(candle.close))
        body_low = min(float(candle.open), float(candle.close))
        wick = (high - body_high) if direction > 0 else (body_low - low)
        shares.append(max(0.0, wick) / span)
    if not shares:
        return 0.0
    return fmean(shares)


def extract_features(candles: Sequence[PriceCandle]) -> RegimeFeatures:
    closes = [float(candle.close) for candle in candles]
    # Kraken's OHLC endpoint returns the minute currently in progress as its
    # last row. Its close is live spot - exactly what the price, momentum, and
    # volatility terms want - but its *shape* is meaningless: seconds into the
    # minute the high, low, and close have not separated yet, so a partial bar
    # would make close_location and wick_rejection flicker, and the regime
    # label with them, several times within one minute. Shape is therefore read
    # from the last bar that has actually finished forming.
    settled = candles[:-1] if len(candles) >= 2 else candles
    returns = log_returns(closes)
    fast_returns = returns[-min(FAST_WINDOW, len(returns)):]
    slow_returns = returns[-min(SLOW_WINDOW, len(returns)):]

    fast_vol = _safe_stdev(fast_returns)
    slow_vol = _safe_stdev(slow_returns)
    ratio = (fast_vol / slow_vol) if slow_vol > 0.0 else 1.0

    impulse_size, impulse_direction, bars_since = find_impulse(closes, slow_vol)

    return RegimeFeatures(
        realized_vol_fast=fast_vol,
        realized_vol_slow=slow_vol,
        vol_ratio=ratio,
        efficiency_ratio=efficiency_ratio(closes, FAST_WINDOW),
        return_autocorrelation=lag_one_autocorrelation(
            returns[-min(AUTOCORRELATION_WINDOW, len(returns)):]
        ),
        range_compression=range_compression(candles),
        impulse_sigma=impulse_size,
        impulse_direction=impulse_direction,
        minutes_since_impulse=bars_since,
        reversal_retracement=reversal_retracement(
            closes, impulse_direction, bars_since
        ),
        close_location=close_location(settled[-1]),
        wick_rejection=wick_rejection(settled, impulse_direction),
        travel_direction=_sign(net_travel(closes, FAST_WINDOW)),
        recent_travel_sigma=stall_sigma(closes, slow_vol),
        bars_available=len(candles),
    )


def _sign(value: float) -> int:
    if value > 0.0:
        return 1
    if value < 0.0:
        return -1
    return 0


def classify(candles: Sequence[PriceCandle]) -> RegimeAssessment:
    """Label the current regime and return the model's response to it."""
    features = extract_features(candles)
    reasons: list[str] = []

    had_impulse = features.impulse_sigma >= IMPULSE_SIGMA_THRESHOLD
    impulse_is_recent = features.minutes_since_impulse <= REVERSAL_MAX_AGE
    handed_back = features.reversal_retracement >= REVERSAL_RETRACEMENT
    rejected = (
        features.wick_rejection >= WICK_REJECTION_THRESHOLD
        and features.minutes_since_impulse <= 2
    )

    # 1. Reversal. The expensive mistake to get wrong, so it is checked first.
    if had_impulse and impulse_is_recent and (handed_back or rejected):
        if handed_back:
            reasons.append(
                f"{features.reversal_retracement:.0%} of a "
                f"{features.impulse_sigma:.1f}-sigma impulse retraced "
                f"{features.minutes_since_impulse}m after it ended"
            )
        if rejected:
            reasons.append(
                f"recent bars rejected {features.wick_rejection:.0%} of the "
                "push by the close"
            )
        confidence = min(
            1.0,
            0.4
            + 0.4 * min(1.0, features.reversal_retracement)
            + 0.2 * min(1.0, features.impulse_sigma / 3.0),
        )
        return RegimeAssessment(
            regime=REVERSAL,
            confidence=confidence,
            # Direction right after a turn is close to a coin flip, and the
            # new leg's measured drift is the least trustworthy number on the
            # chart. Keep a trace of it, decay it almost immediately, and
            # widen the distribution because turns are where the range is.
            drift_multiplier=0.25,
            momentum_half_life_minutes=0.75,
            volatility_multiplier=1.25,
            extra_shrinkage=0.25,
            reasons=tuple(reasons),
            features=features,
        )

    # 2. Volatile chop - the aftermath of a sharp drop.
    if features.vol_ratio >= CHOP_VOL_RATIO and (
        features.efficiency_ratio <= CHOP_EFFICIENCY
        or features.return_autocorrelation <= CHOP_AUTOCORRELATION
    ):
        reasons.append(
            f"volatility {features.vol_ratio:.2f}x its baseline with "
            f"efficiency {features.efficiency_ratio:.2f} and lag-1 "
            f"autocorrelation {features.return_autocorrelation:+.2f}"
        )
        return RegimeAssessment(
            regime=VOLATILE_CHOP,
            confidence=min(1.0, 0.35 + 0.4 * (features.vol_ratio - 1.0)),
            drift_multiplier=0.35,
            momentum_half_life_minutes=1.5,
            volatility_multiplier=1.15,
            extra_shrinkage=0.20,
            reasons=tuple(reasons),
            features=features,
        )

    # 3. Consolidation - the flat shelf.
    #
    # Two independent ways to qualify. The efficiency-based test catches a
    # market grinding sideways. The stall test catches the shape this module
    # exists for: a steep impulse that stops dead. In that case the efficiency
    # ratio is still pinned near 1.0 by the impulse sitting inside the 15-bar
    # window and will not fall for another ~14 bars, so requiring low
    # efficiency would label a five-minute-old shelf a live trend and
    # extrapolate the dead impulse's drift across the interval.
    stalled = features.recent_travel_sigma <= STALL_SIGMA
    ground_sideways = (
        features.range_compression <= CONSOLIDATION_COMPRESSION
        and features.vol_ratio <= CONSOLIDATION_VOL_RATIO
        and features.efficiency_ratio <= CONSOLIDATION_EFFICIENCY
    )
    if stalled or ground_sideways:
        if stalled:
            reasons.append(
                f"price has travelled only "
                f"{features.recent_travel_sigma:.2f} sigma in the last "
                f"{STALL_WINDOW}m"
            )
        if ground_sideways:
            reasons.append(
                f"range compressed to {features.range_compression:.2f} of "
                f"baseline at {features.vol_ratio:.2f}x volatility with "
                f"efficiency {features.efficiency_ratio:.2f}"
            )
        return RegimeAssessment(
            regime=CONSOLIDATION,
            confidence=min(1.0, 0.4 + (1.0 - features.range_compression)),
            drift_multiplier=0.40,
            momentum_half_life_minutes=1.5,
            # Deliberately NOT below 1.0. The tempting move is to narrow the
            # distribution because a quiet market travels less, but the model's
            # base volatility is already the fast 15-bar window, which has
            # itself collapsed - narrowing it again double-counts the calm.
            # Worse, compression is what precedes expansion: the flat shelf on
            # a crypto chart ends in a steep move, and a 15-minute binary near
            # its strike is decided by exactly that tail. Erring wide costs
            # skipped trades; erring narrow costs money.
            volatility_multiplier=1.05,
            extra_shrinkage=0.10,
            reasons=tuple(reasons),
            features=features,
        )

    # 4. Trend. Momentum is only trusted at full strength when the move is
    # efficient AND the last bar closed in the direction it travelled.
    #
    # The comparison must use travel_direction - the direction of the move
    # whose drift the model is about to trust - and not impulse_direction,
    # which is the largest move anywhere in the last 20 bars and can point the
    # other way. Normalising by a stale impulse inverts this check: a decline
    # whose final bar was bought back hard (the exact rejection this guard
    # exists to catch) would read as agreement merely because an unrelated
    # up-spike happened 16 bars earlier.
    direction_agrees = (
        features.close_location * (features.travel_direction or 1)
        >= TREND_CLOSE_LOCATION
    )
    if features.efficiency_ratio >= TREND_EFFICIENCY and direction_agrees:
        reasons.append(
            f"efficiency {features.efficiency_ratio:.2f} with the close "
            f"{features.close_location:+.2f} inside its bar"
        )
        return RegimeAssessment(
            regime=TREND,
            confidence=min(1.0, features.efficiency_ratio),
            drift_multiplier=1.0,
            momentum_half_life_minutes=6.0,
            volatility_multiplier=1.0,
            extra_shrinkage=0.0,
            reasons=tuple(reasons),
            features=features,
        )

    if features.efficiency_ratio >= TREND_EFFICIENCY:
        reasons.append(
            f"efficient move ({features.efficiency_ratio:.2f}) but the close "
            f"sat at {features.close_location:+.2f} of its bar, so the push "
            "was partly rejected"
        )
    else:
        reasons.append(
            f"no dominant structure: efficiency {features.efficiency_ratio:.2f}, "
            f"vol ratio {features.vol_ratio:.2f}, compression "
            f"{features.range_compression:.2f}"
        )
    return RegimeAssessment(
        regime=NEUTRAL,
        confidence=0.3,
        drift_multiplier=0.70,
        momentum_half_life_minutes=3.0,
        volatility_multiplier=1.0,
        extra_shrinkage=0.05,
        reasons=tuple(reasons),
        features=features,
    )


def effective_drift_minutes(horizon_minutes: float, half_life: float) -> float:
    """Integrate exponentially decaying drift over the remaining horizon.

    The old model multiplied per-minute drift by the full time to close, which
    assumes the current push persists undiminished for the whole interval.
    Nothing on a one-minute crypto chart does that. With a decay constant this
    becomes ``tau * (1 - exp(-T/tau))``, which saturates: a 0.75-minute
    half-life contributes at most ~0.75 minutes of drift no matter how much
    time is left.
    """
    if half_life <= 0.0:
        return 0.0
    if horizon_minutes <= 0.0:
        return 0.0
    return half_life * (1.0 - math.exp(-horizon_minutes / half_life))
