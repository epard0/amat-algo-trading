"""Observed order lifecycle, separate from broker status and trading authority."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any

from executor.broker import AlpacaPaperClient, Clock, Order
from executor.journal import Journal


def order_state(order: Order, market_is_open: bool | None) -> str:
    if market_is_open is False and not order.is_terminal and order.quantity > order.filled_quantity:
        return "pending_market_closed"
    return order.status


def refresh_orders(
    root: Path, client: AlpacaPaperClient, clock: Clock, journal: Journal
) -> dict[str, Any]:
    """Poll recent and old outstanding orders; absence never means filled."""
    observed = {o.id: o for o in (*client.list_orders(), *client.list_orders(status="open"))}
    if any(not key for key in observed):
        raise ValueError("broker order is missing its identity")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "order-registry.json"
    with (root / "order-registry.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(path.read_text()) if path.exists() else {"orders": {}}
        rows = previous["orders"]
        for key, row in rows.items():
            if key not in observed and not row["terminal"]:
                row["registry_status"] = "observation_unavailable"
        for key, order in observed.items():
            state = order_state(order, clock.is_open)
            prior = rows.get(key, {})
            if prior.get("registry_status") != state or prior.get("broker_status") != order.status:
                journal.record(
                    "order_state_changed",
                    order_id=key,
                    symbol=order.symbol,
                    previous_state=prior.get("registry_status"),
                    registry_status=state,
                    broker_status=order.status,
                    filled_quantity=order.filled_quantity,
                )
            rows[key] = {
                "order_id": key,
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": str(order.quantity),
                "filled_quantity": str(order.filled_quantity),
                "remaining_quantity": str(max(order.quantity - order.filled_quantity, 0)),
                "broker_status": order.status,
                "registry_status": state,
                "terminal": order.is_terminal,
                "submitted_at": order.submitted_at,
                "observed_at": clock.timestamp.isoformat(),
            }
        result = {
            "schema": "order-registry/1",
            "observed_at": clock.timestamp.isoformat(),
            "market_is_open": clock.is_open,
            "orders": rows,
        }
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            handle.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        return result
