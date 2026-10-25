"""Inert causal DAY-limit research reducer and isolated durable ownership.

No broker client, transport, daemon registration or production-state discovery.
Commands are review data. A caller must not connect them to paper/live execution.
Expiry is a submission/cancel-request boundary, never a promise of fill finality.
"""

from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from executor.safety import atomic_json

SCHEMA = "research-causal-day-limit/1"
ZERO = Decimal(0)
BPS = Decimal(10000)
ZONE = ZoneInfo("America/New_York")
TERMINAL = {"filled", "canceled", "expired", "rejected"}
MAX_ACTIONS = 16
MAX_QUOTE_AGE = 2
MAX_PREFIX_DELAY = 5
STATUSES = TERMINAL | {"accepted", "new", "partially_filled", "pending_cancel", "unknown"}
RULES = {
    "trigger": "one_fresh_executable_quote_after_completed_prefix",
    "prefix_seconds": 60,
    "max_quote_age_seconds": 2,
    "max_prefix_delay_seconds": 5,
    "entry_participation": "0.01",
    "opening_window_seconds": 300,
    "time_in_force": "day",
    "expiry": "stop_submission_request_cancel_keep_commitments_until_terminal",
    "fill": "broker_observation_only_no_vwap_or_queue_inference",
    "retry": "never_automatically_resubmit_original_intent",
    "corporate_actions": "explicit_empty_coverage_required",
}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def number(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Exact decimal string required")
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid nonnegative decimal")
    return result


def instant(value: str) -> dt.datetime:
    result = dt.datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() != dt.timedelta(0):
        raise ValueError("UTC instant required")
    return result


def fee(notional: Decimal, bps: Decimal) -> Decimal:
    return (notional * bps / BPS).quantize(Decimal("0.000001"))


def wire_decimal(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def validate_contract(plan: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
    """Validate a compiler-produced research artifact; no execution authority."""
    expected_keys = {
        "schema",
        "engine",
        "research_only",
        "execution_authority",
        "promotion_authority",
        "required_capabilities",
        "rules",
        "original_plan",
        "policy_id",
        "contract_sha256",
    }
    if (
        set(plan) != expected_keys
        or plan["schema"] != SCHEMA
        or plan["engine"] != "research-policy/1"
        or plan["research_only"] is not True
        or plan["execution_authority"] != "none"
        or plan["promotion_authority"] is not False
        or plan["required_capabilities"] != [SCHEMA]
        or digest(plan["rules"]) != digest(RULES)
        or plan["contract_sha256"]
        != digest({k: v for k, v in plan.items() if k != "contract_sha256"})
    ):
        raise ValueError("Unknown or altered research contract")
    original = plan["original_plan"]
    ctx: dict[str, Any] = original["context"]
    proposal = original["proposal"]
    if original["schema"] != "research-native-opening-attempt/1":
        raise ValueError("Unknown original policy")
    if (
        original["execution_authority"] != "none"
        or original["promotion_authority"] is not False
        or proposal["execution_authority"] != "none"
        or proposal["research_only"] is not True
        or proposal["schema"] != "research-agent-portfolio-action/1"
    ):
        raise ValueError("Original artifact cannot grant execution authority")
    if (
        digest(ctx) != original["context_sha256"]
        or original["context_sha256"] != proposal["evidence_sha256"]
        or original["plan_id"]
        != digest(
            {"policy": original["schema"], "proposal": proposal, "context_sha256": digest(ctx)}
        )
        or proposal["policy_id"] != digest(ctx["policy"])
        or proposal["account_id"] != ctx["account_id"]
        or proposal["authorized_intents_sha256"] != digest(ctx["authorized_intents"])
        or not ctx["account_id"]
        or ctx["execution_clock"] != "next_session_open"
        or any(
            ctx["policy"][k] is not False
            for k in ("allow_direct_short", "allow_inverse", "allow_intraday")
        )
    ):
        raise ValueError("Original account/policy/intent identity differs")
    for clock in ("decision_at", "execution_at", "expires_at"):
        if ctx[clock] != proposal[clock]:
            raise ValueError("Original clock changed")
        instant(ctx[clock])
    if not instant(ctx["decision_at"]) < instant(ctx["execution_at"]) < instant(ctx["expires_at"]):
        raise ValueError("Invalid original timing")
    if (
        not 0
        <= (instant(ctx["decision_at"]) - instant(ctx["account_observed_at"])).total_seconds()
        <= ctx["policy"]["max_evidence_age_seconds"]
    ):
        raise ValueError("Original account cutoff is invalid")
    if instant(ctx["execution_at"]).astimezone(ZONE).time().replace(tzinfo=None) != dt.time(9, 30):
        raise ValueError("Opening policy only")
    bps = number(ctx["policy"]["one_way_bps"])
    if bps not in {Decimal(2), Decimal(6), Decimal(10)}:
        raise ValueError("Research cost ladder must be 2/6/10bps")
    if plan["policy_id"] != digest({"schema": SCHEMA, "rules": RULES, "cost_bps": str(bps)}):
        raise ValueError("Execution policy identity differs")
    positions = {s: number(q) for s, q in ctx["positions"]}
    marks = {r["symbol"]: r for r in ctx["evidence"]}
    if len(positions) != len(ctx["positions"]) or len(marks) != len(ctx["evidence"]):
        raise ValueError("Duplicate context symbols")
    if any(r["instrument"] != "ordinary_etf" for r in marks.values()):
        raise ValueError("Unsupported instrument")
    for row in marks.values():
        if (
            not row["evidence_id"]
            or number(row["mark"]) <= 0
            or not 0
            <= (instant(ctx["decision_at"]) - instant(row["available_at"])).total_seconds()
            <= ctx["policy"]["max_evidence_age_seconds"]
        ):
            raise ValueError("Original market cutoff is invalid")
    cash, equity = number(ctx["confirmed_cash"]), number(ctx["equity"])
    if (
        equity <= 0
        or cash + sum((q * number(marks[s]["mark"]) for s, q in positions.items()), ZERO) != equity
    ):
        raise ValueError("Original ledger does not reconcile")
    if number(ctx["restricted_cash"]) > cash:
        raise ValueError("Restricted cash exceeds confirmed cash")
    for key in ("max_gross", "max_abs_net", "max_symbol", "cash_reserve_fraction"):
        if not ZERO <= number(ctx["policy"][key]) <= 1:
            raise ValueError("Unsupported risk limits")
    locked = set()
    ids = set()
    for row in ctx["authorized_intents"]:
        if (
            row["intent_id"] in ids
            or not row["intent_id"]
            or row["account_id"] != ctx["account_id"]
            or row["policy_id"] != proposal["policy_id"]
        ):
            raise ValueError("Original intent identity changed")
        ids.add(row["intent_id"])
        if number(row["original_quantity"]) != number(row["cumulative_fills"]) + number(
            row["remaining_quantity"]
        ):
            raise ValueError("Original intent quantities differ")
        number(row["pending_cash"])
        instant(row["expires_at"])
        if number(row["remaining_quantity"]) > 0:
            locked.add(row["symbol"])
    seen = set()
    actions = proposal["actions"]
    if not isinstance(actions, list) or not actions or len(actions) > MAX_ACTIONS:
        raise ValueError("Invalid action count")
    for a in actions:
        if a["kind"] == "hold":
            if len(actions) != 1 or set(a) != {"kind", "reason"}:
                raise ValueError("Hold must be exclusive")
            continue
        if a["kind"] not in {"resize", "open", "close", "stop_loss", "take_profit"}:
            raise ValueError("Unsupported action")
        keys = {"kind", "reason", "symbol", "side", "quantity", "limit_price", "evidence_id"}
        if a["kind"] in {"stop_loss", "take_profit"}:
            keys.add("trigger_price")
        if set(a) != keys:
            raise ValueError("Unknown action fields")
        s = a["symbol"]
        q, limit = number(a["quantity"]), number(a["limit_price"])
        held = positions.get(s, ZERO)
        if (a["kind"] == "open" and held != 0) or (a["kind"] == "resize" and held == 0):
            raise ValueError("Action kind differs from actual position")
        if (
            s in seen
            or s in locked
            or s not in marks
            or a["side"] not in {"buy", "sell"}
            or q <= 0
            or limit <= 0
        ):
            raise ValueError("Invalid or outstanding symbol authority")
        seen.add(s)
        if a["evidence_id"] != marks[s]["evidence_id"]:
            raise ValueError("Action evidence changed")
        if a["side"] == "sell" and q > held:
            raise ValueError("Unbacked sale")
        if a["side"] == "buy" and (
            locked or q != q.to_integral_value() or q > number(marks[s]["entry_quantity_cap"])
        ):
            raise ValueError("New risk conflicts with original intent/capacity")
        if a["kind"] in {"close", "stop_loss", "take_profit"} and a["side"] != "sell":
            raise ValueError("Protective instruction must reduce a held long")
        if a["kind"] == "close" and q != held:
            raise ValueError("Close quantity differs")
        if a["kind"] in {"stop_loss", "take_profit"}:
            trigger, mark = number(a["trigger_price"]), number(marks[s]["mark"])
            if (
                trigger <= 0
                or limit > trigger
                or (a["kind"] == "stop_loss" and trigger >= mark)
                or (a["kind"] == "take_profit" and trigger <= mark)
            ):
                raise ValueError("Protective trigger/limit differs")
    return ctx


def _pending(state: dict[str, Any]) -> Decimal:
    ctx = state["contract"]["original_plan"]["context"]
    bps = number(ctx["policy"]["one_way_bps"])
    return sum((number(i["pending_cash"]) for i in ctx["authorized_intents"]), ZERO) + sum(
        (
            (number(r["submitted_quantity"]) - number(r["cumulative_fills"]))
            * number(r["limit_price"])
            * (1 + bps / BPS)
            for r in state["orders"]
            if r["side"] == "buy" and (r["status"] not in TERMINAL or state["quarantine"])
        ),
        ZERO,
    )


def _seal_state(state: dict[str, Any]) -> dict[str, Any]:
    state["pending_cash"] = str(_pending(state))
    state["state_sha256"] = digest({k: v for k, v in state.items() if k != "state_sha256"})
    return state


def initial_state(contract: dict[str, Any]) -> dict[str, Any]:
    ctx = validate_contract(contract)
    return _seal_state(
        {
            "schema": SCHEMA + "/state",
            "contract": copy.deepcopy(contract),
            "attempted": False,
            "orders": [],
            "fills": [],
            "events": {},
            "event_log": [],
            "quarantine": None,
            "cash": ctx["confirmed_cash"],
            "positions": dict(ctx["positions"]),
            "modeled_fees": "0",
            "execution_authority": "none",
            "original_authorized_intents": copy.deepcopy(ctx["authorized_intents"]),
        }
    )


def verify_state(state: dict[str, Any], contract: dict[str, Any]) -> None:
    ctx = validate_contract(contract)
    if state["contract"] != contract or state["state_sha256"] != digest(
        {k: v for k, v in state.items() if k != "state_sha256"}
    ):
        raise ValueError("State source/identity changed")
    if state["original_authorized_intents"] != ctx["authorized_intents"]:
        raise ValueError("Original commitments changed")
    cash = number(ctx["confirmed_cash"])
    positions = {s: number(q) for s, q in ctx["positions"]}
    fees = ZERO
    for fill in state["fills"]:
        q, price, cost = (number(fill[k]) for k in ("quantity", "price", "cost_cash"))
        sign = 1 if fill["side"] == "buy" else -1
        positions[fill["symbol"]] = positions.get(fill["symbol"], ZERO) + sign * q
        cash -= sign * q * price + cost
        fees += cost
    if (
        cash != number(state["cash"])
        or fees != number(state["modeled_fees"])
        or positions != {s: number(q) for s, q in state["positions"].items()}
    ):
        raise ValueError("State accounting changed")
    for row in state["orders"]:
        action = contract["original_plan"]["proposal"]["actions"][row["action_index"]]
        original = number(action["quantity"])
        expected_cid = (
            "rday-"
            + digest({"contract": contract["contract_sha256"], "action": row["action_index"]})[:40]
        )
        if (
            row["client_order_id"] != expected_cid
            or row["original_quantity"] != action["quantity"]
            or row["limit_price"] != action["limit_price"]
            or row["symbol"] != action["symbol"]
            or row["side"] != action["side"]
            or row["expires_at"] != ctx["expires_at"]
        ):
            raise ValueError("State original order identity changed")
        if row["wire"] != {
            "symbol": row["symbol"],
            "side": row["side"],
            "qty": wire_decimal(number(row["submitted_quantity"])),
            "limit_price": wire_decimal(number(row["limit_price"])),
            "type": "limit",
            "time_in_force": "day",
            "client_order_id": expected_cid,
        }:
            raise ValueError("State wire instruction changed")
        cumulative = sum(
            (
                number(f["quantity"])
                for f in state["fills"]
                if f["client_order_id"] == row["client_order_id"]
            ),
            ZERO,
        )
        if (
            not ZERO <= cumulative <= number(row["submitted_quantity"]) <= original
            or cumulative != number(row["cumulative_fills"])
            or number(row["remaining_quantity"]) != original - cumulative
        ):
            raise ValueError("State cumulative authorization changed")
    if number(state["pending_cash"]) != _pending(state):
        raise ValueError("Pending commitments changed")


def _observation(ctx: dict[str, Any], packet: dict[str, Any], now: dt.datetime) -> None:
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise ValueError("UTC admission time required")
    start = instant(ctx["execution_at"])
    prefix_end = start + dt.timedelta(seconds=60)
    if not prefix_end <= now < min(instant(ctx["expires_at"]), start + dt.timedelta(seconds=300)):
        raise ValueError("Outside causal admission/submission cutoff")
    account = packet["account"]
    if (
        account["account_id"] != ctx["account_id"]
        or number(account["confirmed_cash"]) != number(ctx["confirmed_cash"])
        or {s: number(q) for s, q in account["positions"].items()}
        != {s: number(q) for s, q in ctx["positions"]}
        or not account["source_id"]
        or account["trading_allowed"] is not True
    ):
        raise ValueError("Fresh account differs from original authorization")
    if not 0 <= (now - instant(account["observed_at"])).total_seconds() <= MAX_QUOTE_AGE:
        raise ValueError("Stale account")
    cal = packet["calendar"]
    if (
        not cal["source_id"]
        or instant(cal["available_at"]) > now
        or instant(cal["open"]) != start
        or instant(cal["close"]) <= now
        or cal["session_date"] != str(start.astimezone(ZONE).date())
    ):
        raise ValueError("Unproved exchange session")
    coverage = packet["corporate_actions"]
    if (
        not coverage["source_id"]
        or coverage["actions"] != []
        or coverage["actions_sha256"] != digest([])
        or dt.date.fromisoformat(coverage["from_session"]) > instant(ctx["decision_at"]).date()
        or dt.date.fromisoformat(coverage["to_session"]) < start.date()
    ):
        raise ValueError("Missing/nonempty corporate-action coverage")
    required = {s for s, q in ctx["positions"] if number(q) > 0} | {
        a["symbol"] for a in packet["actions"] if a["kind"] != "hold"
    }
    for s in required:
        row = packet["symbols"][s]
        if (
            row["source_id"] != row["quote_source_id"]
            or not row["source_id"]
            or row["price_basis"] != "raw"
            or row["volume_unit"] != "shares"
            or row["tradable"] is not True
        ):
            raise ValueError("Unknown source/units/halt status")
        qtime, available = instant(row["quote_time"]), instant(row["quote_available_at"])
        if (
            not prefix_end <= qtime <= available <= now
            or (now - qtime).total_seconds() > MAX_QUOTE_AGE
        ):
            raise ValueError("Stale/future quote")
        if (
            instant(row["prefix_start"]) != start
            or instant(row["prefix_end"]) != prefix_end
            or not prefix_end <= instant(row["prefix_available_at"]) <= qtime
            or (now - prefix_end).total_seconds() > MAX_PREFIX_DELAY
        ):
            raise ValueError("Incomplete/future/old prefix volume")
        bid, ask = number(row["bid"]), number(row["ask"])
        if not ZERO < bid <= ask or row["prefix_complete"] is not True:
            raise ValueError("Invalid quote or incomplete prefix")
        number(row["prefix_volume"])


def admit(  # noqa: PLR0912, PLR0915 -- explicit causal admission and reservations
    state: dict[str, Any], observation: dict[str, Any], now: dt.datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Consume one attempt, reserve all commitments before returning inert commands."""
    state = copy.deepcopy(state)
    ctx = validate_contract(state["contract"])
    verify_state(state, state["contract"])
    if state["attempted"] or state["quarantine"]:
        return state, []
    state["attempted"] = True
    actions = state["contract"]["original_plan"]["proposal"]["actions"]
    packet = {**observation, "actions": actions}
    try:
        _observation(ctx, packet, now)
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        state["admission_block"] = type(exc).__name__ + ": " + str(exc)
        return _seal_state(state), []
    bps = number(ctx["policy"]["one_way_bps"])
    spendable = max(
        ZERO,
        number(ctx["confirmed_cash"])
        - number(ctx["restricted_cash"])
        - _pending(state)
        - number(ctx["equity"]) * number(ctx["policy"]["cash_reserve_fraction"]),
    )
    positions = {s: number(q) for s, q in ctx["positions"]}
    risk = {s: q * number(packet["symbols"][s]["bid"]) for s, q in positions.items() if q}
    nav = min(number(ctx["equity"]), number(ctx["confirmed_cash"]) + sum(risk.values(), ZERO))
    commands = []
    for index, action in enumerate(actions):
        if action["kind"] == "hold":
            continue
        s, side = action["symbol"], action["side"]
        q, limit = number(action["quantity"]), number(action["limit_price"])
        quote = packet["symbols"][s]
        bid, ask = number(quote["bid"]), number(quote["ask"])
        eligible = (side == "buy" and ask <= limit) or (side == "sell" and bid >= limit)
        if action["kind"] == "stop_loss":
            eligible &= bid <= number(action["trigger_price"])
        if action["kind"] == "take_profit":
            eligible &= bid >= number(action["trigger_price"])
        quantum = Decimal("0.01") if limit >= 1 else Decimal("0.0001")
        if limit % quantum or q != q.quantize(Decimal("0.000000001")):
            eligible = False
        if side == "buy":
            q = min(
                q,
                (number(quote["prefix_volume"]) * Decimal("0.01")).to_integral_value(
                    rounding=ROUND_DOWN
                ),
                (spendable / (limit * (1 + bps / BPS))).to_integral_value(rounding=ROUND_DOWN),
            )
        elif q != positions.get(s, ZERO):
            q = q.to_integral_value(rounding=ROUND_DOWN)
        if side == "buy" and q > 0:
            projected = {
                **risk,
                s: max(risk.get(s, ZERO), positions.get(s, ZERO) * limit) + q * limit,
            }
            nav_floor = nav - fee(q * limit, bps)
            if (
                nav_floor <= 0
                or sum(projected.values(), ZERO) > nav_floor * number(ctx["policy"]["max_gross"])
                or any(
                    v > nav_floor * number(ctx["policy"]["max_symbol"]) for v in projected.values()
                )
                or sum(projected.values(), ZERO) > nav_floor * number(ctx["policy"]["max_abs_net"])
            ):
                eligible = False
        if not eligible or q <= 0:
            state.setdefault("blocked_actions", []).append(
                {"action_index": index, "reason": "trigger_limit_precision_capacity_or_risk"}
            )
            continue
        cid = (
            "rday-"
            + digest({"contract": state["contract"]["contract_sha256"], "action": index})[:40]
        )
        body = {
            "symbol": s,
            "side": side,
            "qty": wire_decimal(q),
            "limit_price": wire_decimal(limit),
            "type": "limit",
            "time_in_force": "day",
            "client_order_id": cid,
        }
        row = {
            "action_index": index,
            "client_order_id": cid,
            "symbol": s,
            "side": side,
            "original_quantity": action["quantity"],
            "submitted_quantity": str(q),
            "cumulative_fills": "0",
            "cumulative_notional": "0",
            "remaining_quantity": action["quantity"],
            "limit_price": action["limit_price"],
            "status": "unknown",
            "submitted_at": now.isoformat(),
            "expires_at": ctx["expires_at"],
            "order_id": None,
            "cancel_requested": False,
            "last_event_at": now.isoformat(),
            "wire": body,
        }
        state["orders"].append(row)
        commands.append({"kind": "submit_limit_day", "body": body, "execution_authority": "none"})
        if side == "buy":
            spendable -= q * limit * (1 + bps / BPS)
            risk = projected
            nav = nav_floor
    state["admission_observation"] = copy.deepcopy(observation)
    return _seal_state(state), commands


def observe(state: dict[str, Any], event: dict[str, Any], now: dt.datetime) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
    """Reconcile cumulative observations, including partial and late fills; no retry."""
    verify_state(state, state["contract"])
    state = copy.deepcopy(state)
    if state["quarantine"]:
        raise ValueError("Research state is quarantined; explicit reconciliation required")
    if not event["event_id"]:
        raise ValueError("Event identity missing")
    if event["event_id"] in state["events"]:
        if state["events"][event["event_id"]] != digest(event):
            raise ValueError("Changed duplicate event")
        return state
    row = next(r for r in state["orders"] if r["client_order_id"] == event["client_order_id"])
    ctx = state["contract"]["original_plan"]["context"]
    if (
        event["account_id"] != ctx["account_id"]
        or event["policy_id"] != state["contract"]["policy_id"]
    ):
        raise ValueError("Broker observation account/policy mismatch")
    for name in ("symbol", "side", "limit_price", "type", "time_in_force"):
        differs = (
            number(event[name]) != number(row["wire"][name])
            if name == "limit_price"
            else event[name] != row["wire"][name]
        )
        if differs:
            raise ValueError("Broker order attributes differ")
    if (
        number(event["quantity"]) != number(row["submitted_quantity"])
        or not event["order_id"]
        or row["order_id"] not in {None, event["order_id"]}
        or event["status"] not in STATUSES
    ):
        raise ValueError("Broker order identity/status differs")
    occurred, received = instant(event["occurred_at"]), instant(event["received_at"])
    if not instant(row["last_event_at"]) <= occurred <= received <= now or now.tzinfo is None:
        raise ValueError("Out-of-order or future event")
    q = number(event["cumulative_filled"])
    previous = number(row["cumulative_fills"])
    if not previous <= q <= number(row["submitted_quantity"]):
        raise ValueError("Cumulative quantity outside authorization")
    if row["status"] in TERMINAL and (q != previous or event["status"] != row["status"]):
        raise ValueError("Terminal order changed")
    if event["status"] == "filled" and q != number(row["submitted_quantity"]):
        raise ValueError("Filled status requires complete submitted quantity")
    if event["status"] == "rejected" and q:
        raise ValueError("Rejected order cannot claim fills")
    notional = q * number(event["average_price"]) if q else ZERO
    old_notional = number(row["cumulative_notional"])
    if q == previous and notional != old_notional:
        raise ValueError("Unsupported fill correction")
    if q > previous:
        delta = q - previous
        price = (notional - old_notional) / delta
        limit = number(row["limit_price"])
        if (
            price <= 0
            or (row["side"] == "buy" and (price > limit or q != q.to_integral_value()))
            or (row["side"] == "sell" and price < limit)
        ):
            raise ValueError("Fill violates original limit/whole-buy contract")
        cost = fee(delta * price, number(ctx["policy"]["one_way_bps"]))
        sign = 1 if row["side"] == "buy" else -1
        state["positions"][row["symbol"]] = str(
            number(state["positions"].get(row["symbol"], "0")) + sign * delta
        )
        state["cash"] = str(number(state["cash"]) - sign * delta * price - cost)
        state["modeled_fees"] = str(number(state["modeled_fees"]) + cost)
        cutoff = min(
            instant(ctx["expires_at"]), instant(ctx["execution_at"]) + dt.timedelta(seconds=300)
        )
        state["fills"].append(
            {
                "client_order_id": row["client_order_id"],
                "symbol": row["symbol"],
                "side": row["side"],
                "quantity": str(delta),
                "price": str(price),
                "cost_cash": str(cost),
                "occurred_at": event["occurred_at"],
                "received_at": event["received_at"],
                "late_after_submission_cutoff": occurred >= cutoff,
            }
        )
    row.update(
        order_id=event["order_id"],
        cumulative_fills=str(q),
        cumulative_notional=str(notional),
        remaining_quantity=str(number(row["original_quantity"]) - q),
        status=event["status"],
        last_event_at=event["occurred_at"],
    )
    state["events"][event["event_id"]] = digest(event)
    state["event_log"].append(copy.deepcopy(event))
    return _seal_state(state)


def expire(state: dict[str, Any], now: dt.datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    verify_state(state, state["contract"])
    state = copy.deepcopy(state)
    ctx = state["contract"]["original_plan"]["context"]
    cutoff = min(
        instant(ctx["expires_at"]), instant(ctx["execution_at"]) + dt.timedelta(seconds=300)
    )
    commands = []
    if now.tzinfo is None:
        raise ValueError("Aware cancellation clock required")
    if now >= cutoff:
        for row in state["orders"]:
            if row["status"] in TERMINAL or row["cancel_requested"]:
                continue
            if row["order_id"] is None:
                state["reconciliation_required"] = True
                continue
            row["cancel_requested"] = True
            commands.append(
                {
                    "kind": "cancel_request",
                    "order_id": row["order_id"],
                    "client_order_id": row["client_order_id"],
                    "execution_authority": "none",
                }
            )
    return _seal_state(state), commands


class ResearchStore:
    """One frozen research plan per isolated account directory, with write-ahead state.

    Reopening with a different account/plan or corrupt/missing state fails closed.
    New directories cannot confer production/global account authority.
    """

    def __init__(self, root: Path, contract: dict[str, Any]) -> None:
        validate_contract(contract)
        if root.is_symlink() or (
            root.exists()
            and any(
                p.name not in {"writer.lock", "owner.json", "state.json"} for p in root.iterdir()
            )
        ):
            raise ValueError("Only an isolated research state directory is permitted")
        self.root = root
        self.contract = copy.deepcopy(contract)
        root.mkdir(parents=True, exist_ok=True)

    def step(
        self, operation: str, payload: dict[str, Any], now: dt.datetime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with (self.root / "writer.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            owner_path, state_path = self.root / "owner.json", self.root / "state.json"
            owner = {
                "research_only": True,
                "account_id": self.contract["original_plan"]["context"]["account_id"],
                "contract_sha256": self.contract["contract_sha256"],
            }
            if owner_path.exists():
                if (
                    owner_path.is_symlink()
                    or json.loads(owner_path.read_text()) != owner
                    or not state_path.exists()
                    or state_path.is_symlink()
                ):
                    raise ValueError("Research owner/state missing or changed")
                state = json.loads(state_path.read_text())
                verify_state(state, self.contract)
            else:
                if state_path.exists():
                    raise ValueError("Unowned existing state")
                atomic_json(owner_path, owner)
                state = initial_state(self.contract)
                atomic_json(state_path, state)
            commands: list[dict[str, Any]] = []
            if operation == "admit":
                state, commands = admit(state, payload, now)
            elif operation == "observe":
                try:
                    state = observe(state, payload, now)
                except (ValueError, KeyError, TypeError, ArithmeticError, StopIteration) as exc:
                    state["quarantine"] = type(exc).__name__ + ": inconsistent broker observation"
                    state = _seal_state(state)
                    atomic_json(state_path, state)
                    raise ValueError("Research observation quarantined; no new authority") from exc
            elif operation == "expire":
                state, commands = expire(state, now)
            else:
                raise ValueError("Unknown research operation")
            # A crash after this publication cannot make a second call emit a
            # repeated submit/cancel command. Unknown outcomes keep reservations.
            atomic_json(state_path, state)
            return state, commands
