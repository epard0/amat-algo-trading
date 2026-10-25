"""Versioned, durable sell-confirm-budget-buy execution of an unchanged plan.

One immutable authorization per session; at most one broker order per intent.
Partial or rejected orders retain their unspent authorization without automatic
replacement. Unknown POST outcomes can only be resolved by matching broker evidence.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, replace
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from executor.errors import ConfigurationError, OrderOutcomeUnknownError
from executor.safety import atomic_json, bind_account, submission_interlock
from executor.submit import (
    SubmissionResult,
    _preflight,
    _resolve_quantity,
    client_order_id_for,
    submit_plan,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from executor.broker import Account, AlpacaPaperClient, Order
    from executor.config import RiskLimits
    from executor.journal import Journal
    from executor.plan_source import ExecutionPlan, PlanIntent

SCHEMA = "execution-batch/1"
_TERMINAL = frozenset({"filled", "canceled", "expired", "rejected"})
_ZERO = Decimal(0)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def number(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError("nonfinite or negative")
        return result
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise ConfigurationError("invalid numeric execution contract field") from exc


@dataclass(frozen=True)
class BatchPolicy:
    """Unlevered, cash-funded limits; policy identity freezes every setting."""

    reserve_fraction: Decimal = Decimal("0.01")
    price_buffer_fraction: Decimal = Decimal("0.002")
    fee_fraction: Decimal = Decimal("0.0002")
    whole_share_buys: bool = False

    def __post_init__(self) -> None:
        if type(self.whole_share_buys) is not bool:
            raise ConfigurationError("whole_share_buys must be boolean")
        for value in (self.reserve_fraction, self.price_buffer_fraction, self.fee_fraction):
            if not value.is_finite() or not 0 <= value < 1:
                raise ConfigurationError("batch policy fractions must be finite in [0, 1)")

    def payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema": "confirmed-cash-policy/1",
            "reserve_fraction": str(self.reserve_fraction),
            "price_buffer_fraction": str(self.price_buffer_fraction),
            "fee_fraction": str(self.fee_fraction),
            "buy_order_type": "limit_day",
            "replacement_policy": "none; residual authorization is reported only",
        }
        if self.whole_share_buys:
            value["whole_share_buys"] = True
            value["partial_sell_quantity_quantum"] = "1"
            value["full_exit_quantity_policy"] = "preserve_fractional_entitlement"
        return value

    @property
    def identity(self) -> str:
        return digest(self.payload())


def save_batch(path: Path, body: dict[str, Any]) -> None:
    body = {k: v for k, v in body.items() if k != "sha256"}
    atomic_json(path, {**body, "sha256": digest(body)})


def load_batch(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
        body = {k: v for k, v in payload.items() if k != "sha256"}
        if body.get("schema") != SCHEMA or digest(body) != payload.get("sha256"):
            raise ValueError("unsupported or changed batch")
        for row in body["intents"]:
            authorized = number(row["original_authorized_quantity"])
            filled = number(row["cumulative_filled_quantity"])
            remaining = number(row["remaining_intent_quantity"])
            if authorized <= 0 or filled + remaining != authorized:
                raise ValueError("inconsistent cumulative authorization")
            order = row.get("order")
            if order and (
                number(order["quantity"]) > authorized or filled > number(order["quantity"])
            ):
                raise ValueError("order exceeds authorization")
        number(body["pending_cash_commitments"])
        return body
    except (OSError, KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ConfigurationError("execution batch unreadable or inconsistent") from exc


def _observe(row: dict[str, Any], order: Order) -> None:
    submitted = row["order"]
    quantity = number(submitted["quantity"])
    if (
        not order.id
        or not order.status
        or order.client_order_id != submitted["client_order_id"]
        or order.symbol != row["symbol"]
        or order.side != row["side"]
        or order.quantity != quantity
        or (submitted.get("order_id") and submitted["order_id"] != order.id)
        or not number(row["cumulative_filled_quantity"])
        <= number(order.filled_quantity)
        <= quantity
        or (order.status == "filled" and order.filled_quantity != quantity)
        or order.status == "replaced"
    ):
        raise ConfigurationError("broker order identity or cumulative fill mismatch")
    if row["side"] == "buy":
        if order.raw.get("type") != "limit" or number(order.raw.get("limit_price")) != number(
            submitted["limit_price"]
        ):
            raise ConfigurationError("broker buy is not the authorized price-bounded order")
        if order.filled_quantity and (
            order.filled_avg_price is None
            or not order.filled_avg_price.is_finite()
            or not 0 < order.filled_avg_price <= number(submitted["limit_price"])
        ):
            raise ConfigurationError("buy fill price exceeds its cash authorization")
    if submitted["status"] == "filled" and order.status != "filled":
        raise ConfigurationError("filled order cannot reopen")
    submitted.update(order_id=order.id, status=order.status)
    row["cumulative_filled_quantity"] = str(order.filled_quantity)
    row["remaining_intent_quantity"] = str(
        number(row["original_authorized_quantity"]) - order.filled_quantity
    )


def _commitments(state: dict[str, Any], policy: BatchPolicy) -> Decimal:
    return sum(
        (
            (number(row["order"]["quantity"]) - number(row["cumulative_filled_quantity"]))
            * number(row["order"]["limit_price"])
            * (1 + policy.fee_fraction)
            for row in state["intents"]
            if row["side"] == "buy" and row.get("order") and row["order"]["status"] not in _TERMINAL
        ),
        _ZERO,
    )


def _effective(plan: ExecutionPlan, row: dict[str, Any]) -> PlanIntent:
    intent = next(i for i in plan.intents if i.symbol == row["symbol"] and i.side == row["side"])
    quantity = number(row["order"]["quantity"])
    price = number(row["order"].get("limit_price", row["reference_price"]))
    return replace(intent, quantity=quantity, reference_price=price, notional=quantity * price)


def _initialize(
    plan: ExecutionPlan, account: Account, policy: BatchPolicy, session: dt.date
) -> dict[str, Any]:
    seen: set[str] = set()
    intents = []
    for intent in plan.intents:
        if intent.symbol in seen:
            raise ConfigurationError("a staged plan may authorize only one direction per symbol")
        seen.add(intent.symbol)
        intents.append(
            {
                "intent_id": client_order_id_for(plan, intent),
                "symbol": intent.symbol,
                "side": intent.side,
                "original_authorized_quantity": str(intent.quantity),
                "reference_price": str(intent.reference_price),
                "cumulative_filled_quantity": "0",
                "remaining_intent_quantity": str(intent.quantity),
                "order": None,
            }
        )
    account_hash = digest(account.account_number)
    identity = {
        "plan_sha256": digest(plan.raw),
        "account_id": account_hash,
        "policy_id": policy.identity,
        "execution_session": str(session),
    }
    return {
        "schema": SCHEMA,
        **identity,
        "batch_id": digest(identity),
        "config_sha256": plan.config_sha256,
        "expires_at": plan.expires_at.isoformat(),
        "submission_expires_at": min(
            plan.expires_at,
            dt.datetime.combine(session, plan.window_end_local, ZoneInfo(plan.exchange_timezone)),
        ).isoformat(),
        "policy": policy.payload(),
        "status": "selling",
        "intents": intents,
        "pending_cash_commitments": "0",
        "cash_observation": None,
    }


@dataclass(frozen=True)
class BatchResult:
    status: str
    pending: bool
    state: dict[str, Any]
    submission: SubmissionResult


def advance_batch(  # noqa: PLR0911, PLR0912, PLR0915 - explicit durable phase transitions
    plan: ExecutionPlan,
    client: AlpacaPaperClient,
    *,
    session: dt.date,
    now: dt.datetime,
    journal: Journal,
    limits: RiskLimits,
    policy: BatchPolicy | None = None,
    dry_run: bool = False,
    submission_time: Callable[[], dt.datetime] | None = None,
) -> BatchResult:
    """Advance once under the caller's submission lock; never wait for a fill.

    Each call reconciles broker cumulative fills before using refreshed actual
    cash. A nonterminal sale defers the entire buy stage. Terminal partial sales
    allow only the cash already observed. Buy budgets are frozen once, and every
    later call reuses the same original plan and deterministic submission IDs.
    """
    policy = policy or BatchPolicy()
    account = client.get_account()
    bind_account(journal.state_root, account.account_number)
    path = journal.state_root / "batches" / f"{session}.json"
    state = load_batch(path) if path.exists() else _initialize(plan, account, policy, session)
    expected = _initialize(plan, account, policy, session)
    for key in (
        "plan_sha256",
        "account_id",
        "policy_id",
        "batch_id",
        "expires_at",
        "submission_expires_at",
        "config_sha256",
        "execution_session",
        "policy",
    ):
        if state.get(key) != expected[key]:
            raise ConfigurationError(f"execution batch {key} mismatch")
    immutable = ("intent_id", "symbol", "side", "original_authorized_quantity", "reference_price")
    if len(state["intents"]) != len(expected["intents"]) or any(
        any(actual.get(key) != frozen[key] for key in immutable)
        for actual, frozen in zip(state["intents"], expected["intents"], strict=True)
    ):
        raise ConfigurationError("original batch authorization changed")
    if now.tzinfo is None or now.astimezone(ZoneInfo(plan.exchange_timezone)).date() != session:
        raise ConfigurationError("execution batch clock or session mismatch")
    submission = SubmissionResult(
        session=session,
        executed=False,
        refusal_reason="",
        account_equity_before=account.equity,
        dry_run=dry_run,
    )
    # Every known order is reconciled, including late fills after cancellation/expiry.
    for row in state["intents"]:
        if not row.get("order"):
            continue
        order = client.get_order_by_client_id(row["order"]["client_order_id"])
        if order is None:
            state["status"] = "unknown"
            if not dry_run:
                save_batch(path, state)
            return BatchResult(
                "unknown",
                True,
                state,
                replace(
                    submission,
                    refusal_reason="batch order outcome unknown; broker confirmation required",
                ),
            )
        _observe(row, order)
        receipt = journal.state_root / "submissions" / f"{order.client_order_id}.json"
        if receipt.exists():
            from executor.safety import read_receipt  # noqa: PLC0415

            payload = read_receipt(receipt)
            if (payload.get("symbol"), payload.get("side"), number(payload.get("quantity"))) != (
                order.symbol,
                order.side,
                order.quantity,
            ):
                raise ConfigurationError("batch receipt identity mismatch")
            if not dry_run and payload["status"] in {"pending", "unknown"}:
                atomic_json(receipt, {**payload, "status": "submitted", "order_id": order.id})
    state["pending_cash_commitments"] = str(_commitments(state, policy))
    cutoff = dt.datetime.fromisoformat(state["submission_expires_at"])
    if now >= cutoff:
        for row in state["intents"]:
            order = row.get("order")
            if not order or order["status"] in _TERMINAL or order.get("cancel_requested_at"):
                continue
            order["cancel_requested_at"] = now.isoformat()
            if not dry_run:
                save_batch(path, state)
                try:
                    client.cancel_order(order["order_id"])
                except OrderOutcomeUnknownError:
                    order["cancel_outcome"] = "unknown; observe broker state"
        pending = any(
            r.get("order") and r["order"]["status"] not in _TERMINAL for r in state["intents"]
        )
        state["status"] = "expired_pending_cancellation" if pending else "expired"
        if not dry_run:
            save_batch(path, state)
        return BatchResult(
            state["status"],
            pending,
            state,
            replace(
                submission,
                refusal_reason="execution batch expired; no remaining quantity may be submitted",
            ),
        )
    refusal = submission_interlock(journal.state_root) or _preflight(
        plan, account, session, now, limits=limits
    )
    if refusal:
        return BatchResult(
            "blocked",
            any(
                row.get("order") and row["order"]["status"] not in _TERMINAL
                for row in state["intents"]
            ),
            state,
            replace(submission, refusal_reason=refusal),
        )
    if not path.exists() and any(
        (journal.state_root / "submissions").glob(f"amat-{plan.as_of_session}-*.json")
    ):
        raise ConfigurationError(
            "existing session receipts prevent creating a new staged authorization"
        )
    known = {r["order"]["client_order_id"] for r in state["intents"] if r.get("order")}
    if any(o.client_order_id not in known for o in client.list_orders(status="open")):
        raise ConfigurationError("open orders outside the execution batch block cash authorization")
    if dry_run:
        return BatchResult("dry_run", False, state, replace(submission, dry_run=True))
    save_batch(path, state)
    fractionable = client.fractionable_symbols(tuple(r["symbol"] for r in state["intents"]))
    held = client.position_quantities()
    for side in ("sell", "buy"):
        candidates = [r for r in state["intents"] if r["side"] == side]
        if side == "buy":
            if any(
                r.get("order") and r["order"]["status"] not in _TERMINAL
                for r in state["intents"]
                if r["side"] == "sell"
            ):
                state["status"] = "waiting_for_sales"
                save_batch(path, state)
                return BatchResult(state["status"], True, state, submission)
            if state["cash_observation"] is None:
                account = client.get_account()  # confirmed cash, never estimated proceeds
                if not account.is_tradable or not account.account_number:
                    raise ConfigurationError("account changed before buy budgeting")
                if digest(account.account_number) != state["account_id"]:
                    raise ConfigurationError("batch account changed")
                available = max(
                    _ZERO,
                    min(
                        number(account.cash) - _commitments(state, policy),
                        number(account.buying_power),
                    )
                    - number(account.equity) * policy.reserve_fraction,
                )
                quotes: dict[str, Decimal] = {}
                quantities: dict[str, Decimal] = {}
                for row in candidates:
                    price = number(row["reference_price"]) * (1 + policy.price_buffer_fraction)
                    tick = Decimal("0.01") if price >= 1 else Decimal("0.0001")
                    quotes[row["symbol"]] = (price / tick).to_integral_value(
                        rounding=ROUND_DOWN
                    ) * tick
                    quantities[row["symbol"]] = number(row["original_authorized_quantity"])
                required = sum(
                    (q * quotes[s] * (1 + policy.fee_fraction) for s, q in quantities.items()),
                    _ZERO,
                )
                scale = min(Decimal(1), available / required) if required else _ZERO
                if limits.max_session_notional is not None:
                    used = sum(
                        (
                            number(row["order"]["quantity"])
                            * number(row["order"].get("limit_price", row["reference_price"]))
                            for row in state["intents"]
                            if row.get("order")
                        ),
                        _ZERO,
                    )
                    remaining_cap = max(_ZERO, limits.max_session_notional - used)
                    notional_required = sum((q * quotes[s] for s, q in quantities.items()), _ZERO)
                    if notional_required:
                        scale = min(scale, remaining_cap / notional_required)
                for row in candidates:
                    quantity = quantities[row["symbol"]] * scale
                    if limits.max_symbol_notional is not None:
                        quantity = min(quantity, limits.max_symbol_notional / quotes[row["symbol"]])
                    quantum = (
                        Decimal("0.000000001")
                        if fractionable.get(row["symbol"]) and not policy.whole_share_buys
                        else Decimal(1)
                    )
                    quantity = (quantity / quantum).to_integral_value(rounding=ROUND_DOWN) * quantum
                    row["budgeted_quantity"] = str(quantity)
                    row["limit_price"] = str(quotes[row["symbol"]])
                state["cash_observation"] = {
                    "observed_at": now.isoformat(),
                    "confirmed_cash": str(account.cash),
                    "buying_power": str(account.buying_power),
                    "available_budget": str(available),
                    "equity": str(account.equity),
                    "pending_cash_commitments": str(_commitments(state, policy)),
                }
                state["status"] = "buying"
                save_batch(path, state)
        for row in candidates:
            if row.get("order") or row.get("withheld"):
                continue
            intent = next(i for i in plan.intents if i.symbol == row["symbol"])
            quantity = (
                number(row["budgeted_quantity"])
                if side == "buy"
                else _resolve_quantity(
                    intent,
                    fractionable=fractionable.get(row["symbol"], False),
                    held=held.get(row["symbol"], _ZERO),
                )[0]
            )
            # Native whole-share research rounds partial rebalances, while a
            # full exit preserves fractional split/legacy entitlements. Keep
            # the original authorization and any unsent residual in the batch.
            if side == "sell" and policy.whole_share_buys and intent.target_weight > 0:
                quantity = quantity.to_integral_value(rounding=ROUND_DOWN)
            if side == "buy":
                fresh = client.get_account()
                if digest(fresh.account_number) != state["account_id"] or not fresh.is_tradable:
                    raise ConfigurationError("batch account changed before buy submission")
                remaining_cash = max(
                    _ZERO,
                    min(
                        number(fresh.cash) - _commitments(state, policy), number(fresh.buying_power)
                    )
                    - number(fresh.equity) * policy.reserve_fraction,
                )
                unit_cost = number(row["limit_price"]) * (1 + policy.fee_fraction)
                quantum = (
                    Decimal("0.000000001")
                    if fractionable.get(row["symbol"]) and not policy.whole_share_buys
                    else Decimal(1)
                )
                quantity = min(
                    quantity,
                    (remaining_cash / unit_cost / quantum).to_integral_value(rounding=ROUND_DOWN)
                    * quantum,
                )
            if quantity <= 0:
                row["withheld"] = "cash budget or available shares leave no sendable quantity"
                save_batch(path, state)
                continue
            row["order"] = {
                "quantity": str(quantity),
                "client_order_id": "",
                "status": "pending",
                "order_id": "",
            }
            if side == "buy":
                row["order"]["limit_price"] = row["limit_price"]
            effective = _effective(plan, row)
            row["order"]["client_order_id"] = client_order_id_for(plan, effective)
            # Commit budget/identity before the native write-ahead receipt and POST.
            state["pending_cash_commitments"] = str(_commitments(state, policy))
            save_batch(path, state)
            moment = submission_time() if submission_time else now
            if moment >= cutoff:
                row["order"] = None  # no native submission has been attempted
                state["pending_cash_commitments"] = str(_commitments(state, policy))
                state["status"] = "expired"
                save_batch(path, state)
                return BatchResult(
                    "expired",
                    False,
                    state,
                    replace(submission, refusal_reason="batch expired before submission"),
                )
            result = submit_plan(
                replace(plan, intents=(effective,), expires_at=cutoff),
                client,
                account=client.get_account(),
                session=session,
                now=moment,
                journal=journal,
                limits=limits,
                current_positions=client.position_quantities(),
                submission_time=submission_time,
                buy_limit_prices={row["symbol"]: number(row["limit_price"])}
                if side == "buy"
                else None,
            )
            submission = replace(
                result,
                outcomes=(*submission.outcomes, *result.outcomes),
                orders=(*submission.orders, *result.orders),
            )
            if result.orders:
                _observe(row, result.orders[0])
            else:
                if not result.outcomes and result.refusal_reason:
                    row["order"] = None  # explicit native preflight refusal, before any POST
                    row["withheld"] = result.refusal_reason
                    state["pending_cash_commitments"] = str(_commitments(state, policy))
                    state["status"] = "blocked"
                    save_batch(path, state)
                    return BatchResult("blocked", False, state, submission)
                state["status"] = (
                    "unknown"
                    if result.refusal_reason
                    or any(o.status == "outcome_unknown" for o in result.outcomes)
                    else "blocked"
                )
                save_batch(path, state)
                return BatchResult(state["status"], state["status"] == "unknown", state, submission)
            state["pending_cash_commitments"] = str(_commitments(state, policy))
            save_batch(path, state)
    pending = any(
        r.get("order") and r["order"]["status"] not in _TERMINAL for r in state["intents"]
    )
    state["status"] = (
        "waiting_for_fills"
        if pending
        else (
            "complete_with_residual"
            if any(number(r["remaining_intent_quantity"]) > 0 for r in state["intents"])
            else "complete"
        )
    )
    state["pending_cash_commitments"] = str(_commitments(state, policy))
    save_batch(path, state)
    journal.record(
        "execution_batch_observed",
        session=session,
        batch_id=state["batch_id"],
        status=state["status"],
        pending_cash_commitments=state["pending_cash_commitments"],
    )
    return BatchResult(state["status"], pending, state, submission)
