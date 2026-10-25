"""Fixed synthetic research contexts; no real account, calendar, quote or order."""

import copy
import datetime as dt
import json
from pathlib import Path

from executor.research_day_policy import digest

START = dt.datetime(2026, 10, 26, 13, 30, tzinfo=dt.UTC)
NOW = START + dt.timedelta(seconds=61)
END = START + dt.timedelta(minutes=5)


def contract(cost=6):
    p = json.loads((Path(__file__).parent / "fixtures/research-day-plan.json").read_text())
    ctx = p["original_plan"]["context"]
    ctx["policy"]["one_way_bps"] = str(cost)
    return rebind(p)


def rebind(p):
    p = copy.deepcopy(p)
    original = p["original_plan"]
    ctx, proposal = original["context"], original["proposal"]
    proposal["policy_id"] = digest(ctx["policy"])
    for i in ctx["authorized_intents"]:
        i["account_id"], i["policy_id"] = ctx["account_id"], proposal["policy_id"]
    proposal["account_id"] = ctx["account_id"]
    for clock in ["decision_at", "execution_at", "expires_at"]:
        proposal[clock] = ctx[clock]
    proposal["authorized_intents_sha256"] = digest(ctx["authorized_intents"])
    original["context_sha256"] = proposal["evidence_sha256"] = digest(ctx)
    original["plan_id"] = digest(
        {"policy": original["schema"], "proposal": proposal, "context_sha256": digest(ctx)}
    )
    p["policy_id"] = digest(
        {"schema": p["schema"], "rules": p["rules"], "cost_bps": ctx["policy"]["one_way_bps"]}
    )
    p["contract_sha256"] = digest({k: v for k, v in p.items() if k != "contract_sha256"})
    return p


def observation(p):
    ctx = p["original_plan"]["context"]
    quote_time = START + dt.timedelta(seconds=60)
    prefix = {
        "source_id": "SYNTHETIC-SIP",
        "quote_source_id": "SYNTHETIC-SIP",
        "price_basis": "raw",
        "volume_unit": "shares",
        "tradable": True,
        "quote_time": quote_time.isoformat(),
        "quote_available_at": quote_time.isoformat(),
        "bid": "100",
        "ask": "100.5",
        "prefix_start": START.isoformat(),
        "prefix_end": quote_time.isoformat(),
        "prefix_available_at": quote_time.isoformat(),
        "prefix_volume": "1000",
        "prefix_complete": True,
    }
    return {
        "account": {
            "account_id": ctx["account_id"],
            "confirmed_cash": ctx["confirmed_cash"],
            "positions": dict(ctx["positions"]),
            "source_id": "SYNTHETIC-ACCOUNT",
            "observed_at": NOW.isoformat(),
            "trading_allowed": True,
        },
        "calendar": {
            "source_id": "SYNTHETIC-CALENDAR",
            "available_at": ctx["decision_at"],
            "session_date": str(START.date()),
            "open": START.isoformat(),
            "close": (START + dt.timedelta(hours=6, minutes=30)).isoformat(),
        },
        "corporate_actions": {
            "source_id": "SYNTHETIC-NO-ACTIONS",
            "actions": [],
            "actions_sha256": digest([]),
            "from_session": "2026-10-23",
            "to_session": "2026-10-26",
        },
        "symbols": {r["symbol"]: copy.deepcopy(prefix) for r in ctx["evidence"]},
    }


def event(
    state, *, qty="0", status="new", price="100.5", seconds=62, event_id="one", order_index=0
):
    row = state["orders"][order_index]
    ctx = state["contract"]["original_plan"]["context"]
    return {
        "event_id": event_id,
        "account_id": ctx["account_id"],
        "policy_id": state["contract"]["policy_id"],
        "client_order_id": row["client_order_id"],
        "order_id": "SYNTHETIC-BROKER-" + str(order_index),
        "symbol": row["symbol"],
        "side": row["side"],
        "quantity": row["submitted_quantity"],
        "limit_price": row["limit_price"],
        "type": "limit",
        "time_in_force": "day",
        "status": status,
        "cumulative_filled": qty,
        "average_price": price,
        "occurred_at": (START + dt.timedelta(seconds=seconds)).isoformat(),
        "received_at": (START + dt.timedelta(seconds=seconds + 1)).isoformat(),
    }
