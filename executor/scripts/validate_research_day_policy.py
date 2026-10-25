"""Offline research lifecycle and actual DAY-limit/cancel payload witness.

No real credentials, requests or production state; not a daemon command.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch
from urllib.parse import urlsplit

from executor.broker.alpaca_paper import AlpacaPaperClient
from executor.errors import OrderOutcomeUnknownError, PlanError
from executor.plan_source import parse_plan
from executor.research_day_policy import ResearchStore


def run_case(case: dict[str, Any], root: Path) -> dict[str, Any]:
    plan = case["plan"]
    store = ResearchStore(root, plan)
    try:
        parse_plan(plan)
    except PlanError:
        legacy_refused = True
    else:
        raise ValueError("Legacy executor must refuse research policy")
    client = AlpacaPaperClient("synthetic-offline", "synthetic-offline")
    wire: list[dict[str, Any]] = []
    cancels: list[dict[str, str]] = []
    history = []

    def deny(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise AssertionError("Offline research attempted network")

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path != "/v2/orders":
            raise ValueError("Unexpected offline POST")
        wire.append(dict(body))
        if case["name"] == "unknown_ack":
            raise OrderOutcomeUnknownError("Synthetic uncertain outcome; no retry")
        return {
            **body,
            "id": "SYNTHETIC-BROKER-0",
            "status": "new",
            "filled_qty": "0",
            "submitted_at": case["admit_at"],
        }

    def read(request: Any) -> None:
        if request.get_method() != "DELETE":
            raise ValueError("Unexpected offline request")
        cancels.append({"method": "DELETE", "path": urlsplit(request.full_url).path})

    with (
        patch.object(socket, "socket", deny),
        patch.object(client, "_post_once", side_effect=post),
        patch.object(client, "_read", side_effect=read),
    ):
        now = dt.datetime.fromisoformat(case["admit_at"])
        state, commands = store.step("admit", case["observation"], now)
        history.append({"operation": "admit", "state": state, "commands": commands})
        for command in commands:
            body = command["body"]
            try:
                client.submit_limit_order(
                    symbol=body["symbol"],
                    side=body["side"],
                    quantity=Decimal(body["qty"]),
                    limit_price=Decimal(body["limit_price"]),
                    client_order_id=body["client_order_id"],
                )
            except OrderOutcomeUnknownError:
                continue
            ack = event(state, body, "ack", now + dt.timedelta(milliseconds=1), "0", "0", "new")
            state, _ = store.step("observe", ack, now + dt.timedelta(milliseconds=1))
        # Reopening the owner after a write-ahead reservation never resends.
        _, repeated = ResearchStore(root, plan).step("admit", case["observation"], now)
        if repeated:
            raise ValueError("Restart repeated a submission")
        for index, step in enumerate(case["timeline"]):
            now = dt.datetime.fromisoformat(step["at"])
            if step["kind"] == "expire":
                state, commands = ResearchStore(root, plan).step("expire", {}, now)
                for command in commands:
                    if command["kind"] != "cancel_request":
                        raise ValueError("Unexpected research command")
                    client.cancel_order(command["order_id"])
                history.append({"operation": "expire", "state": state, "commands": commands})
                continue
            body = state["orders"][0]["wire"]
            observation = event(
                state,
                body,
                "fixture-" + str(index),
                now,
                step["quantity"],
                step["price"],
                step["status"],
            )
            try:
                state, _ = ResearchStore(root, plan).step("observe", observation, now)
            except ValueError:
                if case["name"] != "bad_fill":
                    raise
                state = json.loads((root / "state.json").read_text())
            history.append({"operation": "observe", "observation": observation, "state": state})
    return {
        "name": case["name"],
        "state": state,
        "history": history,
        "wire_payloads": wire,
        "cancel_requests": cancels,
        "legacy_refused": legacy_refused,
        "restart_no_resend": True,
        "network_calls": 0,
        "real_broker_observation": False,
    }


def event(
    state: dict[str, Any],
    body: dict[str, Any],
    identity: str,
    now: dt.datetime,
    quantity: str,
    price: str,
    status: str,
) -> dict[str, Any]:
    return {
        "event_id": identity,
        "account_id": state["contract"]["original_plan"]["context"]["account_id"],
        "policy_id": state["contract"]["policy_id"],
        "client_order_id": body["client_order_id"],
        "order_id": "SYNTHETIC-BROKER-0",
        "symbol": body["symbol"],
        "side": body["side"],
        "quantity": body["qty"],
        "limit_price": body["limit_price"],
        "type": "limit",
        "time_in_force": "day",
        "status": status,
        "cumulative_filled": quantity,
        "average_price": price,
        "occurred_at": now.isoformat(),
        "received_at": now.isoformat(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    inputs = json.load(sys.stdin)
    json.dump(
        [run_case(row, args.output_root / (row["name"] + "-" + row["cost_bps"])) for row in inputs],
        sys.stdout,
        sort_keys=True,
    )
