"""Price-based take-profit supervision shared by paper and live execution."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from loguru import logger

from core.strategy_brain.strategies.salvage import outcome_book
from core.strategy_brain.strategies.take_profit import (
    decide_take_profit,
    is_active_winner,
)
from execution.kalshi_client import OrderPlan
from execution.trade_ledger import _ceil_to


ONE = Decimal("1")
ZERO = Decimal("0")
CENTICENT = Decimal("0.0001")


class TakeProfitExit:
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
        run_id_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.ledger = ledger
        self.decisions = decisions
        self.positions = positions
        self.mode = mode
        self.live = live
        self._now = now
        self._run_id_provider = run_id_provider or (lambda: None)
        self.evaluations = 0
        self.exits = 0
        self.blocks: dict[str, int] = {}
        self._last_checked: dict[str, datetime] = {}
        self._last_event_check: dict[str, float] = {}
        self._trade_locks: dict[str, asyncio.Lock] = {}
        self._probability_yes: dict[str, Decimal] = {}
        self._predictions: dict[str, Any] = {}
        self._peak_bid: dict[str, Decimal] = {}
        self._winning_streak: dict[str, int] = {}
        self._reversal_streak: dict[str, int] = {}

    def rebind(self, *, client: Any, mode: str, live: bool) -> None:
        self.client = client
        self.mode = mode
        self.live = live

    async def run(
        self,
        trades: list[Any],
        *,
        analyses: dict[str, Any] | None = None,
        probabilities: dict[str, Decimal] | None = None,
        predictions: dict[str, Any] | None = None,
    ) -> int:
        if probabilities:
            self._probability_yes.update(probabilities)
        if predictions:
            self._predictions.update(predictions)
            self._probability_yes.update(
                {
                    ticker: Decimal(str(prediction.probability_yes))
                    for ticker, prediction in predictions.items()
                }
            )
        open_ids = {trade.trade_id for trade in trades}
        self._last_checked = {
            trade_id: checked
            for trade_id, checked in self._last_checked.items()
            if trade_id in open_ids
        }
        for state in (self._peak_bid, self._winning_streak, self._reversal_streak):
            for trade_id in tuple(state):
                if trade_id not in open_ids:
                    state.pop(trade_id, None)
        exited = 0
        for trade in trades:
            moment = self._now()
            if not self.is_due(trade, moment):
                continue
            try:
                exited += int(
                    await self.evaluate(
                        trade,
                        analysis=(analyses or {}).get(trade.ticker),
                        model_probability=self._outcome_probability(trade),
                    )
                )
            except Exception:
                logger.exception("Take-profit evaluation failed for {}", trade.ticker)
            finally:
                self._last_checked[trade.trade_id] = moment
        return exited

    def is_due(self, trade: Any, moment: datetime | None = None) -> bool:
        """Use a steady cadence, then tighten it in the final two minutes."""
        now = moment or self._now()
        last = self._last_checked.get(trade.trade_id)
        if last is None:
            return True
        seconds_to_close = (trade.market_close_time - now).total_seconds()
        interval = (
            self.settings.take_profit_fast_check_seconds
            if seconds_to_close <= self.settings.take_profit_fast_window_seconds
            else self.settings.take_profit_check_interval_seconds
        )
        return (now - last).total_seconds() >= interval

    async def on_book(self, ticker: str, analysis: Any) -> int:
        """Reassess matching positions immediately after a sequenced book event."""
        now = time.monotonic()
        minimum_interval = float(
            getattr(self.settings, "event_reassessment_min_interval_seconds", 0.5)
        )
        if now - self._last_event_check.get(ticker, 0.0) < minimum_interval:
            return 0
        self._last_event_check[ticker] = now
        trades = [
            trade
            for trade in self.ledger.get_open_trades(
                self.mode, self.settings.environment
            )
            if trade.ticker == ticker
        ]
        return sum(
            [
                int(
                    await self.evaluate(
                        trade,
                        analysis=analysis,
                        trigger="book_event",
                        model_probability=self._outcome_probability(trade),
                    )
                )
                for trade in trades
            ]
        )

    def _outcome_probability(self, trade: Any) -> Decimal:
        probability_yes = self._probability_yes.get(trade.ticker)
        if probability_yes is None:
            return Decimal(str(trade.model_probability))
        return probability_yes if trade.outcome == "yes" else ONE - probability_yes

    async def evaluate(
        self,
        trade: Any,
        *,
        analysis: Any = None,
        trigger: str = "poll",
        model_probability: Decimal | None = None,
    ) -> bool:
        lock = self._trade_locks.setdefault(trade.trade_id, asyncio.Lock())
        async with lock:
            current = self.ledger.get_trade(trade.trade_id)
            if current is None or current.status != "OPEN":
                return False
            return await self._evaluate_unlocked(
                current,
                analysis=analysis,
                trigger=trigger,
                model_probability=(
                    model_probability
                    if model_probability is not None
                    else Decimal(str(current.model_probability))
                ),
            )

    async def _evaluate_unlocked(
        self,
        trade: Any,
        *,
        analysis: Any = None,
        trigger: str,
        model_probability: Decimal,
    ) -> bool:
        self.evaluations += 1
        if analysis is None:
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
        fee = self._fee_per_contract(book.bid, trade.metadata) if book else Decimal("0")
        seconds = Decimal(str((trade.market_close_time - self._now()).total_seconds()))
        peak_bid = self._peak_bid.get(trade.trade_id, book.bid if book else ZERO)
        if book is not None:
            peak_bid = max(peak_bid, book.bid)
            self._peak_bid[trade.trade_id] = peak_bid
        winning_now = bool(
            book is not None
            and is_active_winner(
                book_bid=book.bid,
                model_probability=model_probability,
                seconds_to_close=seconds,
                minimum_bid=Decimal(
                    str(
                        getattr(
                            self.settings,
                            "take_profit_winning_hold_min_bid",
                            "0.85",
                        )
                    )
                ),
                minimum_probability=Decimal(
                    str(
                        getattr(
                            self.settings,
                            "take_profit_winning_hold_min_probability",
                            "0.65",
                        )
                    )
                ),
                strong_book_bid=Decimal(
                    str(
                        getattr(
                            self.settings,
                            "take_profit_winning_hold_strong_book_bid",
                            "0.95",
                        )
                    )
                ),
                maximum_seconds_to_close=Decimal(
                    str(
                        getattr(
                            self.settings,
                            "take_profit_winning_hold_max_seconds_to_close",
                            0,
                        )
                    )
                ),
            )
        )
        winning_streak = self._winning_streak.get(trade.trade_id, 0)
        winning_streak = winning_streak + 1 if winning_now else 0
        self._winning_streak[trade.trade_id] = winning_streak

        prediction = self._predictions.get(trade.ticker)
        aligned_z: Decimal | None = None
        signed_momentum: Decimal | None = None
        if prediction is not None:
            raw_z = Decimal(str(prediction.distance_z))
            raw_momentum = Decimal(str(prediction.raw_momentum_return))
            aligned_z = raw_z if trade.outcome == "yes" else -raw_z
            signed_momentum = raw_momentum if trade.outcome == "yes" else -raw_momentum
        reversal_signals = {
            "probability": model_probability
            <= Decimal(
                str(
                    getattr(
                        self.settings,
                        "confirmed_reversal_probability_max",
                        "0.48",
                    )
                )
            ),
            "peak_drawdown": bool(
                book is not None
                and peak_bid - book.bid
                >= Decimal(
                    str(
                        getattr(
                            self.settings,
                            "confirmed_reversal_peak_drawdown",
                            "0.15",
                        )
                    )
                )
            ),
            "strike_cross": aligned_z is not None and aligned_z <= ZERO,
            "momentum": signed_momentum is not None and signed_momentum < ZERO,
            "book": book is not None and book.bid < Decimal("0.50"),
        }
        reversal_signal_count = sum(reversal_signals.values())
        # A sub-50 model estimate by itself can be noisy, and a temporary
        # strike/momentum cross can occur while the held contract remains the
        # executable favorite.  Require both probability and saleable book
        # price to agree that the position is losing before persistence can
        # accumulate.  The remaining signals still provide confirmation.
        reversal_now = (
            reversal_signals["probability"]
            and reversal_signals["book"]
            and reversal_signal_count
            >= int(getattr(self.settings, "confirmed_reversal_min_signals", 3))
        )
        reversal_streak = self._reversal_streak.get(trade.trade_id, 0)
        reversal_streak = reversal_streak + 1 if reversal_now else 0
        self._reversal_streak[trade.trade_id] = reversal_streak
        reversal_required = int(
            getattr(self.settings, "confirmed_reversal_confirmations", 2)
        )
        confirmed_reversal = bool(
            getattr(self.settings, "confirmed_reversal_enabled", False)
            and reversal_streak >= reversal_required
        )
        decision = decide_take_profit(
            book=book,
            contracts=trade.remaining_contracts,
            position_contracts=trade.contracts,
            total_debit=trade.total_debit,
            exit_fee_per_contract=fee,
            minimum_gain_per_contract=self.settings.take_profit_per_contract,
            maximum_spread=self.settings.take_profit_max_spread,
            seconds_to_close=seconds,
            minimum_seconds_to_close=Decimal(
                self.settings.take_profit_min_seconds_to_close
            ),
            minimum_capture_fraction=Decimal(
                str(
                    getattr(
                        self.settings,
                        "take_profit_min_capture_fraction",
                        "0.75",
                    )
                )
            ),
            model_probability=model_probability,
            confidence_margin=Decimal(
                str(getattr(self.settings, "take_profit_confidence_margin", "0.02"))
            ),
            winning_hold_enabled=bool(
                getattr(self.settings, "take_profit_winning_hold_enabled", True)
            ),
            winning_hold_min_bid=Decimal(
                str(getattr(self.settings, "take_profit_winning_hold_min_bid", "0.85"))
            ),
            winning_hold_min_probability=Decimal(
                str(
                    getattr(
                        self.settings,
                        "take_profit_winning_hold_min_probability",
                        "0.65",
                    )
                )
            ),
            winning_hold_strong_book_bid=Decimal(
                str(
                    getattr(
                        self.settings,
                        "take_profit_winning_hold_strong_book_bid",
                        "0.95",
                    )
                )
            ),
            winning_hold_max_seconds_to_close=Decimal(
                str(
                    getattr(
                        self.settings,
                        "take_profit_winning_hold_max_seconds_to_close",
                        0,
                    )
                )
            ),
            winning_hold_confirmation_count=winning_streak,
            winning_hold_required_confirmations=int(
                getattr(self.settings, "take_profit_winning_hold_confirmations", 2)
            ),
            confirmed_reversal=confirmed_reversal,
            confirmed_reversal_reason=(
                f"{reversal_signal_count}/5 deterioration signals confirmed "
                f"{reversal_streak}/{reversal_required} times: "
                + ", ".join(name for name, hit in reversal_signals.items() if hit)
            ),
        )
        self.ledger.record_position_evaluation(
            run_id=self._run_id_provider(),
            trade_id=trade.trade_id,
            trigger={"poll": "timer", "book_event": "book"}.get(trigger, trigger),
            executable_exit_price=book.bid if book is not None else None,
            executable_depth=book.depth if book is not None else Decimal("0"),
            estimated_exit_fee=decision.exit_fee,
            net_executable_pnl=decision.gain_per_contract * trade.remaining_contracts,
            model_probability=decision.model_probability,
            required_gain_per_contract=decision.required_gain_per_contract,
            maximum_settlement_gain_per_contract=(
                decision.maximum_settlement_gain_per_contract
            ),
            spread=decision.spread,
            seconds_to_close=float(seconds),
            verdict="exit" if decision.should_exit else "hold",
            gate=decision.gate,
            evaluated_at=self._now(),
        )
        self._record("take_profit_evaluated", trade, decision)
        if not decision.should_exit:
            self.blocks[decision.gate] = self.blocks.get(decision.gate, 0) + 1
            return False

        # Defense in depth at the side-effect boundary.  The pure policy above
        # owns the decision, but no future strategy change may reach Kalshi with
        # a loss-making automatic exit even if that policy regresses.
        if decision.gain_per_contract < ZERO:
            self.blocks["principal_protection"] = (
                self.blocks.get("principal_protection", 0) + 1
            )
            logger.error(
                "AUTOMATIC EXIT BLOCKED for {}: net executable gain {} per "
                "contract is below break-even",
                trade.ticker,
                decision.gain_per_contract,
            )
            return False

        assert book is not None
        price = book.bid
        contracts = min(trade.remaining_contracts, book.depth)
        if self.live:
            if hasattr(self.client, "get_position_contracts"):
                venue_contracts = await self.client.get_position_contracts(
                    trade.ticker, trade.outcome
                )
                contracts = min(contracts, venue_contracts)
                if contracts <= 0:
                    self.blocks["venue_position"] = (
                        self.blocks.get("venue_position", 0) + 1
                    )
                    logger.error(
                        "Exit blocked for {}: ledger holds {} {} but Kalshi "
                        "reports no matching position; reconciliation required",
                        trade.ticker,
                        trade.remaining_contracts,
                        trade.outcome.upper(),
                    )
                    return False
            plan = OrderPlan(
                ticker=trade.ticker,
                outcome=trade.outcome,
                sdk_book_side=book.book_side,
                sdk_yes_leg_price=book.yes_leg_price.quantize(CENTICENT),
                outcome_limit_price=price.quantize(CENTICENT),
                contracts=contracts,
                maximum_cost=(contracts * price).quantize(CENTICENT),
                fee_reserve=(decision.exit_fee * contracts).quantize(CENTICENT),
                maximum_total_debit=(contracts * price).quantize(CENTICENT),
                client_order_id=f"exit-{uuid.uuid4().hex[:16]}",
                strategy=trade.strategy,
                intent="exit",
                trade_id=trade.trade_id,
                exchange_index=int(trade.metadata.get("exchange_index", 0)),
            )
            result = await self.client.place_exit_order(plan)
            if not result.filled:
                self.blocks["unfilled"] = self.blocks.get("unfilled", 0) + 1
                self._record("take_profit_unfilled", trade, decision)
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
                else f"paper-profit-{uuid.uuid4().hex}"
            ),
            exit_price=price,
            exit_contracts=contracts,
            exit_fees=fees,
            exited_at=self._now(),
            exchange_status=decision.gate,
        )
        await self.positions.reduce(
            trade.trade_id,
            exited.remaining_contracts / trade.contracts,
        )
        if exited.status == "EXITED":
            self.exits += 1
        logger.warning(
            "POSITION EXIT [{}] {} {} {} | sold {} at {:.2f} | P&L ${}",
            decision.gate,
            trade.asset,
            trade.ticker,
            trade.outcome.upper(),
            contracts,
            float(price),
            exited.pnl if exited.pnl is not None else "partial",
        )
        self._record(
            "confirmed_reversal_exit"
            if decision.gate == "confirmed_reversal"
            else "take_profit_exit",
            exited,
            decision,
        )
        return exited.status == "EXITED"

    @staticmethod
    def _fee_per_contract(price: Decimal, metadata: Any = None) -> Decimal:
        multiplier = Decimal("1")
        if isinstance(metadata, dict) and metadata.get("fee_multiplier") is not None:
            multiplier = Decimal(str(metadata["fee_multiplier"]))
        clamped = max(Decimal("0.01"), min(Decimal("0.99"), price))
        return _ceil_to(
            Decimal("0.07") * multiplier * clamped * (ONE - clamped), CENTICENT
        )

    def _record(self, event: str, trade: Any, decision: Any) -> None:
        self.decisions.record_event(
            event,
            mode=self.mode,
            environment=self.settings.environment,
            trade_id=trade.trade_id,
            strategy=trade.strategy,
            asset=trade.asset,
            ticker=trade.ticker,
            outcome=trade.outcome,
            entry_sequence=trade.entry_sequence,
            bid=str(decision.bid),
            spread=str(decision.spread),
            cost_basis=str(decision.cost_basis),
            exit_fee=str(decision.exit_fee),
            gain_per_contract=str(decision.gain_per_contract),
            required_gain_per_contract=str(decision.required_gain_per_contract),
            maximum_settlement_gain_per_contract=str(
                decision.maximum_settlement_gain_per_contract
            ),
            model_probability=(
                str(decision.model_probability)
                if decision.model_probability is not None
                else None
            ),
            gate=decision.gate,
            reason=decision.reason,
            should_exit=decision.should_exit,
        )

    def record_funnel(self, occasion: str) -> None:
        self.decisions.record_event(
            "take_profit_funnel",
            mode=self.mode,
            environment=self.settings.environment,
            occasion=occasion,
            evaluations=self.evaluations,
            exits=self.exits,
            blocks=dict(self.blocks),
        )
