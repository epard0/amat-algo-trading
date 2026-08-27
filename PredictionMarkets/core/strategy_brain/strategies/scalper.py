"""Pure maker-side scalping logic.

Everything here is side-effect free: no clock, no network, no ledger. The
strategy decides *what* to quote and *when* to leave; ``execution/`` decides how
to get that to the venue.

The edge this encodes is not a better forecast. It is the two effects measured
in Bürgi, Deng & Whelan (2026), *Makers or Takers: The Economics of the Kalshi
Prediction Market* (GWU CER WP 2026-001), over 313,972 contracts:

* **Favourite-longshot bias.** Contracts under 10c returned worse than -60%;
  contracts over 70c showed statistically significant positive post-fee returns.
* **Makers beat takers.** -9.64% against -31.46% on average, and makers buying
  at 50c and above earned +2.6%.

So this strategy buys only favourites, and quotes rather than crosses.

Two caveats travel with those numbers and must not be quietly dropped. The
paper's sample is 24-hour-to-10-day markets, not 15-minute crypto markets; and
Kalshi began charging maker fees after April 2025, so part of the measured maker
premium is historical. Both are why the band is configurable and why every quote
is logged with what it would take to re-fit it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


CENT = Decimal("0.01")
ONE = Decimal("1")
ZERO = Decimal("0")


@dataclass(frozen=True)
class ScalpQuote:
    """A price we are willing to rest at, and the arithmetic behind it.

    ``price`` is what the chosen outcome costs; ``yes_leg_price`` is what goes on
    the wire. They differ for NO, because Kalshi quotes one YES book: resting a
    sell at 0.10 on that book *is* buying NO at 0.90. Keeping both means the
    band and the EV are always judged on what we actually pay, while the order
    still carries the price the venue expects.
    """

    price: Decimal
    probability: Decimal
    ev_per_dollar: Decimal
    spread: Decimal
    joins_bid: bool
    outcome: str = "yes"
    yes_leg_price: Decimal = ZERO
    book_side: str = "bid"

    @property
    def improves_bid(self) -> bool:
        return not self.joins_bid


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""
    ev_hold: Decimal = ZERO
    ev_exit: Decimal = ZERO


def ev_per_dollar(probability: Decimal, price: Decimal, fee: Decimal = ZERO) -> Decimal:
    """Expected profit per dollar committed, fees included.

    This is the common currency the candidate pool ranks on. A probability edge
    in points and an arbitrage edge in cents are not otherwise comparable, and
    ranking them on the wrong scale would systematically favour whichever
    strategy happened to produce bigger numbers.

    Returns zero for a nonsensical cost basis rather than raising: this is used
    inside a ranking loop, and one malformed market should not abort a cycle.
    """
    cost = price + fee
    if cost <= ZERO:
        return ZERO
    return ((probability * ONE) - cost) / cost


def break_even_win_rate(price: Decimal, fee: Decimal = ZERO) -> Decimal:
    """The win rate a position must clear simply to lose nothing.

    This is why a price band is the win-rate constraint. Confining entries to
    prices at or above 0.60 means any strategy that is EV-positive at all must
    win more than 60% of the time, so a >=55% target is a consequence of the
    band rather than an independent objective that could be gamed by buying
    expensive favourites.
    """
    return price + fee


def in_band(price: Decimal, low: Decimal, high: Decimal) -> bool:
    """Whether a price sits inside the favourite band, inclusive at both ends."""
    return low <= price <= high


def build_quote(
    *,
    probability: Decimal,
    yes_bid: Decimal,
    yes_ask: Decimal,
    band_low: Decimal,
    band_high: Decimal,
    min_ev_per_dollar: Decimal,
    fee: Decimal = ZERO,
    tick: Decimal = CENT,
) -> ScalpQuote | None:
    """Choose a resting price on whichever side is the favourite, or decline.

    **Both sides are considered.** The strike on a 15-minute market is set at the
    interval's opening price, so P(yes) wanders freely and is a longshot roughly
    as often as it is a favourite. A YES-only quoter would therefore sit idle
    through every interval where the *NO* side is the favourite - which is most
    of them, and exactly the trades the reference paper says are the profitable
    ones. Buying NO is expressed as resting a sell on the YES book, since Kalshi
    has only that one book.

    Improves the touch by one tick where the spread leaves room, and joins it
    otherwise. It never prices through the opposite side: crossing is the taker
    path, and a ``post_only`` order priced there is simply rejected.
    """
    if yes_bid <= ZERO or yes_ask <= ZERO or yes_ask <= yes_bid:
        return None

    spread = yes_ask - yes_bid
    improves = spread > tick
    candidates: list[ScalpQuote] = []

    # --- YES: rest a bid, improving it where there is room ---
    yes_price = (yes_bid + tick) if improves else yes_bid
    yes_price = yes_price.quantize(CENT, rounding=ROUND_HALF_UP)
    if ZERO < yes_price < yes_ask and in_band(yes_price, band_low, band_high):
        edge = ev_per_dollar(probability, yes_price, fee)
        if edge >= min_ev_per_dollar:
            candidates.append(
                ScalpQuote(
                    price=yes_price,
                    probability=probability,
                    ev_per_dollar=edge,
                    spread=spread,
                    joins_bid=not improves,
                    outcome="yes",
                    yes_leg_price=yes_price,
                    book_side="bid",
                )
            )

    # --- NO: rest a sell on the YES book; buying NO at 1 - that price ---
    leg = (yes_ask - tick) if improves else yes_ask
    leg = leg.quantize(CENT, rounding=ROUND_HALF_UP)
    no_price = (ONE - leg).quantize(CENT, rounding=ROUND_HALF_UP)
    no_probability = ONE - probability
    if ZERO < leg and leg > yes_bid and in_band(no_price, band_low, band_high):
        edge = ev_per_dollar(no_probability, no_price, fee)
        if edge >= min_ev_per_dollar:
            candidates.append(
                ScalpQuote(
                    price=no_price,
                    probability=no_probability,
                    ev_per_dollar=edge,
                    spread=spread,
                    joins_bid=not improves,
                    outcome="no",
                    yes_leg_price=leg,
                    book_side="ask",
                )
            )

    if not candidates:
        return None
    return max(candidates, key=lambda item: item.ev_per_dollar)


def build_taker_entry(
    *,
    probability: Decimal,
    yes_bid: Decimal,
    yes_ask: Decimal,
    band_low: Decimal,
    band_high: Decimal,
    min_ev_per_dollar: Decimal,
    fee: Decimal = ZERO,
) -> ScalpQuote | None:
    """Price an entry that crosses the spread now, or decline.

    This is the primary entry path. The maker version above is kept, and is
    still the better economics on the reference paper's numbers, but it could
    not be tested: across five paper sessions a resting quote filled **zero**
    times, because the honest fill model only credits a fill when the book
    trades *through* the resting price. A strategy whose main mechanism never
    executes cannot be evaluated, and the latency of quoting-then-waiting was
    being paid for nothing.

    Crossing pays the spread, so the edge is computed at the price actually
    paid - the ask for YES, ``1 - bid`` for NO. Every gate that made the taker
    fallback profitable is preserved by construction: the band is checked
    against the crossed price, and the caller applies the higher EV threshold
    that separated the winners from the marginal trades.
    """
    if yes_bid <= ZERO or yes_ask <= ZERO or yes_ask <= yes_bid:
        return None

    spread = yes_ask - yes_bid
    candidates: list[ScalpQuote] = []

    # --- YES: lift the offer ---
    yes_price = yes_ask.quantize(CENT, rounding=ROUND_HALF_UP)
    if ZERO < yes_price < ONE and in_band(yes_price, band_low, band_high):
        edge = ev_per_dollar(probability, yes_price, fee)
        if edge >= min_ev_per_dollar:
            candidates.append(
                ScalpQuote(
                    price=yes_price,
                    probability=probability,
                    ev_per_dollar=edge,
                    spread=spread,
                    joins_bid=False,
                    outcome="yes",
                    yes_leg_price=yes_price,
                    book_side="bid",
                )
            )

    # --- NO: hit the bid on the YES book, which buys NO at 1 - that price ---
    no_price = (ONE - yes_bid).quantize(CENT, rounding=ROUND_HALF_UP)
    no_probability = ONE - probability
    if ZERO < no_price < ONE and in_band(no_price, band_low, band_high):
        edge = ev_per_dollar(no_probability, no_price, fee)
        if edge >= min_ev_per_dollar:
            candidates.append(
                ScalpQuote(
                    price=no_price,
                    probability=no_probability,
                    ev_per_dollar=edge,
                    spread=spread,
                    joins_bid=False,
                    outcome="no",
                    yes_leg_price=yes_bid,
                    book_side="ask",
                )
            )

    if not candidates:
        return None
    return max(candidates, key=lambda item: item.ev_per_dollar)


def should_requote(
    *,
    quoted_bid: Decimal,
    current_bid: Decimal,
    tick: Decimal = CENT,
    max_ticks: int = 2,
) -> bool:
    """Whether the book has moved far enough that our resting price is stale.

    A quote left behind a moving book is the adverse-selection case: it fills
    exactly when the move has already gone against us.
    """
    if tick <= ZERO:
        return False
    drift = abs(current_bid - quoted_bid) / tick
    return drift >= Decimal(max_ticks)


def decide_exit(
    *,
    probability: Decimal,
    cost_basis: Decimal,
    current_bid: Decimal,
    exit_fee: Decimal,
    exit_probability_floor: Decimal,
    exit_buffer: Decimal,
    outcome: str = "position",
) -> ExitDecision:
    """Whether to sell the position back into the book now.

    Two independent triggers, because they catch different failures:

    * the model has turned outright (``probability`` below the floor), and
    * holding is simply worth less than leaving, even with the model unchanged.

    Both compare per-contract values. ``ev_hold`` is what the position is worth
    if carried to settlement; ``ev_exit`` is what the book will actually pay
    right now, after the exit fee. Selling is only better when it clears
    ``exit_buffer``, which stops a position churning across a one-tick wobble.
    """
    ev_hold = (probability * ONE) - cost_basis
    ev_exit = current_bid - exit_fee - cost_basis

    if probability < exit_probability_floor:
        return ExitDecision(
            True,
            f"model P({outcome})={probability:.1%} fell below the exit floor "
            f"{exit_probability_floor:.1%}",
            ev_hold,
            ev_exit,
        )
    if ev_exit > ev_hold + exit_buffer:
        return ExitDecision(
            True,
            f"selling at {current_bid:.2f} nets {ev_exit:.4f}/contract against "
            f"{ev_hold:.4f} for holding",
            ev_hold,
            ev_exit,
        )
    return ExitDecision(False, "", ev_hold, ev_exit)


def position_size(
    *,
    budget: Decimal,
    price: Decimal,
    fee_per_contract: Decimal,
    depth: Decimal,
    minimum: Decimal = ONE,
) -> Decimal:
    """Contracts to quote, clipped to budget and to resting depth.

    Rounds down, like every other sizing path in this project: an order for
    more than the budget covers is a rejection, and an order for more than the
    book holds is a partial fill at a worse average price.
    """
    unit = price + fee_per_contract
    if unit <= ZERO or budget <= ZERO:
        return ZERO
    affordable = (budget / unit).quantize(ONE, rounding=ROUND_DOWN)
    capped = min(affordable, depth.quantize(ONE, rounding=ROUND_DOWN))
    return capped if capped >= minimum else ZERO
