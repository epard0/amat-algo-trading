"""Pure order-book analytics for prediction markets.

Everything here is side-effect free and takes plain price/size levels, so each
measure is testable without a socket.

The measures are not generic market-microstructure boilerplate; each one exists
because a specific piece of published Kalshi research says it predicts something
we care about.

**Bartlett & O'Hara (2026), _Adverse Selection in Prediction Markets: Evidence
from Kalshi_** (41.6M trades). Two findings drive most of this module:

* Adapting the VPIN order-flow-toxicity metric, **one-sided order flow predicts
  maker losses** in single-name markets. A 15-minute crypto strike is about as
  single-name as a market gets, so book one-sidedness is the toxicity proxy we
  can compute from a depth snapshot.
* Traders buy YES about **61%** of the time while YES settles only about **32%**
  of the time. That is the favourite-longshot bias stated as an order-flow fact,
  and it says the *cheap* side of a prediction market is where the crowd
  overpays.

**Becker, _Prediction Market Microstructure_.** Takers earn −1.12% and makers
+1.12% on average; at 1c, YES is −41% EV against NO at +23%. Crucially for us,
the maker-taker gap is smallest in the **Finance** category (0.17pp) — which is
the category 15-minute crypto sits in, and the reason this bot stopped trying to
capture a maker premium that is close to absent in the markets it trades.

Kalshi's book is quoted as resting **bids on both sides**: a YES bid at ``p`` is
a NO ask at ``1 - p`` for the same size. Every function here takes that shape
directly rather than reconstructing a two-sided book that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")

Level = tuple[Decimal, Decimal]  # (price, size)


@dataclass(frozen=True)
class BookAnalysis:
    """What one depth snapshot says about a market."""

    yes_bid: Decimal
    yes_ask: Decimal
    yes_depth: Decimal
    no_depth: Decimal
    # Size resting at the touch, as distinct from the depth fields above, which
    # sum every level. An exit can only be filled against what is actually at
    # the price it is sold at, so checking a full-book depth would wave through
    # a position the touch cannot absorb.
    yes_bid_size: Decimal
    no_bid_size: Decimal
    spread: Decimal
    mid: Decimal
    microprice: Decimal
    imbalance: Decimal
    toxicity: Decimal
    yes_slope: Decimal
    no_slope: Decimal
    levels_yes: int
    levels_no: int

    @property
    def microprice_edge(self) -> Decimal:
        """How far the size-weighted fair value sits from the naive midpoint.

        Positive means depth is leaning toward YES being underpriced at the mid.
        """
        return self.microprice - self.mid

    @property
    def is_thin(self) -> bool:
        """Whether either side is too shallow to trade against meaningfully."""
        return self.yes_depth <= ZERO or self.no_depth <= ZERO


def _top(levels: Sequence[Level]) -> Level | None:
    """Best (highest) resting bid and its size."""
    if not levels:
        return None
    return max(levels, key=lambda item: item[0])


def total_depth(levels: Sequence[Level]) -> Decimal:
    return sum((size for _, size in levels), ZERO)


def book_imbalance(yes_depth: Decimal, no_depth: Decimal) -> Decimal:
    """Signed depth imbalance in ``[-1, +1]``.

    ``+1`` means every resting contract is on the YES side, ``-1`` the NO side,
    ``0`` a balanced book. This is the raw directional-pressure reading: a book
    leaning heavily one way is a book where the crowd has already taken a view.
    """
    total = yes_depth + no_depth
    if total <= ZERO:
        return ZERO
    return (yes_depth - no_depth) / total


def flow_toxicity(yes_depth: Decimal, no_depth: Decimal) -> Decimal:
    """One-sidedness of resting liquidity, in ``[0, 1]``.

    This is a static stand-in for VPIN. The real metric is built from *executed*
    volume over time; from a single depth snapshot the observable analogue is how
    lopsided the resting book is. Bartlett & O'Hara find one-sided flow predicts
    maker losses in single-name markets, so a high reading is a reason to stand
    aside — or, if entering anyway, to take the side the pressure is on rather
    than the side it is against.

    Zero means perfectly balanced; one means everything rests on a single side.
    """
    return abs(book_imbalance(yes_depth, no_depth))


def microprice(
    yes_bid: Decimal,
    yes_ask: Decimal,
    bid_size: Decimal,
    ask_size: Decimal,
) -> Decimal:
    """Size-weighted fair value, which beats the midpoint on a lopsided book.

    Weighting is deliberately crossed - the *bid* price is weighted by the *ask*
    size. Depth on one side is pressure pushing price toward the other, so a
    heavy bid with a thin offer means the true value sits nearer the ask. Using
    the plain midpoint on such a book reads the fair value backwards by up to
    half the spread, and on a 1c market that is the entire edge.
    """
    total = bid_size + ask_size
    if total <= ZERO:
        return (yes_bid + yes_ask) / Decimal("2")
    return ((yes_bid * ask_size) + (yes_ask * bid_size)) / total


def book_slope(levels: Sequence[Level], reference: Decimal) -> Decimal:
    """Contracts available per cent of price concession, from the touch.

    A steep slope (large number) is a deep, forgiving book; a shallow one means
    a few contracts move the price a long way. This is the "tight spread but
    shallow depth" case: the headline spread looks fine and the second trade
    fills far worse.

    Returns zero when there is nothing to measure, so callers can treat zero as
    "no liquidity gradient" rather than special-casing empty books.
    """
    if not levels or reference <= ZERO:
        return ZERO
    concession = sum(
        (size * (ONE + abs(price - reference)) for price, size in levels),
        ZERO,
    )
    distance = sum((abs(price - reference) for price, _ in levels), ZERO)
    if distance <= ZERO:
        return concession
    return concession / (distance / CENT)


def analyse(
    yes_bid_levels: Sequence[Level],
    no_bid_levels: Sequence[Level],
) -> BookAnalysis | None:
    """Full read of one depth snapshot, or None if the book is unusable.

    Both arguments are *resting bids*, which is how Kalshi publishes a book. The
    YES ask is derived as ``1 - best NO bid``, because that is definitionally
    what it is - there is no separate ask book to fetch.
    """
    yes_top = _top(yes_bid_levels)
    no_top = _top(no_bid_levels)
    if yes_top is None or no_top is None:
        return None

    yes_bid, yes_bid_size = yes_top
    no_bid, no_bid_size = no_top
    yes_ask = ONE - no_bid
    if yes_ask <= yes_bid:
        # A crossed or locked book is not tradeable and usually means a stale
        # snapshot. Refusing beats pricing off an impossible spread.
        return None

    yes_depth = total_depth(yes_bid_levels)
    no_depth = total_depth(no_bid_levels)
    return BookAnalysis(
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        yes_depth=yes_depth,
        no_depth=no_depth,
        yes_bid_size=yes_bid_size,
        no_bid_size=no_bid_size,
        spread=yes_ask - yes_bid,
        mid=(yes_bid + yes_ask) / Decimal("2"),
        microprice=microprice(yes_bid, yes_ask, yes_bid_size, no_bid_size),
        imbalance=book_imbalance(yes_depth, no_depth),
        toxicity=flow_toxicity(yes_depth, no_depth),
        yes_slope=book_slope(yes_bid_levels, yes_bid),
        no_slope=book_slope(no_bid_levels, no_bid),
        levels_yes=len(yes_bid_levels),
        levels_no=len(no_bid_levels),
    )


def crowd_side(analysis: BookAnalysis) -> str:
    """Which outcome the resting crowd is leaning on: "yes", "no" or "flat".

    Bartlett & O'Hara's headline order-flow fact is that traders buy YES 61% of
    the time while YES settles 32% of the time. Where the crowd is leaning is
    therefore *informative about where it is overpaying*, not about where the
    value is.
    """
    if analysis.imbalance > Decimal("0.15"):
        return "yes"
    if analysis.imbalance < Decimal("-0.15"):
        return "no"
    return "flat"


def contrarian_bonus(
    analysis: BookAnalysis,
    outcome: str,
    weight: Decimal = ZERO,
) -> Decimal:
    """Edge adjustment for trading with or against resting depth.

    **Ships disabled (``weight=0``), and the reason is the sign, not the size.**

    Resting bids on Kalshi are *maker* orders. Becker measures makers at +1.12%
    and takers at -1.12%, which argues that resting depth marks where the
    profitable cohort is sitting, and that we should lean *toward* it. But
    Bartlett & O'Hara's order-flow fact - traders buy YES 61% of the time while
    YES settles 32% - argues that visible pressure marks where the crowd is
    overpaying, and that we should lean *against* it.

    Those two readings give opposite signs, and the published abstracts do not
    settle which applies to a 15-minute crypto strike. A directional adjustment
    with an unverified sign is worse than none: it would push every marginal
    trade the wrong way while looking like a refinement.

    So the mechanism is here, tested, and inert. Every entry logs the imbalance
    it saw, so the sign can be *fitted* from this bot's own settled fills - the
    same discipline the price band is under. Set ``SCALP_CROWD_WEIGHT`` above
    zero only once that fit exists.

    Sign convention as written: positive for taking the opposite side of resting
    depth, negative for joining it.
    """
    lean = crowd_side(analysis)
    if lean == "flat":
        return ZERO
    magnitude = abs(analysis.imbalance) * weight
    return -magnitude if lean == outcome else magnitude
