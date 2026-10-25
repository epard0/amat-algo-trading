"""Execution stage: timing, window pricing, slippage, fees, missed-fill policy.

Implements :class:`amat.boundaries.ExecutionBoundary`.

Scope note: "execution" here means *simulated* execution against historical
bars. This package contains no broker client, no network transport, and no
account state, and must not acquire any.

Contract for this package:

* a decision formed after session ``t`` may fill no earlier than the configured
  window of session ``t+1``;
* the canonical fill price is the raw-price VWAP of that window plus a
  side-adjusted one-way cost;
* a missing or zero-volume window produces a recorded missed fill; a prior
  close or forward fill is never substituted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from amat.execution.budget import CashBudgetPolicy

from amat.config.models import CostConfig, ExecutionConfig, RebalanceConfig
from amat.domain.bars import ExecutionWindowBar
from amat.domain.fills import Fill, MissedFill, MissedFillReason
from amat.domain.orders import SimulatedOrder, TradeReason
from amat.domain.positions import Position
from amat.domain.types import Cash, Price, SessionDate, Side, Symbol
from amat.errors import CausalityError

__all__ = ["SimulatedExecutionModel", "build_instructions", "one_way_cost"]

#: Basis points per unit. One basis point is 1/10,000.
_BPS = Decimal("10000")


def one_way_cost(notional: Cash, one_way_bps: float) -> Cash:
    """Side-adjusted one-way transaction cost for a traded notional.

    The cost is always a non-negative magnitude and is stored separately from
    the fill price, so gross and net results stay separately attributable. Both
    directions pay it: a sell releases the notional but still bears the cost.
    """
    rate = Decimal(str(one_way_bps)) / _BPS
    return (abs(notional) * rate).quantize(Decimal("0.000001"))


def build_instructions(
    targets: Mapping[Symbol, float],
    positions: Mapping[Symbol, Position],
    marks: Mapping[Symbol, Price],
    equity: Cash,
    *,
    signal_session: SessionDate,
    execution_session: SessionDate,
    rebalance: RebalanceConfig,
    execution: ExecutionConfig,
    in_flight: Mapping[Symbol, Decimal] | None = None,
) -> tuple[list[SimulatedOrder], list[MissedFill]]:
    """Convert target weights into simulated instructions for a future session.

    A trade is emitted only for a reason the contract permits: a weight delta at
    or above the threshold, or a zero/non-zero transition. Instructions below the
    minimum notional are *recorded as missed fills* rather than dropped, so the
    reason a target was not reached is never silently lost.

    Share counts are computed from the decision session's marks, which are known
    at decision time. The fill price is the next session's window VWAP, which is
    not -- that gap is genuine execution risk and is preserved deliberately.

    ``in_flight`` is the net signed quantity already scheduled but not yet
    settled, per symbol. With the baseline one-session lag it is always empty
    -- every instruction settles before the next decision forms -- but at a
    stressed lag of two or more, consecutive decisions overlap, and a decision
    blind to its predecessor's scheduled exit would sell the same shares twice
    (measured: an ``AccountingError`` short). Netting scheduled quantities
    into the deltas is what "the same strategy, executed later" actually
    means; assuming a scheduled instruction fills is the same assumption the
    scheduling decision itself already made, and if it misses, the next
    decision sees the settled truth and re-issues.

    Raises:
        CausalityError: if the execution session does not respect the configured
            lag, which is the canonical look-ahead defect.
    """
    if execution_session <= signal_session:
        message = (
            f"execution session {execution_session.isoformat()} must be strictly after "
            f"signal session {signal_session.isoformat()}"
        )
        raise CausalityError(message)
    if execution.signal_to_execution_lag_sessions < 1:  # pragma: no cover - pinned ge=1
        message = "signal_to_execution_lag_sessions must be at least 1"
        raise CausalityError(message)

    in_flight = in_flight or {}
    instructions: list[SimulatedOrder] = []
    missed: list[MissedFill] = []

    for symbol in sorted(set(targets) | set(positions) | set(in_flight)):
        mark = marks.get(symbol)
        if mark is None:
            continue

        held = positions.get(symbol)
        held_quantity = held.quantity if held is not None else Decimal(0)
        # A pending exit may exceed holdings after an earlier buy only partly
        # filled or missed. Settlement clips that exit to the shares actually
        # held; it cannot create a short. Do not size a replacement buy against
        # a fictitious negative position while waiting for the exit to settle.
        held_quantity = max(Decimal(0), held_quantity + in_flight.get(symbol, Decimal(0)))
        current_weight = float(held_quantity * mark / equity) if equity > 0 else 0.0
        target_weight = float(targets.get(symbol, 0.0))

        delta_weight = target_weight - current_weight
        transition = (target_weight > 0) != (current_weight > 0)
        if abs(delta_weight) < rebalance.min_weight_delta and not transition:
            continue

        target_quantity = (Decimal(str(target_weight)) * equity / mark) if mark > 0 else Decimal(0)
        delta_quantity = target_quantity - held_quantity
        if delta_quantity == 0:
            continue

        side = Side.BUY if delta_quantity > 0 else Side.SELL
        quantity = abs(delta_quantity)
        reason = TradeReason.POSITION_TRANSITION if transition else TradeReason.WEIGHT_DELTA

        if quantity * mark < rebalance.min_trade_notional:
            missed.append(
                MissedFill(
                    symbol=symbol,
                    execution_session=execution_session,
                    side=side,
                    requested_quantity=quantity,
                    reason=MissedFillReason.BELOW_MINIMUM_NOTIONAL,
                )
            )
            continue

        instructions.append(
            SimulatedOrder(
                symbol=symbol,
                signal_session=signal_session,
                execution_session=execution_session,
                side=side,
                quantity=quantity,
                reason=reason,
            )
        )
    return instructions, missed


class SimulatedExecutionModel:
    """Fills instructions against a historical execution window.

    Satisfies :class:`amat.boundaries.ExecutionBoundary`. It holds no account
    state and reaches no network; it is a pure function of its inputs plus the
    configured cost.
    """

    def __init__(
        self,
        execution: ExecutionConfig,
        costs: CostConfig,
        *,
        one_way_bps: float | None = None,
        max_entry_window_participation: float | None = None,
        drop_every_nth_fill: int | None = None,
        cash_policy: CashBudgetPolicy | None = None,
        whole_share_buys: bool = False,
    ) -> None:
        """Build the model.

        Args:
            execution: Window, pricing basis, and missing-bar policy.
            costs: Cost configuration supplying the baseline.
            one_way_bps: Override used by the cost-stress ladder. Defaults to
                the configured baseline.
            drop_every_nth_fill: Missed-fill stress injection: every Nth fill
                that would otherwise execute is recorded as a
                ``STRESS_DROPPED`` missed fill instead (N=20 is a 5% rate,
                N=10 is 10%). Deterministic -- the counter advances in the
                model's fixed sells-then-buys, alphabetical order -- so a
                stressed run reproduces exactly. ``None`` (the default, and
                the only primary-run value) injects nothing.
        """
        self._cash_policy = cash_policy
        self._whole_share_buys = whole_share_buys
        self._execution = execution
        self._costs = costs
        self._one_way_bps = costs.baseline_one_way_bps if one_way_bps is None else one_way_bps
        if max_entry_window_participation is not None and not (
            0.0 < max_entry_window_participation <= 1.0
        ):
            message = "max_entry_window_participation must lie inside (0, 1]"
            raise CausalityError(message)
        self._max_entry_window_participation = max_entry_window_participation
        if drop_every_nth_fill is not None and drop_every_nth_fill < 2:  # noqa: PLR2004
            message = "drop_every_nth_fill must be at least 2 (N=2 drops half of all fills)"
            raise CausalityError(message)
        self._drop_every_nth_fill = drop_every_nth_fill
        self._fill_counter = 0

    @property
    def one_way_bps(self) -> float:
        """The cost actually applied, in basis points."""
        return self._one_way_bps

    def execute(  # noqa: PLR0912 - explicit fill outcomes
        self,
        instructions: Sequence[SimulatedOrder],
        windows: Mapping[Symbol, ExecutionWindowBar],
        available_cash: Cash | None = None,
        account_equity: Cash | None = None,
    ) -> tuple[list[Fill], list[MissedFill]]:
        """Attempt each instruction against its execution window.

        A window that is absent, untradable, or on the wrong session yields a
        :class:`~amat.domain.fills.MissedFill` naming the reason. No price is
        ever synthesized, carried forward, or borrowed from a neighbouring
        session -- that is the guardrail-9 silent fallback this method exists to
        prevent.

        Sells settle before buys, and when ``available_cash`` is supplied a buy
        that the running balance cannot fund is recorded as
        ``INSUFFICIENT_CASH`` rather than overdrawing the account. Sizing happens
        at the decision session's marks but filling happens at a later session's
        price, so this gap is real execution risk, not an accounting artifact.
        Passing ``None`` disables the funding check and is intended only for
        isolated unit tests.
        """
        if self._cash_policy is not None:
            if available_cash is None or account_equity is None:
                raise CausalityError("cash policy requires an actual supplied balance")
            return self._execute_budgeted(instructions, windows, available_cash, account_equity)
        fills: list[Fill] = []
        missed: list[MissedFill] = []

        priced: list[tuple[SimulatedOrder, Price, Decimal]] = []
        for instruction in instructions:
            window = windows.get(instruction.symbol)
            if window is None:
                missed.append(self._miss(instruction, MissedFillReason.NO_EXECUTION_BAR))
                continue
            if window.session_date != instruction.execution_session:
                missed.append(self._miss(instruction, MissedFillReason.NON_SESSION_DATE))
                continue
            if not window.is_tradable:
                missed.append(self._miss(instruction, MissedFillReason.ZERO_VOLUME))
                continue
            priced.append((instruction, self._fill_price(window), window.volume))

        # Sells first: their proceeds are what funds the buys, exactly as a
        # cash account behaves. Within each side, order is deterministic by
        # symbol so a run is reproducible.
        priced.sort(key=lambda item: (item[0].side == Side.BUY, item[0].symbol))

        balance = available_cash
        for instruction, price, window_volume in priced:
            quantity = instruction.quantity
            if instruction.side == Side.BUY and self._max_entry_window_participation is not None:
                maximum = window_volume * Decimal(str(self._max_entry_window_participation))
                if quantity > maximum:
                    remainder = quantity - maximum
                    quantity = maximum
                    missed.append(
                        MissedFill(
                            symbol=instruction.symbol,
                            execution_session=instruction.execution_session,
                            side=instruction.side,
                            requested_quantity=remainder,
                            reason=MissedFillReason.PARTICIPATION_CAP,
                        )
                    )
            if self._whole_share_buys and (
                instruction.side == Side.BUY
                or instruction.reason != TradeReason.POSITION_TRANSITION
            ):
                rounded = quantity.to_integral_value(rounding=ROUND_DOWN)
                if quantity > rounded:
                    missed.append(
                        replace(
                            self._miss(instruction, MissedFillReason.QUANTITY_INCREMENT),
                            requested_quantity=quantity - rounded,
                        )
                    )
                quantity = rounded
                if quantity <= 0:
                    continue
            if self._drop_every_nth_fill is not None:
                self._fill_counter += 1
                if self._fill_counter % self._drop_every_nth_fill == 0:
                    missed.append(self._miss(instruction, MissedFillReason.STRESS_DROPPED))
                    continue

            notional = quantity * price
            cost = one_way_cost(notional, self._one_way_bps)

            if instruction.side == Side.BUY and balance is not None:
                required = notional + cost
                if required > balance:
                    missed.append(self._miss(instruction, MissedFillReason.INSUFFICIENT_CASH))
                    continue
                balance -= required
            elif balance is not None:
                balance += notional - cost

            fills.append(
                Fill(
                    symbol=instruction.symbol,
                    execution_session=instruction.execution_session,
                    side=instruction.side,
                    quantity=quantity,
                    price=price,
                    cost_cash=cost,
                    reason=instruction.reason,
                )
            )
        return fills, missed

    def _execute_budgeted(
        self,
        instructions: Sequence[SimulatedOrder],
        windows: Mapping[Symbol, ExecutionWindowBar],
        available_cash: Cash,
        equity: Cash,
    ) -> tuple[list[Fill], list[MissedFill]]:
        """Aggregate-window proxy: confirmed simulated sales fund proportional buys.

        This is not a claim of tick-level sequencing. The supplied window prices
        are fill-model inputs, never earlier signal inputs.
        """
        from amat.execution.budget import budget_buys  # noqa: PLC0415 - avoid import cycle

        base = SimulatedExecutionModel(
            self._execution,
            self._costs,
            one_way_bps=self._one_way_bps,
            max_entry_window_participation=self._max_entry_window_participation,
            whole_share_buys=self._whole_share_buys,
            drop_every_nth_fill=self._drop_every_nth_fill,
        )
        base._fill_counter = self._fill_counter
        sells = [o for o in instructions if o.side is Side.SELL]
        buys = [o for o in instructions if o.side is Side.BUY]
        fills, missed = base.execute(sells, windows, available_cash=available_cash)
        cash = available_cash + sum((f.cash_delta for f in fills), Decimal(0))
        tradable = {
            o.symbol: o
            for o in buys
            if o.symbol in windows
            and windows[o.symbol].is_tradable
            and windows[o.symbol].session_date == o.execution_session
        }
        if len({o.symbol for o in buys}) != len(buys):
            raise CausalityError("cash-policy batch requires netted buy instructions")
        prices = {str(s): self._fill_price(windows[s]) for s in tradable}
        requested = {str(s): o.quantity for s, o in tradable.items()}
        quantities, residual = budget_buys(
            requested,
            prices,
            confirmed_cash=cash,
            equity=equity,
            one_way_bps=self._one_way_bps,
            policy=self._cash_policy,
        )
        adjusted = []
        for order in buys:
            if order.symbol not in tradable:
                adjusted.append(order)
                continue
            remaining = residual[str(order.symbol)]
            if remaining > 0:
                missed.append(
                    replace(
                        self._miss(order, MissedFillReason.INSUFFICIENT_CASH),
                        requested_quantity=remaining,
                    )
                )
            if quantities[str(order.symbol)] > 0:
                adjusted.append(replace(order, quantity=quantities[str(order.symbol)]))
        buy_fills, buy_missed = base.execute(adjusted, windows, available_cash=cash)
        self._fill_counter = base._fill_counter
        return [*fills, *buy_fills], [*missed, *buy_missed]

    def _fill_price(self, window: ExecutionWindowBar) -> Price:
        """Canonical fill price: the raw VWAP of the configured window."""
        if self._execution.price_method != "vwap":  # pragma: no cover - pinned by Literal
            message = f"unsupported price_method {self._execution.price_method!r}"
            raise CausalityError(message)
        return window.vwap

    @staticmethod
    def _miss(instruction: SimulatedOrder, reason: MissedFillReason) -> MissedFill:
        return MissedFill(
            symbol=instruction.symbol,
            execution_session=instruction.execution_session,
            side=instruction.side,
            requested_quantity=instruction.quantity,
            reason=reason,
        )
