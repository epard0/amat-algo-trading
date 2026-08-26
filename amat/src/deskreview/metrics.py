"""Every number a review reports, computed here and nowhere else.

Each rule below is a *declared threshold*, not a discovered one. None of them has
been fitted, validated, or shown to predict anything, and they must not be read
as signals -- they exist so that a review says "IWM's 63-session resistance broke
by 1.4%" instead of "IWM looks strong". A stated constant that someone can argue
with is worth more than a vague adjective.

Causality is the same rule as everywhere else in this repository: a value
reported for session ``t`` uses rows through ``t`` and never past it. Level
crossings compare session ``t``'s close to a level computed through ``t-1``,
because a level that includes today's own high can never be broken by today.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from amat.errors import DataIntegrityError

__all__ = [
    "EXHAUSTION_CLOSE_LOCATION",
    "EXHAUSTION_RANGE_RATIO",
    "LevelBreak",
    "PullbackState",
    "close_location",
    "detect_level_breaks",
    "overnight_gap",
    "pullback_state",
    "true_range",
]

#: A bar's range must fall below this fraction of the pullback's own average
#: range before the move is called "contracting". 0.7 is a declared choice.
_UPPER_EXHAUSTION_RANGE_RATIO = 0.88
EXHAUSTION_RANGE_RATIO = 0.70
_LOWER_EXHAUSTION_RANGE_RATIO = 0.5338

#: ...and the bar must close in the upper 60% of its own range. Together these
#: describe a narrow bar that closed strong. That is a *description*; whether it
#: precedes anything is untested here and is not claimed.
EXHAUSTION_CLOSE_LOCATION = 0.60

#: Bars averaged to form the pullback's reference range.
_EXHAUSTION_LOOKBACK = 5


@dataclass(frozen=True, slots=True)
class LevelBreak:
    """One trailing level crossed by one instrument on one session."""

    symbol: str
    kind: str
    """``"resistance"`` or ``"support"``."""

    level: float
    close: float
    distance: float
    """Signed fraction of the level. Positive means the close is above it."""


@dataclass(frozen=True, slots=True)
class PullbackState:
    """How far an instrument has retraced, and whether the retrace is narrowing.

    Attributes:
        drawdown_from_high: Fraction below the trailing window high, negative or
            zero.
        range_ratio: Latest bar's true range over the mean of the previous
            :data:`_EXHAUSTION_LOOKBACK` true ranges. Below 1 means contracting.
        close_location: Where the latest close sits in its own bar, 0 at the low
            and 1 at the high.
        contracting: ``range_ratio`` below :data:`EXHAUSTION_RANGE_RATIO`.
        closing_strong: ``close_location`` above :data:`EXHAUSTION_CLOSE_LOCATION`.
        exhausted: Both of the above. A narrow bar that closed near its high,
            after a decline. **This is a description of two measured numbers,
            not a prediction and not a signal.**
    """

    symbol: str
    drawdown_from_high: float
    range_ratio: float
    close_location: float
    contracting: bool
    closing_strong: bool

    @property
    def exhausted(self) -> bool:
        return self.contracting and self.closing_strong


def overnight_gap(open_price: float, prior_close: float) -> float:
    """Fractional move from the prior close to this session's open.

    Raises:
        DataIntegrityError: on a non-positive prior close, which would make the
            fraction meaningless rather than merely large.
    """
    if not np.isfinite(prior_close) or prior_close <= 0:
        message = f"prior close must be finite and positive, received {prior_close!r}"
        raise DataIntegrityError(message)
    if not np.isfinite(open_price):
        message = f"open must be finite, received {open_price!r}"
        raise DataIntegrityError(message)
    return float(open_price / prior_close - 1.0)


def true_range(high: np.ndarray, low: np.ndarray, prior_close: np.ndarray) -> np.ndarray:
    """Wilder's true range, elementwise.

    The prior close is included because a gap is part of the move. Using only
    ``high - low`` would call a session that gapped 3% and then traded flat a
    *quiet* session, which is exactly backwards.
    """
    stacked = np.maximum.reduce(
        [
            high - low,
            np.abs(high - prior_close),
            np.abs(low - prior_close),
        ]
    )
    return np.asarray(stacked, dtype=float)


def close_location(close: float, high: float, low: float) -> float:
    """Where a close sits within its own bar. 0 at the low, 1 at the high.

    A zero-range bar has no location; it returns 0.5 rather than dividing by
    zero, because "exactly in the middle" is the only defensible answer when the
    high and the low are the same price.
    """
    span = high - low
    if not np.isfinite(span) or span <= 0:
        return 0.5
    return float((close - low) / span)


def detect_level_breaks(
    symbols: tuple[str, ...],
    closes: np.ndarray,
    resistance: np.ndarray,
    support: np.ndarray,
    *,
    index: int,
) -> list[LevelBreak]:
    """Which instruments closed through a trailing level on session ``index``.

    The level is taken from ``index - 1``. That offset is the whole point: the
    trailing resistance at ``index`` already includes session ``index``'s own
    high, so a close can never exceed it and no break would ever be detected.
    Comparing today's close against yesterday's level is the only formulation
    that can fire, and it is also the only causal one.

    Raises:
        DataIntegrityError: if ``index`` is the first row, where no prior level
            exists.
    """
    if index < 1:
        message = f"a level break needs a prior session; index {index} has none"
        raise DataIntegrityError(message)

    breaks: list[LevelBreak] = []
    for column, symbol in enumerate(symbols):
        close = float(closes[index, column])
        if not np.isfinite(close):
            continue
        ceiling = float(resistance[index - 1, column])
        floor = float(support[index - 1, column])
        if np.isfinite(ceiling) and ceiling > 0 and close > ceiling:
            breaks.append(
                LevelBreak(
                    symbol=symbol,
                    kind="resistance",
                    level=ceiling,
                    close=close,
                    distance=close / ceiling - 1.0,
                )
            )
        elif np.isfinite(floor) and floor > 0 and close < floor:
            breaks.append(
                LevelBreak(
                    symbol=symbol,
                    kind="support",
                    level=floor,
                    close=close,
                    distance=close / floor - 1.0,
                )
            )
    return breaks


def pullback_state(
    symbol: str,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    *,
    window: int,
) -> PullbackState:
    """Retracement depth and whether the latest bar contracted.

    ``closes``, ``highs`` and ``lows`` are 1-D series for one instrument, oldest
    first, and the final element is the bar being described.

    Raises:
        DataIntegrityError: if the series are too short to form both a window
            high and a true-range baseline.
    """
    needed = max(window, _EXHAUSTION_LOOKBACK + 2)
    if closes.size < needed:
        message = (
            f"{symbol}: {closes.size} bars is too few; need {needed} for a "
            f"{window}-bar high and a {_EXHAUSTION_LOOKBACK}-bar range baseline"
        )
        raise DataIntegrityError(message)

    recent_high = float(np.nanmax(highs[-window:]))
    close = float(closes[-1])
    drawdown = close / recent_high - 1.0 if recent_high > 0 else 0.0

    ranges = true_range(highs[1:], lows[1:], closes[:-1])
    baseline = float(np.nanmean(ranges[-(_EXHAUSTION_LOOKBACK + 1) : -1]))
    latest = float(ranges[-1])
    ratio = latest / baseline if baseline > 0 else 1.0
    location = close_location(close, float(highs[-1]), float(lows[-1]))

    return PullbackState(
        symbol=symbol,
        drawdown_from_high=float(min(drawdown, 0.0)),
        range_ratio=ratio,
        close_location=location,
        contracting=ratio < EXHAUSTION_RANGE_RATIO,
        closing_strong=location > EXHAUSTION_CLOSE_LOCATION,
    )
