"""Turn a plan's intents into paper orders, or refuse and say why.

This module is where "long-or-cash research artifact" meets "an account that
will do whatever it is told". Every check below exists because the failure it
prevents is silent:

* **An expired or wrong-session plan is not executed.** A plan formed from
  Tuesday's close is not a licence to trade on Friday, and the plan itself
  names the earliest session it may execute in.
* **Unrecognised holdings stop the run.** If the account holds something the
  strategy's universe does not contain, the account and the strategy disagree
  about what is held. The strategy repository's plan builder refuses to emit an
  instruction for it; this module refuses to trade *anything* until a human
  resolves it, because the equity those holdings represent is in the
  denominator of every weight in the plan.
* **Non-fractionable symbols round down, never up.** Rounding up buys exposure
  the allocator did not ask for. The residual is recorded, not discarded.
* **A sell is clamped to the position actually held.** Long-or-cash means a
  sell can reduce or close, never cross through zero into a short.
* **Order IDs are deterministic.** Running the same plan twice is rejected by
  the broker as a duplicate rather than doubling the position.
* **Only an authorised configuration is traded.** A plan whose
  ``config_sha256`` is not in ``AUTHORIZED_CONFIG_SHA256`` is refused, so a
  strategy change never reaches the broker without a human adding its digest
  to the allowlist.
* **The operator's kill switch is checked first.** ``EXECUTOR_DISABLED``
  refuses every submission before anything else is evaluated.
* **Broker equity and plan equity may not drift apart.** The plan was sized on
  an equity value fetched earlier; if the broker's current equity has moved
  more than ``EQUITY_TOLERANCE``, the plan was sized on a different account
  state and is not safe to execute.
* **Session and per-symbol notional, and order count, are capped.** These caps
  exist independent of anything the plan itself asserts, so a strategy defect
  that emits an oversized plan is caught here rather than at the broker.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from executor.broker import Account, AlpacaPaperClient, Order
from executor.config import RiskLimits
from executor.errors import BrokerError, OrderOutcomeUnknownError
from executor.plan_source import ExecutionPlan, PlanIntent
from executor.safety import atomic_json, read_receipt, submission_interlock

if TYPE_CHECKING:
    from executor.journal import Journal

__all__ = [
    "MAX_QUANTITY_DECIMALS",
    "SubmissionOutcome",
    "SubmissionResult",
    "client_order_id_for",
    "submit_plan",
]

#: Alpaca caps `client_order_id` at 128 characters.
_MAX_CLIENT_ORDER_ID = 128

#: The finest quantity the broker accepts: nine decimal places on `qty`.
#: Anything longer is rejected outright rather than truncated for us, so every
#: quantity leaving this module is quantized to it. Documented at
#: https://docs.alpaca.markets/us/docs/fractional-trading -- "Both fields
#: support up to 9 decimal places."
MAX_QUANTITY_DECIMALS = Decimal("1E-9")


def client_order_id_for(plan: ExecutionPlan, intent: PlanIntent) -> str:
    """A stable idempotency key for one intent within one plan.

    Derived from the decision session, the symbol, the side, the quantity, and
    the configuration digest. Two runs of the same plan produce the same key and
    the broker rejects the second as a duplicate. A *changed* plan -- different
    config, different size -- produces a different key and is allowed through,
    which is the correct behaviour when an operator deliberately re-plans.
    """
    material = "|".join(
        (
            plan.as_of_session.isoformat(),
            plan.config_sha256,
            intent.symbol,
            intent.side,
            format(intent.quantity.normalize(), "f"),
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    candidate = f"amat-{plan.as_of_session.isoformat()}-{intent.symbol}-{intent.side}-{digest}"
    return candidate[:_MAX_CLIENT_ORDER_ID]


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """What happened to one intent."""

    symbol: str
    side: str
    intended_quantity: Decimal
    submitted_quantity: Decimal
    status: str
    client_order_id: str
    order_id: str = ""
    detail: str = ""

    @property
    def was_submitted(self) -> bool:
        return self.status == "submitted"

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "intended_quantity": str(self.intended_quantity),
            "submitted_quantity": str(self.submitted_quantity),
            "status": self.status,
            "client_order_id": self.client_order_id,
            "order_id": self.order_id,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    """The whole run: what was sent, what was held back, and why."""

    session: dt.date
    executed: bool
    refusal_reason: str
    outcomes: tuple[SubmissionOutcome, ...] = ()
    account_equity_before: Decimal = Decimal("0")
    dry_run: bool = False
    orders: tuple[Order, ...] = field(default=(), repr=False)

    @property
    def submitted(self) -> tuple[SubmissionOutcome, ...]:
        return tuple(item for item in self.outcomes if item.was_submitted)

    @property
    def not_submitted(self) -> tuple[SubmissionOutcome, ...]:
        return tuple(item for item in self.outcomes if not item.was_submitted)

    def as_dict(self) -> dict[str, object]:
        return {
            "session": self.session.isoformat(),
            "executed": self.executed,
            "refusal_reason": self.refusal_reason,
            "dry_run": self.dry_run,
            "account_equity_before": str(self.account_equity_before),
            "outcomes": [item.as_dict() for item in self.outcomes],
        }


def _preflight(  # noqa: PLR0912 - ordered fail-closed validation
    plan: ExecutionPlan,
    account: Account,
    session: dt.date,
    now: dt.datetime,
    *,
    limits: RiskLimits,
) -> str:
    """Return a refusal reason, or the empty string when it is safe to trade.

    Order matters: the operator's own switches come first, then identity, then
    staleness, then account state, then size. Each refusal names the limit that
    produced it so the journal entry is actionable without reading this code.
    """
    if limits.disabled:
        return "EXECUTOR_DISABLED is true: every submission is refused by operator switch"
    digest = plan.config_sha256.lower()
    if digest not in limits.authorized_config_sha256:
        return (
            f"strategy configuration {digest[:12]}... is not authorised for this executor "
            f"(AUTHORIZED_CONFIG_SHA256 lists {len(limits.authorized_config_sha256)} digest(s)); "
            f"a plan from an unlisted configuration is never traded"
        )
    if not account.is_tradable:
        return (
            f"broker will not accept orders: status={account.status} "
            f"trading_blocked={account.trading_blocked} "
            f"account_blocked={account.account_blocked}"
        )
    numbers = [plan.account_equity, account.equity, *plan.current_positions.values()]
    if any(not value.is_finite() or value < 0 for value in numbers) or plan.account_equity <= 0:
        return "invalid or non-finite plan/account quantities"
    if not math.isfinite(plan.gross_target_exposure) or plan.gross_target_exposure < 0:
        return "invalid gross target exposure"
    if len({intent.symbol for intent in plan.intents}) != len(plan.intents):
        return "duplicate symbol intents are not supported"
    for intent in plan.intents:
        values = (intent.quantity, intent.reference_price, intent.notional)
        if (
            intent.side not in {"buy", "sell"}
            or re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,31}", intent.symbol) is None
            or any(not value.is_finite() or value <= 0 for value in values)
            or not math.isfinite(intent.target_weight)
            or not 0 <= intent.target_weight <= 1
        ):
            return "invalid intent side, sizing, or target weight"
    if plan.is_expired(now=now):
        return (
            f"plan expired at {plan.expires_at.isoformat()} and must not be executed late; "
            f"a stale signal is not a licence to trade"
        )
    if not plan.is_executable_on(session):
        return (
            f"plan may not execute before {plan.earliest_execution_session.isoformat()}, "
            f"and today is {session.isoformat()}"
        )
    if plan.unrecognized_holdings:
        return (
            f"account holds {', '.join(plan.unrecognized_holdings)}, which the strategy "
            f"universe does not contain. Their value sits in the denominator of every "
            f"target weight in this plan, so no trade is safe until a human resolves it."
        )
    if plan.account_equity > 0:
        drift = abs(account.equity - plan.account_equity) / plan.account_equity
        if drift > limits.equity_tolerance:
            return (
                f"broker equity {account.equity} differs from the plan's sizing equity "
                f"{plan.account_equity} by {drift:.4%}, beyond EQUITY_TOLERANCE "
                f"{limits.equity_tolerance}; the plan was sized on a different account"
            )
    if not plan.intents:
        return "no trades: the account already matches the target within the rebalance band"
    if (
        limits.max_orders_per_session is not None
        and len(plan.intents) > limits.max_orders_per_session
    ):
        return (
            f"plan carries {len(plan.intents)} intents, above MAX_ORDERS_PER_SESSION "
            f"{limits.max_orders_per_session}"
        )
    session_notional = sum(
        (max(intent.notional, intent.quantity * intent.reference_price) for intent in plan.intents),
        Decimal("0"),
    )
    if limits.max_session_notional is not None and session_notional > limits.max_session_notional:
        return (
            f"plan reference notional {session_notional} exceeds MAX_SESSION_NOTIONAL "
            f"{limits.max_session_notional}"
        )
    oversized = [
        intent
        for intent in plan.intents
        if limits.max_symbol_notional is not None
        and max(intent.notional, intent.quantity * intent.reference_price)
        > limits.max_symbol_notional
    ]
    if oversized:
        names = ", ".join(f"{item.symbol}={item.notional}" for item in oversized)
        return f"intent notional above MAX_SYMBOL_NOTIONAL {limits.max_symbol_notional}: {names}"
    return ""


def _resolve_quantity(
    intent: PlanIntent,
    *,
    fractionable: bool,
    held: Decimal,
) -> tuple[Decimal, str]:
    """Size the order actually sendable for one intent.

    Returns the quantity and a note describing any adjustment. A returned
    quantity of zero means the intent must be withheld.

    The final quantization to :data:`MAX_QUANTITY_DECIMALS` is deliberately
    silent. It is a unit conversion into the wire format's precision, not an
    adjustment to the decision: at a billionth of a share the residual is worth
    a millionth of a cent, and a note on every order about it would bury the
    notes that describe real money. The rounding-down of a non-fractionable
    symbol, which can strand whole dollars, is noted.
    """
    quantity = intent.quantity
    notes: list[str] = []

    # Long-or-cash: a sell reduces or closes, never crosses zero. Floating
    # residue in the plan's arithmetic can ask for a hair more than is held.
    if intent.side == "sell" and quantity > held:
        notes.append(f"clamped to the {held} shares actually held")
        quantity = held

    if not fractionable:
        truncated = quantity.quantize(Decimal("1"), rounding=ROUND_DOWN)
        if truncated != quantity:
            notes.append(
                f"{intent.symbol} is not fractionable at this broker; "
                f"rounded down {quantity} -> {truncated}, residual "
                f"{quantity - truncated} left untraded"
            )
        quantity = truncated

    # Selling all but a dust fraction of a non-fractionable position would
    # strand an untradeable remainder forever. If the intent is to close, close.
    closing_out = intent.side == "sell" and intent.target_weight == 0.0
    strands_dust = held > 0 and quantity > 0 and held - quantity < Decimal("1")
    if closing_out and strands_dust:
        if quantity != held:
            notes.append(f"closing the full {held}-share position rather than stranding dust")
        quantity = held

    # Last, so it applies to whatever the branches above settled on. The plan's
    # arithmetic carries full Decimal precision -- a target of 113.94095396357
    # 89972420494148 shares is an exact quotient, not a sendable order -- and
    # the broker rejects more than nine places outright rather than truncating
    # for us. Down, never nearest: rounding a buy up spends capital the sizing
    # did not allocate, and rounding a sell up asks to sell more than is held.
    return quantity.quantize(MAX_QUANTITY_DECIMALS, rounding=ROUND_DOWN), "; ".join(notes)


def submit_plan(  # noqa: PLR0912, PLR0915 - durable order transaction
    plan: ExecutionPlan,
    client: AlpacaPaperClient,
    *,
    account: Account,
    session: dt.date,
    now: dt.datetime,
    journal: Journal,
    dry_run: bool = False,
    limits: RiskLimits,
    current_positions: dict[str, Decimal] | None = None,
    submission_time: Callable[[], dt.datetime] | None = None,
    buy_limit_prices: dict[str, Decimal] | None = None,
) -> SubmissionResult:
    """Execute ``plan`` against the paper account, or refuse and record why.

    Args:
        plan: The parsed plan artifact.
        client: Paper broker client.
        account: Account state already fetched by the caller.
        session: Today's exchange session date.
        now: Aware instant used for the expiry check.
        journal: Append-only record.
        dry_run: Perform every check and every sizing decision, then stop
            short of the POST. Used to rehearse a deployment against a real
            account without touching it.
        limits: Risk limits the executor owns, independent of the plan.

    Returns:
        The outcome for every intent, including the ones deliberately withheld.
    """
    refusal = submission_interlock(journal.state_root) or _preflight(
        plan, account, session, now, limits=limits
    )
    if buy_limit_prices is not None and not refusal:
        buy_symbols = {intent.symbol for intent in plan.intents if intent.side == "buy"}
        if (
            set(buy_limit_prices) != buy_symbols
            or any(not price.is_finite() or price <= 0 for price in buy_limit_prices.values())
            or any(
                intent.side == "buy" and buy_limit_prices[intent.symbol] > intent.reference_price
                for intent in plan.intents
            )
        ):
            refusal = (
                "buy limits must be finite, positive, complete, and within checked reference prices"
            )
    if refusal:
        journal.record(
            "submission_refused",
            session=session,
            reason=refusal,
            as_of_session=plan.as_of_session,
            intent_count=len(plan.intents),
        )
        return SubmissionResult(
            session=session,
            executed=False,
            refusal_reason=refusal,
            account_equity_before=account.equity,
            dry_run=dry_run,
        )

    symbols = tuple(sorted({intent.symbol for intent in plan.intents}))
    try:
        fractionable = client.fractionable_symbols(symbols)
    except BrokerError as exc:
        # Not fatal, but it changes the sizing rule, so it is recorded loudly
        # and every symbol is treated as whole-share only. Rounding down on
        # missing information is the conservative direction.
        journal.record("fractionability_lookup_failed", session=session, detail=str(exc))
        fractionable = dict.fromkeys(symbols, False)

    held_now = dict(plan.current_positions if current_positions is None else current_positions)

    outcomes: list[SubmissionOutcome] = []
    orders: list[Order] = []

    for intent in plan.intents:
        quantity, note = _resolve_quantity(
            intent,
            fractionable=fractionable.get(intent.symbol, False),
            held=held_now.get(intent.symbol, Decimal("0")),
        )
        client_order_id = client_order_id_for(plan, intent)

        if quantity <= 0:
            outcomes.append(
                SubmissionOutcome(
                    symbol=intent.symbol,
                    side=intent.side,
                    intended_quantity=intent.quantity,
                    submitted_quantity=Decimal("0"),
                    status="withheld",
                    client_order_id=client_order_id,
                    detail=note or "resolved quantity was zero",
                )
            )
            journal.record(
                "intent_withheld",
                session=session,
                symbol=intent.symbol,
                side=intent.side,
                intended_quantity=intent.quantity,
                reason=note or "resolved quantity was zero",
            )
            continue

        if dry_run:
            outcomes.append(
                SubmissionOutcome(
                    symbol=intent.symbol,
                    side=intent.side,
                    intended_quantity=intent.quantity,
                    submitted_quantity=quantity,
                    status="dry_run",
                    client_order_id=client_order_id,
                    detail=note or "DRY_RUN: nothing was sent",
                )
            )
            journal.record(
                "intent_dry_run",
                session=session,
                symbol=intent.symbol,
                side=intent.side,
                quantity=quantity,
                client_order_id=client_order_id,
                note=note,
            )
            continue

        refusal = submission_interlock(journal.state_root)
        if submission_time is not None and plan.is_expired(now=submission_time()):
            refusal = "plan expired while preparing orders"
        if refusal:
            journal.record("submission_refused", session=session, reason=refusal)
            break
        receipt_path = journal.state_root / "submissions" / f"{client_order_id}.json"
        if receipt_path.exists():
            previous = read_receipt(receipt_path)
            outcomes.append(
                SubmissionOutcome(
                    symbol=intent.symbol,
                    side=intent.side,
                    intended_quantity=intent.quantity,
                    submitted_quantity=Decimal("0"),
                    status="already_submitted" if previous["status"] != "rejected" else "rejected",
                    client_order_id=client_order_id,
                    detail="durable receipt prevents repeating this POST",
                )
            )
            if previous["status"] == "rejected":
                refusal = "previous rejection prevents continuation of this plan"
                break
            continue
        receipt = {
            "status": "pending",
            "client_order_id": client_order_id,
            "symbol": intent.symbol,
            "side": intent.side,
            "quantity": str(quantity),
            "session": session.isoformat(),
            "config_sha256": plan.config_sha256,
        }
        # Write-ahead evidence survives a crash between broker acceptance and journaling.
        atomic_json(receipt_path, receipt)
        try:
            if buy_limit_prices is not None and intent.side == "buy":
                order = client.submit_limit_order(
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=quantity,
                    client_order_id=client_order_id,
                    limit_price=buy_limit_prices[intent.symbol],
                )
            else:
                order = client.submit_market_order(
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=quantity,
                    client_order_id=client_order_id,
                )
        except OrderOutcomeUnknownError as exc:
            atomic_json(receipt_path, {**receipt, "status": "unknown"})
            journal.record(
                "order_outcome_unknown",
                session=session,
                symbol=intent.symbol,
                client_order_id=client_order_id,
                detail=str(exc),
            )
            outcomes.append(
                SubmissionOutcome(
                    symbol=intent.symbol,
                    side=intent.side,
                    intended_quantity=intent.quantity,
                    submitted_quantity=Decimal("0"),
                    status="outcome_unknown",
                    client_order_id=client_order_id,
                    detail=str(exc),
                )
            )
            refusal = "order outcome unknown; further submissions halted pending reconciliation"
            break
        except BrokerError as exc:
            detail = str(exc)
            # A duplicate is the idempotency key doing its job, not a failure:
            # this plan's order for this symbol already exists at the broker.
            duplicate = "duplicate" in detail.lower()
            atomic_json(
                receipt_path, {**receipt, "status": "duplicate" if duplicate else "rejected"}
            )
            outcomes.append(
                SubmissionOutcome(
                    symbol=intent.symbol,
                    side=intent.side,
                    intended_quantity=intent.quantity,
                    submitted_quantity=Decimal("0"),
                    status="already_submitted" if duplicate else "rejected",
                    client_order_id=client_order_id,
                    detail=detail,
                )
            )
            journal.record(
                "order_rejected" if not duplicate else "order_duplicate",
                session=session,
                symbol=intent.symbol,
                side=intent.side,
                quantity=quantity,
                client_order_id=client_order_id,
                detail=detail,
            )
            if not duplicate:
                refusal = "broker rejected an order; remaining intents withheld"
                break
            continue

        atomic_json(receipt_path, {**receipt, "status": "submitted", "order_id": order.id})
        orders.append(order)
        outcomes.append(
            SubmissionOutcome(
                symbol=intent.symbol,
                side=intent.side,
                intended_quantity=intent.quantity,
                submitted_quantity=quantity,
                status="submitted",
                client_order_id=client_order_id,
                order_id=order.id,
                detail=note,
            )
        )
        journal.record(
            "order_submitted",
            session=session,
            symbol=intent.symbol,
            side=intent.side,
            quantity=quantity,
            client_order_id=client_order_id,
            order_id=order.id,
            broker_status=order.status,
            note=note,
        )

    return SubmissionResult(
        session=session,
        executed=True,
        refusal_reason=refusal,
        outcomes=tuple(outcomes),
        account_equity_before=account.equity,
        dry_run=dry_run,
        orders=tuple(orders),
    )
