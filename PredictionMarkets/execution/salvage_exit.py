"""Runtime for the salvage exit.

Owns exactly one decision and the plumbing to act on it: read the book for a
position that is about to expire deeply out of the money, ask the pure rule in
``strategies/salvage.py`` whether the book is overpaying for it, and if so sell
the whole thing ``reduce_only``.

It lives outside ``ScalpEngine`` on purpose. The scalper has no exit path any
more and should not grow one back; and this applies to *every* strategy's
positions, including the predictor's, so scoping it to the scalper would make it
unreachable in the mode the bot actually ran live in.

Two properties are structural rather than checked:

* **It can never open a position.** The only order it constructs is a sell of
  the outcome already held, on the opposite side of the book from the one that
  opened it, with ``reduce_only=True`` on the wire.
* **It evaluates each position at most once.** The window is a moment before
  close, not a monitoring mode. A rule that re-asks every poll is a rule that
  eventually says yes to noise.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from loguru import logger

from core.strategy_brain.strategies.oversight import OversightPlan, plan_oversight
from core.strategy_brain.strategies.salvage import (
    SalvageDecision,
    decide_salvage,
    outcome_book,
)
from execution.kalshi_client import OrderPlan
from execution.trade_ledger import _ceil_to


ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
CENTICENT = Decimal("0.0001")

_event = logger.bind(event=True)


class SalvageExit:
    """The salvage rule's whole runtime."""

    def __init__(
        self,
        *,
        settings: Any,
        client: Any,
        ledger: Any,
        decisions: Any,
        positions: Any,
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
        # Trade ids whose answer is final - sold, or out of checks. Membership,
        # not a count: a refusal is as final as a sale once the budget is spent.
        self._evaluated: set[str] = set()
        # How many times each position has been asked, and when it was last
        # asked. Both are needed: exposure buys a position more checks, and the
        # spacing is what stops those extra checks becoming a poll loop.
        self._checks: dict[str, int] = {}
        self._last_check: dict[str, datetime] = {}
        # The portfolio the exposure fraction is measured against. Supplied by
        # the caller each pass because it compounds; zero means "not known",
        # which oversight deliberately reads as fully exposed.
        self._portfolio_value: Decimal = ZERO
        self.evaluations = 0
        self.exits = 0
        self.blocks: dict[str, int] = {}

    # ---------------------------------------------------------------- window --

    def rebind(self, *, client: Any, mode: str, live: bool) -> None:
        """Adopt a new client and book after a paper/live switch.

        SalvageExit captures the client, mode and live flag at construction. The
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

    def set_portfolio_value(self, value: Decimal | None) -> None:
        """Tell the exposure calculation what the account is worth.

        ``run`` sets this itself, but the predictor-only path asks ``is_due``
        *before* it ever calls ``run`` - it has to, because the answer decides
        whether to wake early. Without this that path would price every
        position against a zero portfolio and hand them all full attention.
        """
        self._portfolio_value = ZERO if value is None else Decimal(str(value))

    def plan_for(self, trade: Any) -> OversightPlan:
        """How closely this particular position has earned to be watched.

        With oversight off this returns the historical constants - one check,
        the flat window - so the behaviour is unchanged rather than merely
        similar. With it on, the only inputs are the position's own value and
        the portfolio it is risked against; nothing here consults the model.
        """
        if not getattr(self.settings, "oversight_enabled", False):
            return plan_oversight(
                position_value=ZERO,
                portfolio_value=ONE,
                base_window_seconds=self.settings.salvage_window_seconds,
                max_window_seconds=self.settings.salvage_window_seconds,
                base_checks=1,
                max_checks=1,
                min_spacing_seconds=0.0,
                full_attention_exposure=ONE,
                base_max_bid=self.settings.salvage_max_bid,
                shadow_max_bid=self.settings.salvage_max_bid,
                # Applies with oversight off too: it is a statement about
                # whether an order can still land, not about supervision.
                base_actionable_seconds=getattr(
                    self.settings, "salvage_min_actionable_seconds", 0.0
                ),
            )
        return plan_oversight(
            position_value=self._position_value(trade),
            portfolio_value=self._portfolio_value,
            base_window_seconds=self.settings.salvage_window_seconds,
            max_window_seconds=self.settings.oversight_max_window_seconds,
            base_checks=1,
            max_checks=self.settings.oversight_max_checks,
            min_spacing_seconds=self.settings.oversight_min_spacing_seconds,
            full_attention_exposure=self.settings.oversight_full_exposure,
            base_max_bid=self.settings.salvage_max_bid,
            shadow_max_bid=self.settings.oversight_shadow_max_bid,
            base_actionable_seconds=self.settings.salvage_min_actionable_seconds,
            extra_actionable_seconds=(self.settings.oversight_extra_actionable_seconds),
        )

    @staticmethod
    def _position_value(trade: Any) -> Decimal:
        """What this position put at risk, in dollars.

        The debit, not the notional: the debit is the number that can actually
        be lost, and it is the one already recorded against the account.
        """
        value = getattr(trade, "total_debit", None)
        if value is None:
            return ZERO
        return Decimal(str(value))

    def is_due(self, trade: Any, now: datetime | None = None) -> bool:
        """Whether this position is inside a window and has a check left.

        Both ends of the window matter. Before it there is still time for the
        market to come back, and the whole premise is that it is nearly over;
        after close there is nothing to sell.

        Exposure widens the window and buys additional checks, and the spacing
        is what keeps those checks supervision rather than a poll loop. The
        underlying sell rule is untouched by any of it.
        """
        if trade.trade_id in self._evaluated:
            return False
        moment = now or self._now()
        plan = self.plan_for(trade)
        seconds = (trade.market_close_time - moment).total_seconds()
        if not 0 < seconds <= plan.window_seconds:
            return False
        # Time to cut. Under this floor the sale cannot be relied on to reach
        # the venue and complete its atomic FOK fill before close.
        if seconds < plan.min_actionable_seconds:
            self.blocks["too_late"] = self.blocks.get("too_late", 0) + 1
            return False
        if self._checks.get(trade.trade_id, 0) >= plan.checks:
            return False
        last = self._last_check.get(trade.trade_id)
        if last is not None:
            if (moment - last).total_seconds() < plan.min_spacing_seconds:
                return False
        return True

    def seconds_until_next_check(
        self, trade: Any, now: datetime | None = None
    ) -> float | None:
        """Seconds until this position may next be examined, or None if never.

        This exists because the predictor-only loop does not poll. It sleeps
        from here to the first status check in ONE call, so a check that fires
        and refuses used to end supervision for that position entirely: the
        second, third and fourth checks an exposed position had earned were
        scheduled into the middle of a sleep and never happened. The window
        widened, the budget was granted, and three quarters of it was
        unreachable - switched on, logged as configured, and silently inert.

        None means there is no next check worth waking for: out of budget, past
        the window, or so close to expiry that the sale could not complete.
        """
        if trade.trade_id in self._evaluated:
            return None
        moment = now or self._now()
        plan = self.plan_for(trade)
        seconds_to_close = (trade.market_close_time - moment).total_seconds()
        if seconds_to_close <= plan.min_actionable_seconds:
            return None
        if self._checks.get(trade.trade_id, 0) >= plan.checks:
            return None

        until_window = max(0.0, seconds_to_close - plan.window_seconds)
        until_spacing = 0.0
        last = self._last_check.get(trade.trade_id)
        if last is not None:
            until_spacing = max(
                0.0,
                plan.min_spacing_seconds - (moment - last).total_seconds(),
            )
        wait = max(until_window, until_spacing)
        # A wake that lands too late to act on is not worth taking.
        if seconds_to_close - wait <= plan.min_actionable_seconds:
            return None
        return wait

    def seconds_until_due(self, trade: Any, now: datetime | None = None) -> float:
        """How long until this position enters its window, or 0 if it is in it."""
        seconds = (trade.market_close_time - (now or self._now())).total_seconds()
        return max(0.0, seconds - self.plan_for(trade).window_seconds)

    # ------------------------------------------------------------------ pass --

    async def run(self, trades: Any, *, portfolio_value: Decimal | None = None) -> int:
        """Evaluate every due position. Returns how many were sold.

        ``portfolio_value`` is what the exposure fraction is measured against
        and is re-read by the caller every pass, because it compounds; a
        ceiling frozen at the opening balance stops describing the account it
        is supervising. Omitting it is only correct with oversight off - with
        it on, an unknown portfolio is read as fully exposed, which buys the
        position the *most* supervision rather than the least.

        A failure on one position must not strand the others, so each is
        contained: the alternative is one unreadable book aborting a pass that
        had another position in the same window.
        """
        self._portfolio_value = (
            ZERO if portfolio_value is None else Decimal(str(portfolio_value))
        )
        self._forget_closed(trades)
        sold = 0
        for trade in trades:
            if not self.is_due(trade):
                continue
            try:
                if await self.evaluate(trade):
                    sold += 1
            except Exception:
                logger.exception(
                    "Salvage evaluation failed for {}; the position is held",
                    trade.ticker,
                )
                # A position whose book could not be read is finished with, not
                # retried: the failure is the answer. Retrying it is how a
                # bounded rule becomes an unbounded one.
                self._evaluated.add(trade.trade_id)
        return sold

    def _forget_closed(self, trades: Any) -> None:
        """Drop per-trade state for positions that are no longer open.

        Three structures are keyed by ``trade_id`` and nothing else pruned
        them, so a process running 24/7 accumulated one entry per settled
        trade for as long as it ran. At this bot's rate that is small, which is
        exactly why it would never have been noticed - and a slow leak in the
        supervisor of every open position is not a thing to leave for later.

        Only ids still present in the caller's open set survive. The caller
        reads them from the ledger each pass, so a settled trade disappears on
        the following pass and its ``_evaluated`` membership goes with it -
        which is correct: that membership only ever meant "do not examine this
        open position again", and a settled position is not examined at all.
        """
        live_ids = {trade.trade_id for trade in trades}
        if not live_ids:
            self._evaluated.clear()
            self._checks.clear()
            self._last_check.clear()
            return
        self._evaluated &= live_ids
        for book in (self._checks, self._last_check):
            for stale in [key for key in book if key not in live_ids]:
                del book[stale]

    async def evaluate(self, trade: Any) -> bool:
        """Put one position to the question, once per check it has earned."""
        plan = self.plan_for(trade)
        used = self._checks.get(trade.trade_id, 0) + 1
        self._checks[trade.trade_id] = used
        self._last_check[trade.trade_id] = self._now()
        if used >= plan.checks:
            # Out of checks: the answer is final either way, including when the
            # sell did not fill.
            self._evaluated.add(trade.trade_id)
        self.evaluations += 1

        analysis = await self.client.get_book_analysis(trade.ticker)
        book = None
        if analysis is not None:
            book = outcome_book(
                outcome=trade.outcome,
                yes_bid=analysis.yes_bid,
                yes_ask=analysis.yes_ask,
                yes_bid_size=analysis.yes_bid_size,
                no_bid_size=analysis.no_bid_size,
            )

        fee = (
            self._fee_per_contract(book.bid, trade.metadata)
            if book is not None
            else ZERO
        )
        decision = decide_salvage(
            book=book,
            contracts=trade.contracts,
            entry_price=trade.entry_price,
            exit_fee_per_contract=fee,
            max_bid=self.settings.salvage_max_bid,
            max_spread=self.settings.salvage_max_spread,
            haircut=self.settings.salvage_longshot_haircut,
            min_gain_per_contract=self.settings.salvage_min_gain_per_contract,
        )
        self._record(trade, book, decision)
        self._record_shadow(trade, book, plan, decision, fee)

        if not decision.should_exit:
            self.blocks[decision.gate] = self.blocks.get(decision.gate, 0) + 1
            logger.debug(
                "SALVAGE hold {} {} {}: {}",
                trade.asset,
                trade.ticker,
                trade.outcome.upper(),
                decision.reason,
            )
            return False

        assert book is not None  # decide_salvage refuses a None book
        return await self._sell(trade, book, decision)

    # ------------------------------------------------------------------ sell --

    async def _sell(self, trade: Any, book: Any, decision: SalvageDecision) -> bool:
        """Sell the whole position at the bid, or leave it alone."""
        price = book.bid
        contracts = min(trade.remaining_contracts, book.depth)

        if self.live:
            plan = OrderPlan(
                ticker=trade.ticker,
                outcome=trade.outcome,
                # The opposite side from the one that opened this position.
                # Together with reduce_only on the wire, this is why the path
                # cannot flip a side or open a short.
                sdk_book_side=book.book_side,
                sdk_yes_leg_price=book.yes_leg_price.quantize(CENTICENT),
                outcome_limit_price=price.quantize(CENTICENT),
                contracts=contracts,
                maximum_cost=(contracts * price).quantize(CENTICENT),
                fee_reserve=(decision.exit_fee * contracts).quantize(CENTICENT),
                maximum_total_debit=(contracts * price).quantize(CENTICENT),
                client_order_id=f"salvage-{uuid.uuid4().hex[:16]}",
                strategy=trade.strategy,
                intent="exit",
                trade_id=trade.trade_id,
                exchange_index=int(trade.metadata.get("exchange_index", 0)),
            )
            result = await self.client.place_exit_order(plan)
            if not result.filled:
                logger.warning(
                    "SALVAGE {} {} did not fill at {:.2f}; the position is held "
                    "to settlement",
                    trade.asset,
                    trade.ticker,
                    float(price),
                )
                self.blocks["unfilled"] = self.blocks.get("unfilled", 0) + 1
                self._record_event(
                    "salvage_unfilled",
                    trade_id=trade.trade_id,
                    ticker=trade.ticker,
                    asset=trade.asset,
                    price=price,
                    contracts=contracts,
                    error_code=result.error_code,
                )
                return False
            contracts = result.fill_count
            if result.average_fill_price is not None:
                price = (
                    result.average_fill_price
                    if trade.outcome == "yes"
                    else ONE - result.average_fill_price
                )

        fees = _ceil_to(
            self._fee_per_contract(price, trade.metadata) * contracts, CENTICENT
        )
        exited = self.ledger.record_exit_fill(
            trade.trade_id,
            client_order_id=(
                plan.client_order_id
                if self.live
                else f"paper-salvage-{uuid.uuid4().hex}"
            ),
            exit_price=price,
            exit_contracts=contracts,
            exit_fees=fees,
            exited_at=self._now(),
            exchange_status="salvage",
        )
        await self.positions.reduce(
            trade.trade_id,
            exited.remaining_contracts / trade.contracts,
        )
        if exited.status == "EXITED":
            self.exits += 1

        _event.warning(
            "SALVAGE EXIT {} {} {} | sold {} at {:.2f} | P&L ${} "
            "(held to settlement it was worth about {:.4f}/contract)",
            trade.asset,
            trade.ticker,
            trade.outcome.upper(),
            contracts,
            float(price),
            exited.pnl if exited.pnl is not None else "partial",
            float(decision.fair_value),
        )
        self._record_event(
            "salvage_exit",
            trade_id=trade.trade_id,
            ticker=trade.ticker,
            asset=trade.asset,
            outcome=trade.outcome,
            entry_price=trade.entry_price,
            exit_price=price,
            contracts=contracts,
            exit_fees=fees,
            fair_value=decision.fair_value,
            gain_per_contract=decision.gain_per_contract,
            pnl=exited.pnl,
            reason=decision.reason,
        )
        return exited.status == "EXITED"

    # --------------------------------------------------------------- helpers --

    def _fee_per_contract(self, price: Decimal, metadata: Any = None) -> Decimal:
        """Kalshi's quadratic taker fee for one contract at this price.

        The simulated fee is used even live, as everywhere else in this project:
        Kalshi reports the fee on a fill only after settlement, and this model
        errs high, so a live row understates rather than flatters.
        """
        multiplier = ONE
        if isinstance(metadata, dict):
            raw = metadata.get("fee_multiplier")
            if raw is not None:
                multiplier = Decimal(str(raw))
        clamped = max(CENT, min(Decimal("0.99"), price))
        return _ceil_to(
            Decimal("0.07") * multiplier * clamped * (ONE - clamped), CENTICENT
        )

    def _record(self, trade: Any, book: Any, decision: SalvageDecision) -> None:
        """Log the evaluation whichever way it went.

        Both verdicts, always. A rule that fires rarely by design is
        indistinguishable from a rule that is unreachable unless the refusals
        are on the record - which is how three shipped-but-dead features in this
        project were eventually found.
        """
        self._record_event(
            "salvage_evaluated",
            trade_id=trade.trade_id,
            ticker=trade.ticker,
            asset=trade.asset,
            strategy=trade.strategy,
            outcome=trade.outcome,
            contracts=trade.contracts,
            entry_price=trade.entry_price,
            total_debit=trade.total_debit,
            bid=decision.bid,
            spread=decision.spread,
            depth=(book.depth if book is not None else None),
            fair_value=decision.fair_value,
            exit_fee=decision.exit_fee,
            gain_per_contract=decision.gain_per_contract,
            should_exit=decision.should_exit,
            gate=decision.gate,
            reason=decision.reason,
            seconds_to_close=(trade.market_close_time - self._now()).total_seconds(),
        )

    def _record_shadow(
        self,
        trade: Any,
        book: Any,
        plan: OversightPlan,
        actual: SalvageDecision,
        fee: Decimal,
    ) -> None:
        """Record what a WIDER band would have done. Never acts on it.

        The question this exists to answer is the one that cannot be answered
        from first principles: should a large position be cut earlier than the
        0.15 ceiling the favourite-longshot bias was measured at? Raising the
        band to find out would be the exit-on-reversal again - it sold a live
        BTC position at 0.61 for -$1.73 on a market that then settled in that
        position's favour. Running it in shadow costs nothing, risks nothing,
        and produces the settled counterfactual that a decision needs.

        Joined to settlement later through ``trade_id`` and ``ticker``, so a
        week of these plus the ledger answers it with data rather than opinion.
        """
        # The CONFIGURED ceiling, not the exposure-scaled one. Scaling the
        # shadow band with exposure made the instrumentation collect nothing:
        # measured in the simulation, an 8.5% position earned a 0.218 shadow
        # band, so a bid of 0.27 fell outside even that and the two decisions
        # agreed - zero records after five evaluations. A band that only widens
        # for positions large enough to need it cannot answer whether the band
        # should widen, because the answer is wanted before such a position is
        # taken. Recording is free and never acts; the exposure and the scaled
        # band both travel with the record, so the analysis can narrow later.
        ceiling = getattr(
            self.settings, "oversight_shadow_max_bid", self.settings.salvage_max_bid
        )
        if ceiling <= self.settings.salvage_max_bid:
            # No wider band configured: nothing to counterfactual against, and
            # a record identical to the real decision is noise in a 38 GB log.
            return
        shadow = decide_salvage(
            book=book,
            contracts=trade.contracts,
            entry_price=trade.entry_price,
            exit_fee_per_contract=fee,
            max_bid=ceiling,
            max_spread=self.settings.salvage_max_spread,
            haircut=self.settings.salvage_longshot_haircut,
            min_gain_per_contract=self.settings.salvage_min_gain_per_contract,
        )
        if shadow.should_exit == actual.should_exit:
            # The bands agree; only a disagreement carries information about
            # whether the wider one is worth having.
            return
        self._record_event(
            "salvage_shadow",
            trade_id=trade.trade_id,
            ticker=trade.ticker,
            asset=trade.asset,
            strategy=trade.strategy,
            outcome=trade.outcome,
            contracts=trade.contracts,
            entry_price=trade.entry_price,
            total_debit=trade.total_debit,
            exposure_fraction=plan.exposure_fraction,
            portfolio_value=self._portfolio_value,
            shadow_max_bid=ceiling,
            exposure_scaled_band=plan.shadow_max_bid,
            acted_max_bid=self.settings.salvage_max_bid,
            bid=shadow.bid,
            spread=shadow.spread,
            fair_value=shadow.fair_value,
            exit_fee=shadow.exit_fee,
            gain_per_contract=shadow.gain_per_contract,
            shadow_would_exit=shadow.should_exit,
            actually_exited=actual.should_exit,
            shadow_gate=shadow.gate,
            reason=shadow.reason,
            check_number=self._checks.get(trade.trade_id, 0),
            checks_allowed=plan.checks,
            window_seconds=plan.window_seconds,
            seconds_to_close=(trade.market_close_time - self._now()).total_seconds(),
        )

    def _record_event(self, event: str, **fields: Any) -> None:
        """Write to the decision log, never fatally."""
        payload = {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in fields.items()
        }
        self.decisions.record_event(
            event, mode=self.mode, environment=self.settings.environment, **payload
        )

    def record_funnel(self, occasion: str) -> None:
        """Persist the counters, so a session with no exits still says why."""
        self._record_event(
            "salvage_funnel",
            occasion=occasion,
            evaluations=self.evaluations,
            exits=self.exits,
            blocks=dict(self.blocks),
        )
