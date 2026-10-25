"""Offline parser/wire witnesses for research plans; no account or network access.

Run by the local research validation harness, never registered with the daemon.
The actual paper client's payload builder runs against a replaced transport and
blocked sockets. Its synthetic acknowledgments are not broker observations.
"""

from __future__ import annotations

import copy
import json
import socket
import sys
from decimal import Decimal
from typing import Any, NoReturn
from unittest.mock import patch

from executor.broker.alpaca_paper import AlpacaPaperClient
from executor.errors import PlanError
from executor.plan_source import parse_plan


def probe(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return failures of representational parity without routing a research order."""
    refusals = []
    for label, payload in (("envelope", envelope), ("inner_native_plan", envelope["native_plan"])):
        try:
            parse_plan(payload)
        except PlanError as exc:
            refusals.append({"artifact": label, "refused": True, "reason": str(exc)})
        else:
            raise AssertionError("Research capability contract reached the legacy executor")
    bodies = []

    def transport(path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path != "/v2/orders":
            raise AssertionError("Unexpected offline transport path")
        bodies.append(copy.deepcopy(body))
        return {
            **body,
            "id": "synthetic-wire-ack",
            "filled_qty": "0",
            "status": "accepted",
            "submitted_at": "2026-10-26T13:30:00+00:00",
        }

    def deny(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise AssertionError("Offline contract probe attempted network access")

    client = AlpacaPaperClient("synthetic-not-a-key", "synthetic-not-a-secret")
    with (
        patch.object(client, "_post_once", side_effect=transport),
        patch.object(socket, "socket", deny),
        patch.object(AlpacaPaperClient, "_read", deny),
    ):
        for intent in envelope["native_plan"]["intents"]:
            action = next(
                a
                for a in envelope["adapter_plan"]["proposal"]["actions"]
                if a.get("symbol") == intent["symbol"]
            )
            # A separate offline serialization witness, NOT an authorization or
            # a claim that the legacy submit/batch path supports these actions.
            client.submit_limit_order(
                symbol=intent["symbol"],
                side=intent["side"],
                quantity=Decimal(intent["quantity"]),
                limit_price=Decimal(action["limit_price"]),
                client_order_id="synthetic-offline-" + str(len(bodies)),
            )
    return {
        "schema": "offline-research-broker-contract-witness/1",
        "parser_refusals": refusals,
        "wire_payloads": bodies,
        "network_calls": 0,
        "real_broker_observation": False,
        "broker_execution_parity": False,
        "limitations": [
            "DAY lifetime does not enforce the original research expiry/opening window.",
            "Limit payloads have no opening-only trigger or complete-window eligibility rule.",
            "Wire payloads have no 1% opening-volume cap or pending-commitment context.",
            "Synthetic acknowledgments prove serialization only; actual fills/costs unknown.",
        ],
    }


if __name__ == "__main__":
    json.dump([probe(value) for value in json.load(sys.stdin)], sys.stdout, sort_keys=True)
