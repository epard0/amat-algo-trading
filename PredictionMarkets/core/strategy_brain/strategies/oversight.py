"""How closely a position is watched, as a function of what it is risking.

A position holding 3% of the account and one holding 30% are not the same
object to supervise, but the salvage exit treated them identically: one
evaluation, 30 seconds before close, whatever the size. That is the right
amount of attention for the small one and far too little for the large one -
by the time a 30% position is examined there are 30 seconds left to sell it.

This module is the policy, and nothing else. It computes *when* and *how often*
a position is put to the question from the fraction of the portfolio it is
risking. It does not decide anything about the position itself: the sell rule
stays in ``salvage.py``, stays price-triggered, and never sees the model.
Keeping the two apart is what stops "watch it more closely" from quietly
becoming "sell it on a different rule".

Three properties are deliberate:

- **More exposure buys earlier and more frequent checks, never a looser sell
  rule.** The price at which the salvage rule acts is a separate, measured
  number. Widening attention is cheap and reversible; widening the band is the
  exit-on-reversal that was removed for losing money.
- **The check count is bounded and spaced.** A rule re-asked every poll
  eventually says yes to noise, which is precisely how the reversal exit
  failed. Four spaced evaluations is supervision; three hundred is a random
  number generator with a fee attached.
- **A check needs time to act on its answer.** Below
  ``min_actionable_seconds`` no check is scheduled at all, because an exit is
  an order that has to reach the venue and fill before the market closes. The
  floor rises with the position, since a larger one consumes more of the book
  and is likelier to need a second attempt. Examining a position you can no
  longer sell produces a log line, a book read, and a false impression that it
  was supervised.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class OversightPlan:
    """The supervision one position has earned."""

    exposure_fraction: Decimal
    """Position value as a fraction of the portfolio, clamped to [0, 1]."""

    intensity: Decimal
    """0 at no exposure, 1 at or above the full-attention threshold."""

    window_seconds: int
    """Seconds before close at which this position becomes eligible."""

    checks: int
    """How many times it may be evaluated, across the whole window."""

    min_spacing_seconds: float
    """Minimum gap between two evaluations of the same position."""

    shadow_max_bid: Decimal
    """Band ceiling to *record* against, never to act on. See ``salvage.py``."""

    min_actionable_seconds: float
    """Below this there is not enough time left to complete a sale."""


def exposure_fraction(
    *, position_value: Decimal, portfolio_value: Decimal
) -> Decimal:
    """What share of the account this position is risking.

    A non-positive portfolio value means the answer is unknown, not zero: a
    balance that could not be read must not be reported as "risking nothing",
    because that is the reading that would earn the position the least
    supervision at exactly the moment nobody knows what it is worth. Unknown
    therefore scores as fully exposed.
    """
    if portfolio_value <= ZERO:
        return ONE
    if position_value <= ZERO:
        return ZERO
    return min(ONE, position_value / portfolio_value)


def plan_oversight(
    *,
    position_value: Decimal,
    portfolio_value: Decimal,
    base_window_seconds: int,
    max_window_seconds: int,
    base_checks: int,
    max_checks: int,
    min_spacing_seconds: float,
    full_attention_exposure: Decimal,
    base_max_bid: Decimal,
    shadow_max_bid: Decimal,
    base_actionable_seconds: float = 0.0,
    extra_actionable_seconds: float = 0.0,
) -> OversightPlan:
    """Interpolate supervision between the base and full-attention settings.

    ``full_attention_exposure`` is the fraction at which a position earns every
    check available; beyond it nothing further is granted, so an outsized
    position cannot demand unbounded attention.

    The base values are returned exactly when exposure is zero, which is what
    makes this safe to leave switched on: a caller that supplies a zero
    position value gets the old behaviour, character for character.
    """
    exposure = exposure_fraction(
        position_value=position_value, portfolio_value=portfolio_value
    )
    if full_attention_exposure <= ZERO:
        intensity = ONE if exposure > ZERO else ZERO
    else:
        intensity = min(ONE, exposure / full_attention_exposure)

    window = base_window_seconds + int(
        (max(max_window_seconds, base_window_seconds) - base_window_seconds)
        * intensity
    )
    checks = base_checks + int(
        (max(max_checks, base_checks) - base_checks) * intensity
    )
    # Interpolated the same way, but recorded rather than obeyed. The salvage
    # rule is handed ``base_max_bid``; this number only ever reaches the log.
    shadow = base_max_bid + (
        max(shadow_max_bid, base_max_bid) - base_max_bid
    ) * intensity

    # Time to cut, and why it scales with the position. An exit is an IOC sell
    # of the whole position at the touch; the round trip is fast, but the ways
    # it fails are not instant to recover from - a partial fill leaves a
    # remainder that ``record_exit`` would misvalue, and the depth that was
    # there when the book was read may not be there when the order lands. A
    # larger position consumes more of the book and is likelier to need that
    # second look, so it needs more runway to be worth starting.
    #
    # This is a floor on the whole window, not merely on the last check. A
    # check with no time to act on its answer is not supervision; it is a log
    # line that costs a book read and tells an operator the position was
    # "examined".
    actionable = base_actionable_seconds + extra_actionable_seconds * float(
        intensity
    )

    return OversightPlan(
        exposure_fraction=exposure,
        intensity=intensity,
        window_seconds=max(1, window),
        checks=max(1, checks),
        min_spacing_seconds=max(0.0, min_spacing_seconds),
        shadow_max_bid=shadow,
        min_actionable_seconds=max(0.0, actionable),
    )
