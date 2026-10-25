"""Offline intraday contract and order-event reconciliation; no broker transport."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

SUPPORTED = frozenset({"actual-risk-state/1", "cash-budget/1"})


def validate_policy_plan(
    payload: dict[str, Any],
    *,
    now: dt.datetime,
    account_id: str,
    authorized_configs: frozenset[str],
) -> None:
    """New plans need an explicit capability handshake and exact authorization."""
    if now.tzinfo is None or payload.get("schema") != "research-policy-plan/1":
        raise ValueError("unsupported policy contract or clock")
    if payload.get("account_id") != account_id or not account_id:
        raise ValueError("policy account mismatch")
    if set(payload.get("required_capabilities", [])) != SUPPORTED:
        raise ValueError("unsupported required capabilities")
    if payload.get("research_only") is not True:
        raise ValueError("offline adapter accepts research plans only")
    if dt.datetime.fromisoformat(payload["expires_at"]) <= now:
        raise ValueError("policy plan expired")
    plan = payload["plan"]
    if plan["config_sha256"] not in authorized_configs:
        raise ValueError("configuration not explicitly authorized")
    original = {k: v for k, v in payload.items() if k != "decision_id"}
    if (
        hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
        != payload["decision_id"]
    ):
        raise ValueError("policy decision identity changed")
    for intent in plan["intents"]:
        quantity = Decimal(intent["quantity"])
        if not quantity.is_finite() or quantity <= 0 or intent["side"] not in {"buy", "sell"}:
            raise ValueError("invalid authorized intent")


@dataclass
class OfflineOrderBook:
    """Cumulative authorization survives models, callbacks and cancel/fill races."""

    quantities: dict[str, Decimal] = field(default_factory=dict)
    filled: dict[str, Decimal] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    events: dict[str, str] = field(default_factory=dict)

    def authorize(self, decision_id: str, symbol: str, side: str, quantity: Decimal) -> str:
        if not quantity.is_finite() or quantity <= 0 or side not in {"buy", "sell"}:
            raise ValueError("invalid order authorization")
        key = hashlib.sha256(f"{decision_id}:{symbol}:{side}".encode()).hexdigest()
        if key in self.quantities and self.quantities[key] != quantity:
            raise ValueError("changed repeated order authorization")
        self.quantities.setdefault(key, quantity)
        self.filled.setdefault(key, Decimal(0))
        self.statuses.setdefault(key, "authorized")
        return key

    def observe(
        self, event_id: str, order_id: str, *, cumulative_filled: Decimal, status: str
    ) -> None:
        allowed = {
            "submitted",
            "partial",
            "filled",
            "cancel_pending",
            "cancelled",
            "rejected",
            "unknown",
        }
        if order_id not in self.quantities or status not in allowed or not event_id:
            raise ValueError("unknown order event")
        identity = f"{order_id}:{cumulative_filled}:{status}"
        if event_id in self.events:
            if self.events[event_id] != identity:
                raise ValueError("changed duplicate broker event")
            return
        if (
            not cumulative_filled.is_finite()
            or not self.filled[order_id] <= cumulative_filled <= self.quantities[order_id]
        ):
            raise ValueError("invalid cumulative fill")
        if self.statuses[order_id] == "filled" and status != "filled":
            raise ValueError("terminal filled order cannot reopen")
        if status == "filled" and cumulative_filled != self.quantities[order_id]:
            raise ValueError("filled status requires complete authorized quantity")
        self.events[event_id] = identity
        self.filled[order_id] = cumulative_filled
        self.statuses[order_id] = status

    def remaining(self, order_id: str) -> Decimal:
        return self.quantities[order_id] - self.filled[order_id]

    def may_replace(self, order_id: str) -> bool:
        return self.statuses[order_id] in {"cancelled", "rejected"}

    def checkpoint(self) -> dict[str, Any]:
        body = {
            "schema": "offline-order-book/1",
            "quantities": {k: str(v) for k, v in self.quantities.items()},
            "filled": {k: str(v) for k, v in self.filled.items()},
            "statuses": self.statuses.copy(),
            "events": self.events.copy(),
        }
        return {
            **body,
            "sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
        }

    @classmethod
    def restore(cls, value: dict[str, Any]) -> OfflineOrderBook:
        body = {k: v for k, v in value.items() if k != "sha256"}
        if body.get("schema") != "offline-order-book/1" or hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()
        ).hexdigest() != value.get("sha256"):
            raise ValueError("changed order checkpoint")
        result = cls(
            {k: Decimal(v) for k, v in body["quantities"].items()},
            {k: Decimal(v) for k, v in body["filled"].items()},
            body["statuses"],
            body["events"],
        )
        if set(result.quantities) != set(result.filled) or set(result.quantities) != set(
            result.statuses
        ):
            raise ValueError("inconsistent order checkpoint")
        if any(
            not q.is_finite()
            or q <= 0
            or not result.filled[k].is_finite()
            or not 0 <= result.filled[k] <= q
            for k, q in result.quantities.items()
        ):
            raise ValueError("invalid checkpoint quantities")
        return result
