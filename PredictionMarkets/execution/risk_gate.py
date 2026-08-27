"""Pure risk gates that stand between a model signal and a real order.

Everything here is deliberately side-effect free so each guard can be tested
without a network, a clock, or a ledger. The bot calls them in order; the first
gate that refuses stops the trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Sequence


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.allowed


ALLOW = GateResult(True)


def market_implied_probability(yes_bid: Decimal, yes_ask: Decimal) -> Decimal:
    """Mid-market implied probability of YES.

    Falls back to whichever side exists when the book is one-sided; a fully
    empty book returns 0.5 so the divergence gate neither passes nor fails a
    trade on missing data (the liquidity gate handles that case).
    """
    quotes = [quote for quote in (yes_bid, yes_ask) if quote > 0]
    if not quotes:
        return Decimal("0.5")
    return sum(quotes, Decimal("0")) / Decimal(len(quotes))


def check_model_market_divergence(
    model_probability_yes: Decimal,
    yes_bid: Decimal,
    yes_ask: Decimal,
    maximum_divergence: Decimal,
) -> GateResult:
    """Refuse trades where the model wildly contradicts a real market.

    A 60-point disagreement is not a 60-point edge. On a market that settles
    from a different index than our price feed, it is overwhelmingly evidence
    of stale or mismatched data. Requiring agreement within a band keeps the
    bot trading small, plausible edges instead of data bugs.
    """
    implied = market_implied_probability(yes_bid, yes_ask)
    divergence = abs(model_probability_yes - implied)
    if divergence > maximum_divergence:
        return GateResult(
            False,
            f"model P(yes)={model_probability_yes:.1%} disagrees with market "
            f"{implied:.1%} by {divergence:.1%} (limit {maximum_divergence:.1%}); "
            "treating as a data fault, not an edge",
        )
    return ALLOW


def check_executable_edge_ceiling(
    edge: Decimal,
    maximum_edge: Decimal,
) -> GateResult:
    """Refuse edges too large to be real — the same logic as the divergence gate,
    applied in price space at the moment of execution.

    ``check_model_market_divergence`` compares the model to the *discovery
    snapshot* in probability space. It cannot see a bad re-read of the live book
    seconds later, because by then the comparison has already passed. The live
    edge is computed from that re-read, so a quote that comes back wrong appears
    as free money rather than as the fault it is.

    That is not hypothetical. Across the first 13 live trades, every one whose
    executable edge exceeded ~19 points lost, and every one below ~14 points
    won. The losers were not unlucky: a book that appears to offer a 50-point
    edge on a market whose snapshot was fairly priced seconds earlier is a data
    fault, and fractional Kelly sizes hardest into exactly that fault.

    A ceiling costs a genuine windfall occasionally. Taking the windfall cost
    real money every time it was offered.
    """
    if maximum_edge <= 0:  # ceiling disabled
        return ALLOW
    if edge > maximum_edge:
        return GateResult(
            False,
            f"executable edge {edge:.1%} exceeds the {maximum_edge:.1%} ceiling; "
            "an edge this large against a live book is a data fault, not an "
            "opportunity",
        )
    return ALLOW


def check_quote_sanity(
    snapshot_price: Decimal,
    live_price: Decimal,
    maximum_improvement: Decimal,
) -> GateResult:
    """Refuse a live quote that improved implausibly against the snapshot.

    ``_place_candidate`` deliberately re-reads the book before sizing because
    the discovery snapshot is seconds stale. A price that moved *against* us is
    the expected case and is handled by re-checking the edge. A price that moved
    sharply *in our favour* is the suspicious one: thin 15-minute books do not
    hand out 45-cent discounts between two reads, and treating it as real
    inverts the purpose of the re-read.

    Only improvement is bounded. An adverse move needs no ceiling here — it
    shrinks the edge and the existing ``live_edge`` gate already rejects it.
    """
    if maximum_improvement <= 0:  # check disabled
        return ALLOW
    improvement = snapshot_price - live_price
    if improvement > maximum_improvement:
        return GateResult(
            False,
            f"live quote {live_price:.2f} improved {improvement:.2f} on the "
            f"{snapshot_price:.2f} snapshot (limit {maximum_improvement:.2f}); "
            "refusing to size into an implausible book read",
        )
    return ALLOW


def late_entry_thresholds(
    seconds_to_close: int,
    *,
    late_window_seconds: int,
    min_probability: Decimal,
    min_edge: Decimal,
    late_min_probability: Decimal,
    late_min_edge: Decimal,
) -> tuple[Decimal, Decimal, bool]:
    """Return (probability_floor, edge_floor, is_late) for this moment.

    Near expiry the model is measuring something genuinely easier. The terminal
    standard deviation is ``vol * sqrt(minutes_remaining)``, so as the horizon
    collapses the distribution tightens and the same spot-to-strike distance
    becomes far more decisive. A 3% edge with 30 seconds left is not the same
    claim as a 3% edge with three minutes left, and demanding an identical 5%
    floor for both throws away the trades the model is most certain about.

    The exchange fee schedule points the same direction. Kalshi's taker fee is
    ``0.07 * C * P * (1-P)``, which peaks at a 50c price and shrinks toward the
    ends: a contract at 95c costs roughly 0.0033 in fee, so a 2% edge clears it
    several times over, while the same 2% edge at 50c would not.

    So the late window *lowers* the edge floor and *raises* the probability
    floor. It is not a relaxation — it trades one requirement for a stricter
    one, and ``check_settlement_margin`` adds the guard that actually matters.
    """
    if seconds_to_close > late_window_seconds:
        return (min_probability, min_edge, False)
    # Never let the late window admit something the standing floors would not
    # on the probability side, nor demand more edge than the normal rule.
    return (
        max(min_probability, late_min_probability),
        min(min_edge, late_min_edge),
        True,
    )


def check_settlement_margin(
    z_score: Decimal,
    minimum_sigma: Decimal,
) -> GateResult:
    """Require the strike to be far enough away that settlement is not a toss-up.

    This is the guard that makes late-market entry survivable, and it exists
    because a high probability near expiry is ambiguous. ``P(yes)=0.97`` can
    mean spot is a long way above the strike, or that it is a hair above with
    almost no time left. The probability is identical; the risk is not.

    The bot prices from Kraken spot while Kalshi settles on a CF Benchmarks
    one-minute average. Those two agree closely most of the time, but not
    exactly — and a strike sitting inside that disagreement is decided by which
    index you happened to read, not by the model. Distance measured in standard
    deviations is what separates the two cases, so the late window is gated on
    ``|z|`` rather than on probability.

    Rejecting on this costs some genuinely safe trades near the strike. That is
    the intended trade: those are exactly the ones whose outcome the model
    cannot actually see.
    """
    if minimum_sigma <= 0:  # disabled
        return ALLOW
    margin = abs(z_score)
    if margin < minimum_sigma:
        return GateResult(
            False,
            f"settlement margin {margin:.2f}σ is inside the {minimum_sigma:.2f}σ "
            "floor; this close to the strike the outcome is decided by index "
            "disagreement, not by the model",
        )
    return ALLOW


def expected_value_per_contract(
    probability: Decimal,
    price: Decimal,
    fee_per_contract: Decimal,
) -> Decimal:
    """Expected profit on one contract, after fees.

    A binary contract bought at ``price`` pays 1 on a win and 0 on a loss, so
    the expectation is ``p - price - fee``. Stated directly rather than inferred
    from an edge threshold, because "is this trade worth taking at all" is a
    question about money, and a small positive expectation repeated often is
    still a positive expectation.
    """
    return probability - price - fee_per_contract


def check_candle_freshness(
    latest_candle_time: datetime,
    now: datetime,
    maximum_age_seconds: int,
) -> GateResult:
    """Refuse to price a 15-minute market from a stale spot feed."""
    if latest_candle_time.tzinfo is None:
        latest_candle_time = latest_candle_time.replace(tzinfo=timezone.utc)
    age = (now - latest_candle_time).total_seconds()
    if age > maximum_age_seconds:
        return GateResult(
            False,
            f"spot candles are {age:.0f}s old (limit {maximum_age_seconds}s)",
        )
    return ALLOW


def kelly_position_fraction(
    probability: Decimal,
    price: Decimal,
    kelly_multiplier: Decimal,
    cap: Decimal,
    maximum_edge: Decimal | None = None,
) -> Decimal:
    """Fraction of bankroll to risk, from fractional Kelly, hard-capped.

    A binary contract bought at ``price`` pays 1. Full Kelly for that bet is
    ``(p - price) / (1 - price)``. We take a fraction of it (default a quarter)
    and never exceed ``cap`` (POSITION_FRACTION). Kelly is what makes the
    compounding safe: stake shrinks automatically as the edge shrinks, instead
    of betting a flat 20% on a 1-point edge and a 20-point edge alike.

    ``maximum_edge`` bounds the numerator before Kelly sees it. Kelly is optimal
    only when the probability it is handed is *correct*; fed an overstated edge
    it does not merely overbet, it overbets in proportion to the error. The
    largest observed live position was 13.6 contracts against a typical 2, sized
    from a 51-point edge that did not exist. Clamping the numerator keeps a
    mispriced input from becoming a mis-sized position, independently of the
    gate that should have rejected it.
    """
    if kelly_multiplier <= 0:
        return cap
    if not Decimal("0") < price < Decimal("1"):
        return Decimal("0")
    edge = probability - price
    if maximum_edge is not None and maximum_edge > 0:
        edge = min(edge, maximum_edge)
    full_kelly = edge / (Decimal("1") - price)
    if full_kelly <= 0:
        return Decimal("0")
    return min(cap, (full_kelly * kelly_multiplier))


def confidence_multiplier(
    *,
    probability: Decimal,
    floor: Decimal,
    full: Decimal,
    gain: Decimal,
) -> Decimal:
    """Scale a sized budget upward as the model's confidence rises.

    Kelly already prices confidence *through the price*: ``(p - price) /
    (1 - price)`` grows as the price approaches 1, so a favourite earns a
    larger fraction for the same edge. What Kelly cannot see is that this
    account's own record is lopsided by probability - the scalper, which has
    taken every live trade here, sizes with no reference to ``p`` at all and
    spends its entire per-strategy budget on a 62% quote and a 92% quote alike.

    This is the knob that separates them, and it is deliberately a *multiplier
    on the budget* rather than a second edge term: it cannot change the sign of
    a position, cannot admit a trade the gates refused, and is clamped by the
    caller against the same hard ceiling as before. Turning it off is exact -
    ``gain <= 1`` returns 1 and the sizing path is unchanged, byte for byte.

    Below ``floor`` nothing is added, because a bare majority is not
    confidence. Above ``full`` nothing further is added, because the model's
    calibration at the extremes is measured on very few settled trades and
    extrapolating past it would size on an unmeasured belief.
    """
    if gain <= Decimal("1"):
        return Decimal("1")
    if full <= floor:
        return gain if probability >= full else Decimal("1")
    if probability <= floor:
        return Decimal("1")
    if probability >= full:
        return gain
    span = (probability - floor) / (full - floor)
    return Decimal("1") + (gain - Decimal("1")) * span


def required_probability(
    *,
    base: Decimal,
    intended_exposure: Decimal,
    full_exposure: Decimal,
    uplift: Decimal,
) -> Decimal:
    """The probability floor this position must clear, given how big it is.

    Confidence scaling makes the model's best reads into its biggest positions.
    That is the point of it, and it is also the risk: the same multiplier that
    funds a 0.92 read hardest funds a 0.71 read harder than it used to, and the
    losses that hurt are by construction the large ones. A flat floor treats a
    trade risking 2% of the account and one risking 25% as the same decision.

    So the bar rises with the stake. It is a floor on the *model's* probability
    and nothing else - it cannot make a position larger, cannot admit anything,
    and returns ``base`` exactly when ``uplift`` is zero.

    Capped below 1: a floor of 1.0 is not a strict gate, it is a stopped
    strategy, and one that would stop silently.
    """
    if uplift <= Decimal("0") or full_exposure <= Decimal("0"):
        return base
    intensity = min(Decimal("1"), max(Decimal("0"), intended_exposure) / full_exposure)
    return min(Decimal("0.99"), base + uplift * intensity)


def check_size_scaled_probability(
    probability: Decimal,
    intended_exposure: Decimal,
    required: Decimal,
) -> GateResult:
    """Refuse a position too large for the confidence behind it."""
    if probability < required:
        return GateResult(
            False,
            f"P={probability:.1%} is below the {required:.1%} floor this size "
            f"requires ({intended_exposure:.1%} of the account); a bigger "
            "position needs a better reason, not merely a positive one",
        )
    return ALLOW


def consecutive_losses(statuses: Sequence[str]) -> int:
    """Count losses at the head of a newest-first settled-status sequence."""
    streak = 0
    for status in statuses:
        if status == "LOST":
            streak += 1
        elif status in {"WON", "VOID"}:
            break
    return streak


def check_capital_breaker(
    *,
    current_balance: Decimal,
    peak_balance: Decimal,
    settled_statuses: Iterable[str],
    maximum_consecutive_losses: int,
    maximum_drawdown_fraction: Decimal,
) -> GateResult:
    """Halt trading on a losing streak or a drawdown from the session peak.

    Compounding only works if a bad regime cannot run the balance to zero. This
    is the backstop that stops the bot rather than letting it keep sizing off a
    shrinking bankroll.
    """
    streak = consecutive_losses(list(settled_statuses))
    if streak >= maximum_consecutive_losses:
        return GateResult(
            False,
            f"{streak} consecutive losses reached the "
            f"MAX_CONSECUTIVE_LOSSES={maximum_consecutive_losses} limit",
        )
    if peak_balance > 0:
        drawdown = (peak_balance - current_balance) / peak_balance
        if drawdown >= maximum_drawdown_fraction:
            return GateResult(
                False,
                f"drawdown {drawdown:.1%} from peak ${peak_balance:.2f} reached "
                f"the MAX_DRAWDOWN_FRACTION={maximum_drawdown_fraction:.1%} limit",
            )
    return ALLOW
