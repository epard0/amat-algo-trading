"""Orchestration for maker-side scalping.

Lives outside ``bot.py`` because that file already carries the predictor's whole
lifecycle. The engine owns three things and nothing else:

1. the quotes currently resting (or simulated as resting),
2. the positions those quotes became, and when to leave them,
3. the arithmetic that turns a market plus a prediction into a price to quote.

It never decides *whether* the bot may trade. Live prerequisites, halts and mode
switches all stay in ``bot.py``, so enabling scalping cannot grant a permission
that ``.env`` withheld.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Callable

from loguru import logger

from core.strategy_brain.strategies.orderbook import contrarian_bonus
from core.strategy_brain.strategies.scalper import (
    ScalpQuote,
    build_quote,
    build_taker_entry,
    ev_per_dollar,
    position_size,
)
from execution import scalp_gate
from execution.kalshi_client import KalshiMarket, OrderPlan
from execution.order_lifecycle import (
    QuoteBook,
    QuoteState,
    RestingQuote,
    evaluate_quote,
)
from execution.position_book import PositionBook, Reservation
from execution.risk_gate import (
    check_size_scaled_probability,
    required_probability,
)
from execution.trade_ledger import calculate_paper_fill


STRATEGY = "scalper"
ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")


def entry_touch_depth(market: Any, quote: ScalpQuote, order_style: str) -> Decimal:
    """Executable/queue depth for the exact side an entry uses.

    Kalshi publishes one YES book. Taker YES lifts its ask while taker NO hits
    its bid; maker orders instead join the selected YES-book side. Keeping the
    mapping in one pure function makes asymmetric-book regressions explicit.
    """
    if order_style == "taker":
        return market.yes_ask_size if quote.outcome == "yes" else market.yes_bid_size
    return market.yes_bid_size if quote.book_side == "bid" else market.yes_ask_size


def immediate_exit_book(market: Any, outcome: str) -> tuple[Decimal, Decimal]:
    """Executable bid and depth available to flatten a newly bought outcome."""
    if outcome == "yes":
        return market.yes_bid, market.yes_bid_size
    return ONE - market.yes_ask, market.yes_ask_size


def risk_bounded_contracts(
    *,
    proposed: Decimal,
    entry_price: Decimal,
    entry_fee: Decimal,
    exit_price: Decimal,
    exit_fee: Decimal,
    exit_depth: Decimal,
    maximum_exit_loss: Decimal,
) -> Decimal:
    """Clip an otherwise valid order to its immediate-flatten risk budget."""
    if proposed <= ZERO or exit_depth <= ZERO or maximum_exit_loss < ZERO:
        return ZERO
    contracts = min(
        proposed.quantize(ONE, rounding=ROUND_DOWN),
        exit_depth.quantize(ONE, rounding=ROUND_DOWN),
    )
    unit_loss = max(
        ZERO,
        (entry_price + entry_fee) - (exit_price - exit_fee),
    )
    if unit_loss > ZERO:
        risk_cap = (maximum_exit_loss / unit_loss).quantize(ONE, rounding=ROUND_DOWN)
        contracts = min(contracts, risk_cap)
    return contracts if contracts >= ONE else ZERO


@dataclass
class ScalpStats:
    """Counters for one session, so effectiveness is answerable afterwards."""

    quotes_placed: int = 0
    quotes_filled: int = 0
    quotes_cancelled: int = 0
    quotes_expired: int = 0
    exits: int = 0
    settlements: int = 0
    taker_fills: int = 0
    quotes_requoted: int = 0
    gate_blocks: dict[str, int] | None = None

    def block(self, gate: str) -> None:
        if self.gate_blocks is None:
            self.gate_blocks = {}
        self.gate_blocks[gate] = self.gate_blocks.get(gate, 0) + 1

    @property
    def fill_rate(self) -> Decimal:
        if self.quotes_placed == 0:
            return ZERO
        return Decimal(self.quotes_filled) / Decimal(self.quotes_placed)


class ScalpEngine:
    """The scalping strategy's whole runtime."""

    def __init__(
        self,
        *,
        settings: Any,
        client: Any,
        ledger: Any,
        decisions: Any,
        positions: PositionBook,
        mode: str,
        live: bool,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.settings = settings
        self.client = client
        self.ledger = ledger
        self.decisions = decisions
        self.positions = positions
        self.mode = mode
        self.live = live
        self._now = now
        self.quotes = QuoteBook()
        self.stats = ScalpStats()

    # ------------------------------------------------------------ quoting --

    def rebind(self, *, client: Any, mode: str, live: bool) -> None:
        """Adopt a new client and book after a paper/live switch.

        ScalpEngine captures the client, mode and live flag at construction. The
        switch in ``bot.py`` closes the old client and builds a new one, which
        left this object holding a **closed session** - every order then raised
        ``RuntimeError: Session is closed``.

        The stale ``live`` flag was the more dangerous half and the crash hid
        it: after a switch to paper this object still believed it was live, so
        it would have taken the real order path and written live ledger rows
        while the banner said PAPER. Rebinding all three together is the point;
        ``tests/test_mode_switch.py`` fails if any collaborator is left behind.
        """
        self.client = client
        self.mode = mode
        self.live = live

    def assess(
        self,
        market: KalshiMarket,
        prediction: Any,
        *,
        seconds_to_close: Decimal,
        available_cash: Decimal,
        book: Any = None,
    ) -> ScalpQuote | None:
        """Whether this market is quotable right now, and at what price.

        Pure apart from the counters: every refusal is attributed to a named
        gate so the funnel afterwards says *why* nothing traded, which on a
        strategy that expects to be refused most of the time is the only useful
        diagnostic.
        """
        probability = prediction.probability_yes
        window = scalp_gate.check_quote_window(
            seconds_to_close, Decimal(self.settings.scalp_min_seconds_to_close)
        )
        if not window.allowed:
            self.stats.block("quote_window")
            return None

        # The pattern gate runs before the book is even looked at. A market with
        # no identifiable structure is not worth quoting at any price, so there
        # is nothing to learn from pricing it first.
        pattern = scalp_gate.check_pattern(
            prediction.regime,
            prediction.regime_confidence,
            # distance_z: check_pattern's |z| floor is documented as the
            # distance from spot to the strike, and must not be satisfiable by
            # the drift forecast. See Prediction.distance_z.
            getattr(prediction, "distance_z", prediction.z_score),
            blocked_regimes=self.settings.scalp_blocked_regimes,
            minimum_confidence=self.settings.scalp_min_regime_confidence,
            minimum_abs_z=self.settings.scalp_min_abs_z,
        )
        if not pattern.allowed:
            self.stats.block("pattern")
            return None

        spread = scalp_gate.check_spread(
            market.yes_bid, market.yes_ask, self.settings.scalp_max_spread
        )
        if not spread.allowed:
            self.stats.block("spread")
            return None

        # A position needs an entry side and an exit side. Checking the larger
        # touch allowed a deep bid to hide an empty ask (and vice versa), even
        # though the chosen outcome necessarily uses one to enter and the other
        # to exit. Refuse unless both sides meet the minimum.
        depth = scalp_gate.check_book_depth(
            min(market.yes_bid_size, market.yes_ask_size),
            self.settings.scalp_min_book_depth,
        )
        if not depth.allowed:
            self.stats.block("book_depth")
            return None

        concurrency = scalp_gate.check_concurrency(
            self.positions.open_count(STRATEGY), self.settings.scalp_max_concurrent
        )
        if not concurrency.allowed:
            self.stats.block("concurrency")
            return None

        # Order-book microstructure. Absent (a book that could not be read) is
        # not the same as clean: the ordinary liquidity gates above already
        # covered the market, so a missing analysis simply adds nothing.
        if book is not None and self.settings.scalp_orderbook_enabled:
            toxic = scalp_gate.check_toxicity(
                book.toxicity, self.settings.scalp_max_toxicity
            )
            if not toxic.allowed:
                self.stats.block("toxicity")
                return None

        # The fee is priced per contract at the quote price, because Kalshi's
        # taker fee is quadratic in price and a flat reserve would be wrong at
        # both ends of the band.
        fee = self._fee_per_contract(market.yes_bid + CENT, market.fee_multiplier)
        # Taker is the primary entry path. Five paper sessions produced zero
        # maker fills - the honest fill model only credits one when the book
        # trades *through* the resting price - so the maker premium could not be
        # measured, and Becker puts the maker-taker gap in the Finance category
        # (where 15-minute crypto sits) at 0.17pp, close to absent. Crossing is
        # priced at the touch and gated harder, rather than quoted and hoped for.
        builder = (
            build_taker_entry
            if self.settings.scalp_order_style == "taker"
            else build_quote
        )
        floor = self.settings.scalp_min_ev_per_dollar
        if self.settings.scalp_order_style == "taker":
            # Crossing pays the spread, so it must clear the higher bar that
            # separated session 5's winners from its marginal trades.
            floor = floor * self.settings.scalp_taker_edge_multiple
        quote = builder(
            probability=probability,
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            band_low=self.settings.scalp_band_low,
            band_high=self.settings.scalp_band_high,
            min_ev_per_dollar=floor,
            fee=fee,
        )
        if (
            quote is not None
            and book is not None
            and self.settings.scalp_orderbook_enabled
        ):
            # Trading against resting crowd pressure is rewarded; joining it is
            # penalised. Bartlett & O'Hara: traders buy YES 61% of the time and
            # YES settles 32% of the time, so where the crowd leans is where it
            # overpays, not where the value is.
            bonus = contrarian_bonus(
                book, quote.outcome, self.settings.scalp_crowd_weight
            )
            if quote.ev_per_dollar + bonus < floor:
                self.stats.block("crowd_pressure")
                return None
        if quote is None:
            # build_quote folds the band and the EV floor together, so tell them
            # apart here rather than reporting one cause for two failures. Both
            # sides are checked, because a market can be out of band on YES and
            # in band on NO.
            yes_side = market.yes_bid + CENT
            no_side = ONE - (market.yes_ask - CENT)
            in_band_either = any(
                scalp_gate.check_price_band(
                    price,
                    self.settings.scalp_band_low,
                    self.settings.scalp_band_high,
                ).allowed
                for price in (yes_side, no_side)
            )
            self.stats.block("min_ev" if in_band_either else "price_band")
            return None

        if available_cash <= ZERO:
            self.stats.block("no_cash")
            return None
        return quote

    def _size_probability_allows(
        self, quote: ScalpQuote, cost: Decimal, portfolio_value: Decimal | None
    ) -> bool:
        """Whether probability clears the optional exposure-based admission.

        An unknown portfolio does not silently pass. It is the denominator of
        the exposure, and treating a missing one as "risks nothing" would turn
        the gate off exactly when the account cannot be read - so it is scored
        as fully exposed and must clear the highest floor.
        """
        uplift = getattr(self.settings, "size_probability_uplift", ZERO)
        if uplift <= ZERO:
            return True
        if portfolio_value is None or portfolio_value <= ZERO:
            exposure = ONE
        else:
            exposure = min(ONE, cost / portfolio_value)
        try:
            probability = Decimal(str(quote.context.get("probability", "0")))
        except (InvalidOperation, TypeError, ValueError):
            probability = ZERO
        required = required_probability(
            base=self.settings.scalp_band_low,
            intended_exposure=exposure,
            full_exposure=self.settings.size_probability_full_exposure,
            uplift=uplift,
        )
        allowed = check_size_scaled_probability(probability, exposure, required).allowed
        if not allowed:
            logger.debug(
                "SCALP size gate {}: P={:.1%} < {:.1%} required at {:.1%} "
                "of the account",
                quote.ticker,
                float(probability),
                float(required),
                float(exposure),
            )
        return allowed

    def _refresh_queue(
        self, quote: Any, market: KalshiMarket, live_queue: dict
    ) -> None:
        """Update how many contracts sit ahead of this order, and record it.

        Live: Kalshi's own price-time queue position, which counts matches.
        Paper: the size still resting at our price, which is the same quantity
        a queue position measures - but it also falls when someone ahead of us
        CANCELS, so the paper number decays faster than a real queue does. It
        is recorded rather than trusted for exactly that reason; comparing the
        two on live quotes is what would justify acting on the paper one.
        """
        previous = quote.queue_ahead
        if self.live and quote.order_id and quote.order_id in live_queue:
            quote.queue_ahead = live_queue[quote.order_id]
        else:
            resting = (
                market.yes_ask_size if quote.book_side == "ask" else market.yes_bid_size
            )
            quote.queue_ahead = max(ZERO, resting or ZERO)
        if quote.queue_ahead_at_placement is None:
            quote.queue_ahead_at_placement = quote.queue_ahead
        if previous != quote.queue_ahead:
            self._record(
                "scalp_queue_position",
                ticker=quote.ticker,
                asset=quote.asset,
                order_id=quote.order_id,
                price=quote.price,
                book_side=quote.book_side,
                source="venue"
                if (self.live and quote.order_id in live_queue)
                else "book_estimate",
                queue_ahead=quote.queue_ahead,
                queue_ahead_at_placement=quote.queue_ahead_at_placement,
                contracts=quote.contracts,
                age_seconds=quote.age_seconds(self._now()),
            )

    def _confidence_share(self, quote: ScalpQuote) -> Decimal:
        """Confidence no longer changes order size.

        Kept as a tiny compatibility seam for tests and downstream extensions:
        a signal may pass or fail a confidence gate, but every admitted trade
        receives the same configured dollar budget.
        """
        del quote
        return ONE

    async def place_quote(
        self,
        market: KalshiMarket,
        quote: ScalpQuote,
        *,
        available_cash: Decimal,
        book: Any = None,
        portfolio_value: Decimal | None = None,
    ) -> RestingQuote | None:
        """Reserve capital, then rest the order.

        The reservation is taken *before* the venue call and released if the
        order does not survive, because the alternative - reserve after the
        answer comes back - lets two concurrent placements both pass the
        capacity check.
        """
        if self.quotes.has(STRATEGY, market.ticker):
            return None
        allowed, reason = self.ledger.can_enter_ticker(
            mode=self.mode,
            environment=self.settings.environment,
            ticker=market.ticker,
            strategy=STRATEGY,
            outcome=quote.outcome,
            maximum_entries=self.settings.max_entries_per_market,
        )
        if not allowed:
            self.stats.block("already_traded")
            logger.debug("SCALP {} re-entry refused: {}", market.ticker, reason)
            return None

        exchange_index = int(getattr(market, "exchange_index", 0) or 0)
        if self.live:
            try:
                indexed_cash = await self.client.get_available_balance(exchange_index)
            except Exception as exc:  # unverifiable cash cannot fund an order
                self.stats.block("balance_unavailable")
                logger.warning(
                    "SCALP {} skipped: exchange-index {} balance unavailable ({})",
                    market.ticker,
                    exchange_index,
                    type(exc).__name__,
                )
                return None
            available_cash = min(available_cash, indexed_cash)
            if available_cash <= ZERO:
                self.stats.block("no_cash")
                return None

        fee = self._fee_per_contract(quote.price, market.fee_multiplier)
        ceiling = self.positions.ceiling_for(STRATEGY) or available_cash
        budget = min(
            available_cash,
            ceiling - self.positions.committed(STRATEGY),
            getattr(
                self.settings,
                "trade_budget_usd",
                getattr(self.settings, "live_capital_limit_usd", Decimal("10")),
            ),
        )
        # A taker consumes the opposite touch: buying YES lifts the YES ask;
        # buying NO sells YES into the YES bid. A maker joins its own book side
        # and therefore uses the queue already resting on that same side.
        depth = entry_touch_depth(market, quote, self.settings.scalp_order_style)
        contracts = position_size(
            budget=budget,
            price=quote.price,
            fee_per_contract=fee,
            depth=depth,
        )
        if contracts <= ZERO:
            self.stats.block("size_zero")
            return None

        # Entry depth alone is not liquidity. A position that can be bought but
        # not sold is a settlement bet wearing a scalp label. Price the exact
        # immediate flatten (including a second taker fee) and require enough
        # opposing depth for every proposed contract before reserving capital.
        exit_bid, exit_depth = immediate_exit_book(market, quote.outcome)
        exit_fee = self._fee_per_contract(exit_bid, market.fee_multiplier)
        max_exit_loss = getattr(
            self.settings, "scalp_max_modeled_exit_loss_usd", Decimal("1.18")
        )
        proposed_contracts = contracts
        contracts = risk_bounded_contracts(
            proposed=contracts,
            entry_price=quote.price,
            entry_fee=fee,
            exit_price=exit_bid,
            exit_fee=exit_fee,
            exit_depth=exit_depth,
            maximum_exit_loss=max_exit_loss,
        )
        if contracts <= ZERO:
            self.stats.block("exitability")
            self._record(
                "scalp_refusal",
                ticker=market.ticker,
                asset=market.asset,
                gate="exitability",
                outcome=quote.outcome,
                contracts=proposed_contracts,
                exit_bid=exit_bid,
                exit_depth=exit_depth,
                maximum_exit_loss=max_exit_loss,
            )
            return None
        cost = calculate_paper_fill(
            contracts, quote.price, market.fee_multiplier
        ).total_debit
        modeled_exit_proceeds = contracts * (exit_bid - exit_fee)
        modeled_exit_loss = max(ZERO, cost - modeled_exit_proceeds)
        # Cent rounding can place a boundary-sized order a fraction above the
        # dollar cap. Reduce one contract rather than weakening the invariant.
        while contracts > ZERO and (cost > budget or modeled_exit_loss > max_exit_loss):
            contracts -= ONE
            cost = calculate_paper_fill(
                contracts, quote.price, market.fee_multiplier
            ).total_debit
            modeled_exit_proceeds = contracts * (exit_bid - exit_fee)
            modeled_exit_loss = max(ZERO, cost - modeled_exit_proceeds)
        if contracts <= ZERO:
            self.stats.block("exitability")
            return None
        if contracts < proposed_contracts:
            self._record(
                "scalp_size_clipped",
                ticker=market.ticker,
                asset=market.asset,
                outcome=quote.outcome,
                proposed_contracts=proposed_contracts,
                contracts=contracts,
                modeled_exit_loss=modeled_exit_loss,
                maximum_exit_loss=max_exit_loss,
                exit_depth=exit_depth,
            )

        # Optional exposure-scaled admission runs after top-of-book depth clips
        # the fixed stake, because only then is actual exposure known.
        if not self._size_probability_allows(quote, cost, portfolio_value):
            self.stats.block("size_scaled_probability")
            return None

        reservation = await self.positions.reserve(
            strategy=STRATEGY, ticker=market.ticker, amount=cost
        )
        if reservation is None:
            self.stats.block("reservation_refused")
            return None

        client_order_id = f"scalp-{uuid.uuid4().hex[:16]}"
        order_id = f"paper-{client_order_id}"

        # Taker: cross now and be done. No resting order, no TTL, no requote
        # cycle - the position exists (or does not) on this pass rather than two
        # passes later, which is the latency the maker path was charging for a
        # fill that never came.
        if self.settings.scalp_order_style == "taker":
            actual_fill: Decimal | None = None
            if self.live:
                plan = OrderPlan(
                    ticker=market.ticker,
                    outcome=quote.outcome,
                    sdk_book_side=quote.book_side,
                    sdk_yes_leg_price=quote.yes_leg_price,
                    outcome_limit_price=quote.price,
                    contracts=contracts,
                    maximum_cost=contracts * quote.price,
                    fee_reserve=cost - contracts * quote.price,
                    maximum_total_debit=cost,
                    client_order_id=client_order_id,
                    strategy=STRATEGY,
                    exchange_index=exchange_index,
                )
                result = await self.client.place_order(plan)
                if not result.filled:
                    await self.positions.release(reservation)
                    self.stats.block(f"venue_{result.error_code or 'no_fill'}")
                    return None
                contracts = result.fill_count
                order_id = result.order_id
                if result.average_fill_price is not None:
                    actual_fill = (
                        result.average_fill_price
                        if quote.outcome == "yes"
                        else ONE - result.average_fill_price
                    )
            crossed = RestingQuote(
                ticker=market.ticker,
                asset=market.asset,
                outcome=quote.outcome,
                price=quote.price,
                contracts=contracts,
                placed_at=self._now(),
                order_id=order_id,
                client_order_id=client_order_id,
                bid_at_placement=market.yes_bid,
                reservation_key=reservation.key,
                yes_leg_price=quote.yes_leg_price,
                book_side=quote.book_side,
                exchange_index=exchange_index,
                context=self._quote_context(market, quote, fee, book),
            )
            crossed.context["order_style"] = "taker_cross"
            self.quotes.add(crossed)
            self.stats.quotes_placed += 1
            self.stats.taker_fills += 1
            self._record(
                "scalp_quote",
                ticker=market.ticker,
                asset=market.asset,
                outcome=quote.outcome,
                price=quote.price,
                yes_leg_price=quote.yes_leg_price,
                contracts=contracts,
                probability=quote.probability,
                ev_per_dollar=quote.ev_per_dollar,
                spread=quote.spread,
                joins_bid=False,
                style="taker_cross",
            )
            await self._book_fill(crossed, market, contracts, actual_fill)
            return crossed

        if self.live:
            plan = OrderPlan(
                ticker=market.ticker,
                outcome=quote.outcome,
                sdk_book_side=quote.book_side,
                sdk_yes_leg_price=quote.yes_leg_price,
                outcome_limit_price=quote.price,
                contracts=contracts,
                maximum_cost=contracts * quote.price,
                fee_reserve=cost - contracts * quote.price,
                maximum_total_debit=cost,
                client_order_id=client_order_id,
                strategy=STRATEGY,
                intent="maker",
                exchange_index=exchange_index,
            )
            result = await self.client.place_resting_order(plan)
            if result.error_code and not result.filled:
                await self.positions.release(reservation)
                self.stats.block(f"venue_{result.error_code}")
                return None
            order_id = result.order_id

        resting = RestingQuote(
            ticker=market.ticker,
            asset=market.asset,
            outcome=quote.outcome,
            price=quote.price,
            contracts=contracts,
            placed_at=self._now(),
            order_id=order_id,
            client_order_id=client_order_id,
            bid_at_placement=market.yes_bid,
            reservation_key=reservation.key,
            yes_leg_price=quote.yes_leg_price,
            book_side=quote.book_side,
            exchange_index=exchange_index,
            context={
                "outcome": quote.outcome,
                "yes_leg_price": str(quote.yes_leg_price),
                "probability": str(quote.probability),
                "ev_per_dollar": str(quote.ev_per_dollar),
                "spread_at_quote": str(quote.spread),
                "joins_bid": quote.joins_bid,
                "mid_at_quote": str((market.yes_bid + market.yes_ask) / Decimal("2")),
                "fee_per_contract": str(fee),
                "band": f"{self.settings.scalp_band_low}-{self.settings.scalp_band_high}",
                "market_close_time": market.close_time.isoformat(),
                "target_price": str(market.target_price),
                "fee_multiplier": str(market.fee_multiplier),
                "event_ticker": market.event_ticker,
                "exchange_index": exchange_index,
            },
        )
        self.quotes.add(resting)
        self.stats.quotes_placed += 1
        self._record(
            "scalp_quote",
            ticker=market.ticker,
            asset=market.asset,
            # The side is load-bearing for the post-session band audit, which
            # scores the quoted outcome against settlement. Without it every NO
            # quote is scored as a YES and the audit inverts.
            outcome=quote.outcome,
            price=quote.price,
            yes_leg_price=quote.yes_leg_price,
            contracts=contracts,
            probability=quote.probability,
            ev_per_dollar=quote.ev_per_dollar,
            spread=quote.spread,
            joins_bid=quote.joins_bid,
        )
        return resting

    def _quote_context(
        self,
        market: KalshiMarket,
        quote: ScalpQuote,
        fee: Decimal,
        book: Any = None,
    ) -> dict:
        """Everything needed to re-fit the entry rules from our own fills later.

        The band, the EV floor and the crowd weight were all seeded from
        published research rather than from this bot's history, so each entry
        records what it would take to replace those priors with measurements.
        """
        return {
            "outcome": quote.outcome,
            "yes_leg_price": str(quote.yes_leg_price),
            "probability": str(quote.probability),
            "ev_per_dollar": str(quote.ev_per_dollar),
            "spread_at_quote": str(quote.spread),
            "joins_bid": quote.joins_bid,
            "mid_at_quote": str((market.yes_bid + market.yes_ask) / Decimal("2")),
            "fee_per_contract": str(fee),
            "band": f"{self.settings.scalp_band_low}-{self.settings.scalp_band_high}",
            "market_close_time": market.close_time.isoformat(),
            "target_price": str(market.target_price),
            "fee_multiplier": str(market.fee_multiplier),
            "event_ticker": market.event_ticker,
            "exchange_index": int(getattr(market, "exchange_index", 0) or 0),
            # Book state at entry. Logged whether or not it influenced the
            # decision, because the resting-depth adjustment ships inert and its
            # SIGN has to be fitted from settled outcomes before it can be
            # switched on. Without these fields that fit is impossible after the
            # fact - the book is gone the moment the cycle ends.
            **(
                {
                    "book_imbalance": str(book.imbalance),
                    "book_toxicity": str(book.toxicity),
                    "book_microprice": str(book.microprice),
                    "book_mid": str(book.mid),
                    "book_yes_depth": str(book.yes_depth),
                    "book_no_depth": str(book.no_depth),
                    "book_levels": f"{book.levels_yes}/{book.levels_no}",
                }
                if book is not None
                else {}
            ),
        }

    async def service_quotes(
        self,
        books: dict[str, KalshiMarket],
        probabilities: dict[str, Decimal],
    ) -> list[Any]:
        """Advance every resting quote by one cycle.

        Returns the trades that were opened, so the caller can decide what to
        report. A quote whose market vanished from discovery is cancelled rather
        than carried: an unquotable market is one we cannot exit either.
        """
        opened: list[Any] = []
        # One call for every resting order, before the loop: queue position is
        # a portfolio-wide read and asking per quote would multiply it by the
        # number of assets quoted.
        live_queue = (
            await self.client.get_queue_positions(sorted(books))
            if self.live and self.quotes.all()
            else {}
        )
        for quote in self.quotes.all():
            market = books.get(quote.ticker)
            if market is None:
                await self._retire(quote, "cancel", "market no longer discoverable")
                continue

            probability = probabilities.get(quote.ticker)
            fee = self._fee_per_contract(quote.price, market.fee_multiplier)
            # The probability of the outcome we actually hold, not of YES.
            outcome_probability = (
                None
                if probability is None
                else (probability if quote.outcome == "yes" else ONE - probability)
            )
            edge_survives = (
                outcome_probability is not None
                and ev_per_dollar(outcome_probability, quote.price, fee)
                >= self.settings.scalp_min_ev_per_dollar
            )
            self._refresh_queue(quote, market, live_queue)
            outcome = evaluate_quote(
                quote,
                now=self._now(),
                yes_bid=market.yes_bid,
                yes_ask=market.yes_ask,
                edge_survives=edge_survives,
                ttl_seconds=self.settings.scalp_quote_ttl_seconds,
                requote_ticks=self.settings.scalp_requote_ticks,
                paper=not self.live,
                queue_aware=getattr(self.settings, "scalp_queue_aware_fills", False),
            )
            if outcome.action == "hold":
                continue
            if outcome.action == "fill":
                trade = await self._book_fill(quote, market, outcome.fill_contracts)
                if trade is not None:
                    opened.append(trade)
                continue

            # Maker-heavy, not maker-only. A quote that aged out without being
            # filled is not evidence the trade was wrong - only that nobody came
            # to us. When the setup is still clearly good we take it at the
            # touch instead of walking away; when it is merely acceptable we
            # walk, because paying the spread is what turns a thin edge
            # negative. Requoting-and-crossing every marginal setup is how a
            # maker strategy quietly becomes the taker strategy it was meant to
            # improve on.
            # Reachable on BOTH withdrawal reasons that leave the trade wanted.
            # Gating it on "expire" alone made it dead code: across two live
            # sessions 15 quotes were withdrawn for drift and only 1 aged out,
            # because on a 1c-spread market that ticks constantly a two-tick
            # move arrives long before a 20-second timer. A quote pulled because
            # the book walked away is precisely the case the fallback exists
            # for - we still want the trade, nobody came to us at our price.
            # A quote pulled because the edge died is not, and stays excluded.
            if (
                outcome.action in {"expire", "requote"}
                and self.settings.scalp_taker_fallback
                and outcome_probability is not None
            ):
                threshold = (
                    self.settings.scalp_min_ev_per_dollar
                    * self.settings.scalp_taker_edge_multiple
                )
                cross_price = (
                    market.yes_ask if quote.outcome == "yes" else ONE - market.yes_bid
                )
                cross_fee = self._fee_per_contract(cross_price, market.fee_multiplier)
                cross_edge = ev_per_dollar(outcome_probability, cross_price, cross_fee)
                in_band = (
                    self.settings.scalp_band_low
                    <= cross_price
                    <= self.settings.scalp_band_high
                )
                if cross_edge >= threshold and in_band and cross_price > ZERO:
                    trade = await self._cross(quote, market, cross_price)
                    if trade is not None:
                        opened.append(trade)
                        continue

            await self._retire(quote, outcome.action, outcome.reason)
        return opened

    async def _cross(
        self,
        quote: RestingQuote,
        market: KalshiMarket,
        price: Decimal,
    ) -> Any | None:
        """Take the touch on a setup good enough to pay the spread for."""
        if self.live:
            fill = calculate_paper_fill(quote.contracts, price, market.fee_multiplier)
            plan = OrderPlan(
                ticker=market.ticker,
                outcome=quote.outcome,
                sdk_book_side="bid" if quote.outcome == "yes" else "ask",
                sdk_yes_leg_price=(price if quote.outcome == "yes" else ONE - price),
                outcome_limit_price=price,
                contracts=quote.contracts,
                maximum_cost=fill.entry_cost,
                fee_reserve=fill.fees_paid,
                maximum_total_debit=fill.total_debit,
                client_order_id=f"scalpt-{uuid.uuid4().hex[:16]}",
                strategy=STRATEGY,
                exchange_index=int(getattr(market, "exchange_index", 0) or 0),
            )
            if quote.order_id:
                await self.client.cancel_order(
                    quote.order_id,
                    exchange_index=int(getattr(quote, "exchange_index", 0) or 0),
                )
            result = await self.client.place_order(plan)
            if not result.filled:
                await self._retire(quote, "cancel", "taker fallback did not fill")
                return None
            filled = result.fill_count
        else:
            if quote.order_id:
                pass
            filled = quote.contracts

        self.stats.taker_fills += 1
        crossed = RestingQuote(
            ticker=quote.ticker,
            asset=quote.asset,
            outcome=quote.outcome,
            price=price,
            contracts=filled,
            placed_at=quote.placed_at,
            order_id=quote.order_id or f"paper-taker-{quote.client_order_id}",
            client_order_id=quote.client_order_id,
            bid_at_placement=quote.bid_at_placement,
            reservation_key=quote.reservation_key,
            yes_leg_price=(price if quote.outcome == "yes" else ONE - price),
            book_side=quote.book_side,
            exchange_index=int(getattr(market, "exchange_index", 0) or 0),
            context={**quote.context, "order_style": "taker_cross"},
        )
        self.quotes.remove(quote)
        self.quotes.add(crossed)
        return await self._book_fill(crossed, market, filled)

    # ------------------------------------------------------------ positions --

    # -------------------------------------------------------------- helpers --

    async def _book_fill(
        self,
        quote: RestingQuote,
        market: KalshiMarket,
        contracts: Decimal,
        fill_price: Decimal | None = None,
    ) -> Any | None:
        """Turn a filled quote into a ledger row.

        ``fill_price`` is the venue's *actual* average fill, passed in live mode.
        Without it the ledger books a live trade at the price we asked for and a
        simulated fee, which is how the first live scalp came to record -$1.73
        against a real account movement of -$1.56. A paper-model number in a
        live row is not a small inaccuracy: it is the P&L every later analysis
        is computed from.

        The fee model stays the simulated one even live, because Kalshi reports
        fees on the fill only after settlement. It errs high, so a live row
        understates rather than flatters - the right direction - and the
        ledger-vs-venue gap is now bounded by the fee estimate alone.
        """
        fill = calculate_paper_fill(
            contracts=contracts,
            price=fill_price if fill_price is not None else quote.price,
            fee_multiplier=market.fee_multiplier,
        )
        trade = self.ledger.record_open_trade(
            mode=self.mode,
            environment=self.settings.environment,
            strategy=STRATEGY,
            asset=market.asset,
            ticker=market.ticker,
            event_ticker=market.event_ticker,
            order_id=quote.order_id,
            client_order_id=quote.client_order_id,
            outcome=quote.outcome,
            placed_at=self._now(),
            market_open_time=market.open_time,
            market_close_time=market.close_time,
            status_check_time=market.close_time,
            contracts=contracts,
            entry_price=fill_price if fill_price is not None else quote.price,
            entry_cost=fill.entry_cost,
            fees_paid=fill.fees_paid,
            total_debit=fill.total_debit,
            target_price=market.target_price,
            model_probability=Decimal(quote.context.get("probability", "0")),
            model_edge=Decimal(quote.context.get("ev_per_dollar", "0")),
            metadata={
                **quote.context,
                "strategy": STRATEGY,
                "order_style": quote.context.get("order_style", "maker_post_only"),
                "fill_latency_seconds": quote.age_seconds(self._now()),
                "bid_at_placement": str(quote.bid_at_placement),
                "bid_at_fill": str(market.yes_bid),
                "ask_at_fill": str(market.yes_ask),
            },
        )
        quote.state = QuoteState.FILLED
        self.quotes.remove(quote)
        self.stats.quotes_filled += 1
        await self.positions.confirm(
            Reservation(
                key=quote.reservation_key,
                strategy=STRATEGY,
                ticker=quote.ticker,
                amount=fill.total_debit,
            ),
            trade_id=trade.trade_id,
            actual_amount=fill.total_debit,
        )
        logger.warning(
            "SCALP FILL {} {} {} contracts at {:.2f} (debit ${})",
            market.asset,
            market.ticker,
            contracts,
            float(quote.price),
            fill.total_debit,
        )
        self._record(
            "scalp_fill",
            ticker=market.ticker,
            asset=market.asset,
            price=quote.price,
            contracts=contracts,
            total_debit=fill.total_debit,
            trade_id=trade.trade_id,
        )
        return trade

    async def _retire(self, quote: RestingQuote, action: str, reason: str) -> None:
        if self.live and quote.order_id:
            await self.client.cancel_order(
                quote.order_id,
                exchange_index=int(getattr(quote, "exchange_index", 0) or 0),
            )
        quote.state = QuoteState.EXPIRED if action == "expire" else QuoteState.CANCELLED
        self.quotes.remove(quote)
        await self.positions.release(
            Reservation(
                key=quote.reservation_key,
                strategy=STRATEGY,
                ticker=quote.ticker,
                amount=ZERO,
            )
        )
        if action == "expire":
            self.stats.quotes_expired += 1
        elif action == "requote":
            # Counted apart from a cancel: one says the book moved, the other
            # says the trade went bad, and folding them together is what hid
            # the taker fallback being unreachable.
            self.stats.quotes_requoted += 1
        else:
            self.stats.quotes_cancelled += 1
        self._record(
            "scalp_quote_retired",
            ticker=quote.ticker,
            asset=quote.asset,
            action=action,
            reason=reason,
            price=quote.price,
            age_seconds=quote.age_seconds(self._now()),
        )

    def _fee_per_contract(
        self, price: Decimal, fee_multiplier: Decimal = ONE
    ) -> Decimal:
        """Kalshi's quadratic taker fee for a single contract."""
        clamped = max(Decimal("0.01"), min(Decimal("0.99"), price))
        return (Decimal("0.07") * fee_multiplier * clamped * (ONE - clamped)).quantize(
            Decimal("0.0001")
        )

    def _record(self, event: str, **fields: Any) -> None:
        """Write a scalp event to the decision log, never fatally."""
        payload = {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in fields.items()
        }
        self.decisions.record_event(
            event, mode=self.mode, environment=self.settings.environment, **payload
        )

    def record_funnel(self, occasion: str) -> None:
        """Persist why the strategy declined, not only what it did.

        The gate counters live in memory, so before this they died with the
        process and the report could say how many quotes were placed but never
        why the other markets were passed over. On a strategy that refuses most
        of what it sees, that refusal breakdown IS the result - a session with
        no fills is uninformative unless it says whether the pattern gate, the
        band, or an empty book was doing the work.

        Written at session end and shutdown rather than per cycle, so it costs
        two records per run and does not undo the log-volume work.
        """
        self._record(
            "scalp_funnel",
            occasion=occasion,
            quotes_placed=self.stats.quotes_placed,
            quotes_filled=self.stats.quotes_filled,
            quotes_cancelled=self.stats.quotes_cancelled,
            quotes_requoted=self.stats.quotes_requoted,
            quotes_expired=self.stats.quotes_expired,
            taker_fills=self.stats.taker_fills,
            exits=self.stats.exits,
            settlements=self.stats.settlements,
            gate_blocks=dict(self.stats.gate_blocks or {}),
        )

    async def drain(self) -> None:
        """Cancel everything resting, for shutdown or a mode switch."""
        self.record_funnel("drain")
        for quote in self.quotes.drain():
            if self.live and quote.order_id:
                try:
                    await self.client.cancel_order(
                        quote.order_id,
                        exchange_index=int(getattr(quote, "exchange_index", 0) or 0),
                    )
                except Exception as exc:  # continue cancelling the other quotes
                    logger.error(
                        "Could not cancel resting order {} on shutdown: {}",
                        quote.order_id,
                        exc,
                    )
