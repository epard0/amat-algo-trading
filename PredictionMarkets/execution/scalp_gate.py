"""Pure gates specific to maker-side scalping.

Separate from ``risk_gate.py`` because the failure modes are different: the
predictor's gates guard against a wrong forecast, and these guard against a
correct forecast executed badly. Both modules are side-effect free for the same
reason - a guard that needs a clock, a socket or a ledger to test is a guard
that stops being tested.
"""

from __future__ import annotations

from decimal import Decimal

from execution.risk_gate import ALLOW, GateResult


ZERO = Decimal("0")


def check_price_band(
    price: Decimal,
    band_low: Decimal,
    band_high: Decimal,
) -> GateResult:
    """Confine entries to the favourite band.

    The lower bound is the win-rate constraint in disguise: break-even needs
    ``price + fee``, so a floor of 0.60 means any EV-positive strategy must win
    more than 60% of the time. The upper bound exists because above roughly
    0.92 a loss costs more than ten times the gain, and the remaining edge is
    smaller than a tick.
    """
    if price < band_low:
        return GateResult(
            False,
            f"price {price:.2f} is below the favourite band floor {band_low:.2f}; "
            "longshots returned worse than -60% in the reference data",
        )
    if price > band_high:
        return GateResult(
            False,
            f"price {price:.2f} is above the band ceiling {band_high:.2f}; "
            "the payoff no longer compensates the downside",
        )
    return ALLOW


def check_spread(
    yes_bid: Decimal,
    yes_ask: Decimal,
    maximum_spread: Decimal,
) -> GateResult:
    """Refuse to quote into a wide book.

    The exit path is what bounds the loss on this strategy, and a wide spread is
    paid twice - once entering, once leaving. A book we cannot leave cheaply is
    one we should not enter.
    """
    if yes_bid <= ZERO or yes_ask <= ZERO:
        return GateResult(False, "book is one-sided or empty")
    spread = yes_ask - yes_bid
    if spread > maximum_spread:
        return GateResult(
            False,
            f"spread {spread:.2f} exceeds {maximum_spread:.2f}; the round trip "
            "costs more than the edge",
        )
    return ALLOW


def check_book_depth(
    depth: Decimal,
    minimum_depth: Decimal,
) -> GateResult:
    """Require enough resting size that the position can be exited."""
    if depth < minimum_depth:
        return GateResult(
            False,
            f"resting depth {depth} is below the {minimum_depth} needed to exit",
        )
    return ALLOW


def check_quote_window(
    seconds_to_close: Decimal,
    minimum_seconds: Decimal,
) -> GateResult:
    """Never rest a quote that cannot be exited before the market closes.

    A fill inside the settlement lead window is a position taken with no way
    out, which is exactly the risk the exit path exists to remove.
    """
    if seconds_to_close < minimum_seconds:
        return GateResult(
            False,
            f"{seconds_to_close:.0f}s to close is inside the {minimum_seconds:.0f}s "
            "floor; a fill here could not be exited",
        )
    return ALLOW


def check_pattern(
    regime: str,
    regime_confidence: Decimal,
    z_score: Decimal,
    *,
    blocked_regimes: tuple[str, ...],
    minimum_confidence: Decimal,
    minimum_abs_z: Decimal,
) -> GateResult:
    """Only quote structure the classifier can actually name.

    Three refusals, in order of how badly they burn:

    ``VOLATILE_CHOP`` is by its own definition the regime where price shape
    carries no information - a high-variance mean-reverting mess. Quoting it is
    quoting noise, and noise is what fills a resting order at the worst moment.

    A *negative* confidence means the assessment is malformed, not merely
    uncertain - it falls out of the CONSOLIDATION formula when range compression
    exceeds 1.4, which is a contradiction in terms. That is worth refusing.

    Ordinary low confidence is NOT. The floor was originally set at 0.45, which
    on measured data cut at the median and refused 47% of everything - including
    every NEUTRAL assessment, whose confidence is hardcoded to 0.3. That was a
    directional strategy's instinct applied to a market-making one: a maker is
    not betting the pattern continues, it is collecting a premium on a mispriced
    favourite, and a calm featureless book is the *best* place to do that, not
    the worst. VOLATILE_CHOP is the regime a maker should genuinely avoid, and
    it is refused by name above.

    ``|z|`` is the real one. It is the distance from spot to the strike in
    standard deviations of the terminal distribution, and a contract sitting
    near the middle is decided by whichever way the settlement index happens to
    round - not by anything the model knows. In the first session the single
    losing position was NO at 0.61 with spot close to the strike; it settled
    YES and cost more than the four managed trades made.
    """
    if regime.upper() in {name.upper() for name in blocked_regimes}:
        return GateResult(
            False,
            f"regime {regime} carries no usable structure",
        )
    if regime_confidence < minimum_confidence:
        return GateResult(
            False,
            f"regime {regime} identified with only {regime_confidence:.0%} "
            f"confidence (floor {minimum_confidence:.0%})",
        )
    if abs(z_score) < minimum_abs_z:
        return GateResult(
            False,
            f"spot is {abs(z_score):.2f}σ from the strike, inside the "
            f"{minimum_abs_z:.2f}σ floor; too close to call",
        )
    return ALLOW


def check_toxicity(
    toxicity: Decimal,
    maximum: Decimal,
) -> GateResult:
    """Stand aside when resting liquidity is overwhelmingly one-sided.

    Bartlett & O'Hara (2026), over 41.6M Kalshi trades, adapt the VPIN
    order-flow-toxicity metric and find that **one-sided flow predicts maker
    losses in single-name markets** - and a 15-minute crypto strike is about as
    single-name as a market gets. A book with everything resting on one side is
    a book where somebody has already taken a view, and taking the other side of
    it is how a passive strategy gets picked off.
    """
    if toxicity > maximum:
        return GateResult(
            False,
            f"resting liquidity is {toxicity:.0%} one-sided (limit "
            f"{maximum:.0%}); one-sided flow predicts maker losses",
        )
    return ALLOW


def check_concurrency(
    open_positions: int,
    maximum: int,
) -> GateResult:
    if open_positions >= maximum:
        return GateResult(
            False,
            f"{open_positions} positions already open (limit {maximum})",
        )
    return ALLOW


def check_capital_headroom(
    committed: Decimal,
    requested: Decimal,
    ceiling: Decimal,
) -> GateResult:
    """Stop one strategy committing the whole balance.

    This is a ceiling, not an allocation: capital is a shared pool and this
    strategy competes for it on expected value. The limit exists only so that
    one strategy cannot starve the other of any capacity to act.
    """
    if committed + requested > ceiling:
        return GateResult(
            False,
            f"${committed + requested:.2f} would exceed the ${ceiling:.2f} "
            "capital ceiling for this strategy",
        )
    return ALLOW
