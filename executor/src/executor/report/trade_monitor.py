"""Durable, offline paper trade diagnostics. No strategy or third-party imports.

Each report replaces one dated observation under a file lock. Rebuilding is
deterministic: broker quantities are cumulative snapshots, never new fills to
be added on every report. The result implements the trade-monitor/1 JSON
boundary consumed by AMAT's offline viewer.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from executor.plan_source import ExecutionPlan, parse_plan
from executor.report.daily import DailyReport
from executor.submit import client_order_id_for

HORIZONS = (1, 5, 20, 60)
MIN_EVENTS = 30
MIN_DATES = 10


def _number(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _write(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _local_timestamp(value: str, zone: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp.astimezone(ZoneInfo(zone)) if timestamp.tzinfo else None


def _events(  # noqa: PLR0912, PLR0915 - retain each independent reconciliation check
    observation: dict[str, Any],
    known_plans: dict[str, ExecutionPlan] | None = None,
    known_submissions: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plan = parse_plan(observation["plan"]) if observation["plan"] else None
    intents = {client_order_id_for(plan, item): item for item in plan.intents} if plan else {}
    submitted = {
        **(known_submissions or {}),
        **{
            item["client_order_id"]: item
            for item in observation["submission"].get("outcomes", [])
            if item.get("client_order_id")
        },
    }
    orders = {}
    duplicates = set()
    for ordinal, order in enumerate(observation["orders"]):
        key = order.get("client_order_id") or f"missing-id-{observation['session']}-{ordinal}"
        if key in orders:
            duplicates.add(key)
        orders[key] = order
    plans = dict.fromkeys(intents, plan)
    for key in orders:
        previous_plan = (known_plans or {}).get(key)
        if key not in intents and previous_plan is not None:
            for item in previous_plan.intents:
                if client_order_id_for(previous_plan, item) == key:
                    intents[key] = item
                    plans[key] = previous_plan
    rows = []
    for key in sorted(intents.keys() | orders.keys()):
        plan = plans.get(key)
        intent, order, submission = intents.get(key), orders.get(key), submitted.get(key)
        alerts = []
        if key in duplicates:
            alerts.append("duplicate_order_identity")
        filled = _number(order["filled_quantity"]) if order else 0.0
        price = _number(order.get("filled_avg_price")) if order else None
        expected = float(intent.quantity) if intent else None
        sent = _number(submission.get("submitted_quantity")) if submission else None
        broker_quantity = _number(order.get("quantity")) if order else None
        side = intent.side if intent else str(order["side"])  # type: ignore[index]
        symbol = intent.symbol if intent else str(order["symbol"])  # type: ignore[index]
        matched = bool(intent and order)
        status = "matched" if matched else ("recorded_intent" if intent else "unmatched_order")
        if intent is None:
            alerts.append("unmatched_order")
        if order and intent:
            if order["side"] != side or order["symbol"] != symbol:
                status = "mismatch"
                alerts.append("symbol_or_side_mismatch")
            authorized = sent if sent is not None else expected
            if (
                broker_quantity is None
                or authorized is None
                or not math.isclose(broker_quantity, authorized, rel_tol=1e-9, abs_tol=1e-9)
            ):
                alerts.append("submitted_quantity_mismatch")
                status = "mismatch"
            elif sent is not None and expected != sent:
                status = "matched_with_recorded_adjustment"
            if filled is not None and authorized is not None and filled > authorized + 1e-9:
                alerts.append("overfill")
                status = "mismatch"
        execution = str(order["status"]) if order else "no_order_observed"
        broker_status = execution
        if order and order.get("registry_status") == "pending_market_closed":
            execution = "pending_market_closed"
            alerts.append(execution)
        if not observation.get("orders_available", True):
            execution = "order_data_unavailable"
            alerts.append(execution)
        elif not order or not filled:
            alerts.append("unfilled")
        elif broker_quantity is not None and filled < broker_quantity - 1e-9:
            alerts.append("partial_fill")
        if execution in {"rejected", "canceled", "expired"}:
            alerts.append(execution)
        if order and (
            broker_quantity is None
            or broker_quantity <= 0
            or filled is None
            or filled < 0
            or (filled > 0 and (price is None or price <= 0))
        ):
            status = "mismatch"
            alerts.append("invalid_execution_values")
        if key in duplicates:
            status = "mismatch"
        zone = plan.exchange_timezone if plan else "America/New_York"
        submitted_at = _local_timestamp(str(order.get("submitted_at", "")), zone) if order else None
        filled_at = _local_timestamp(str(order.get("filled_at", "")), zone) if order else None
        execution_session = str(submitted_at.date()) if submitted_at else observation["session"]
        if order and submitted_at is None:
            alerts.append("missing_submission_timestamp")
        if execution_session > observation["session"]:
            status = "mismatch"
            alerts.append("order_after_report_session")
        if submitted_at and plan and intent:
            if submitted_at > plan.expires_at:
                alerts.append("execution_after_plan_expiry")
                status = "mismatch"
            if (
                submitted_at.date() <= plan.as_of_session
                or submitted_at.date() < plan.earliest_execution_session
            ):
                alerts.append("execution_before_authorized_session")
                status = "mismatch"
            if not plan.window_start_local <= submitted_at.time() <= plan.window_end_local:
                alerts.append("outside_planned_window")
        if filled_at and filled_at.date().isoformat() != execution_session:
            alerts.append("multisession_fill_unsupported")
        reference = float(intent.reference_price) if intent else None
        diagnostics = plan.raw.get("monitoring", {}).get("decision_diagnostics", {}) if plan else {}
        gap = (
            (price / reference - 1) * (1 if side == "buy" else -1) * 10000
            if price and reference
            else None
        )
        rows.append(
            {
                "event_id": key,
                "source": "paper",
                "run_id": "paper-account",
                "model_id": plan.config_sha256 if plan and intent else "unattributed",
                "model_name": plan.strategy_name if plan and intent else "unattributed",
                "model_version": plan.strategy_version if plan and intent else None,
                "code_revision": plan.code_revision if plan and intent else None,
                "snapshot_id": plan.snapshot_id if plan and intent else None,
                "event_kind": "instruction",
                "symbol": symbol,
                "side": side,
                "decision_session": str(plan.as_of_session) if plan and intent else None,
                "execution_session": execution_session,
                "reason": intent.reason if intent else "unmatched_order",
                "regime": "unknown",
                "signal_score": diagnostics.get("trend_scores", {}).get(symbol),
                "signal_score_basis": diagnostics.get("score_basis"),
                "decision_constraints": diagnostics.get("binding_constraints", []),
                "forecast_portfolio_volatility": diagnostics.get(
                    "forecast_portfolio_volatility_post"
                ),
                "instruction_status": status,
                "execution_status": execution,
                "broker_status": broker_status,
                "intended_quantity": expected,
                "submitted_quantity": sent,
                "broker_quantity": broker_quantity,
                "filled_quantity": filled,
                "fill_price": price,
                "cost_cash": None,
                "current_weight": intent.current_weight if intent else None,
                "target_weight": intent.target_weight if intent else None,
                "decision_reference_price": reference,
                "decision_to_fill_gap_bps": gap,
                "decision_to_fill_gap_basis": (
                    "raw decision close; may include corporate-action gaps"
                ),
                "submitted_at": order.get("submitted_at") if order else None,
                "filled_at": order.get("filled_at") if order else None,
                "fill_timestamp_verified": filled_at is not None,
                "submission_note": submission.get("detail") if submission else None,
                "forecasts": [],
                "forecast_status": "not_emitted_by_plan",
                "alerts": alerts,
                "outcomes": {str(h): {"status": "pending"} for h in HORIZONS},
            }
        )
    return rows


def _history(observation: dict[str, Any]) -> list[dict[str, Any]]:
    plan = observation.get("plan") or {}
    context = plan.get("monitoring") or {}
    if not context:
        return []
    if context.get("schema") != "trade-context/1":
        raise ValueError("unsupported paper monitoring context")
    history: list[dict[str, Any]] = context.get("price_history", [])
    dates = [str(dt.date.fromisoformat(frame["session"])) for frame in history]
    as_of = context.get("as_of_session")
    if (
        dates != sorted(set(dates))
        or as_of != plan.get("as_of_session")
        or (dates and dates[-1] != as_of)
        or (as_of and as_of > observation["session"])
    ):
        raise ValueError("paper monitoring history has inconsistent or future session dates")
    return history


def _mature(event: dict[str, Any], history: list[dict[str, Any]], observed: str) -> None:
    if all(row["status"] == "matured" for row in event["outcomes"].values()):
        return
    dates = [frame["session"] for frame in history]
    price = event["fill_price"]
    for horizon in HORIZONS:
        outcome = event["outcomes"][str(horizon)]
        if outcome["status"] == "matured":
            continue
        if not event["filled_quantity"] or price is None or price <= 0:
            outcome["status"] = "no_fill_price"
            continue
        if not history:
            outcome["status"] = "missing_history"
            continue
        if not event["fill_timestamp_verified"]:
            outcome["status"] = "missing_fill_timestamp"
            continue
        if (
            event["instruction_status"] == "mismatch"
            or "multisession_fill_unsupported" in event["alerts"]
        ):
            outcome["status"] = "execution_mismatch"
            continue
        if event["execution_session"] not in dates:
            outcome["status"] = (
                "missing_history" if dates and event["execution_session"] < dates[0] else "pending"
            )
            continue
        start = dates.index(event["execution_session"])
        end = start + horizon - 1
        if end >= len(history):
            continue
        marks = [frame["prices"].get(event["symbol"]) for frame in history[start : end + 1]]
        if not all(marks):
            outcome["status"] = "missing_price"
            continue
        adjusted = [_number(mark.get("adjusted_close")) for mark in marks]
        raw = _number(marks[0].get("raw_close"))
        if raw is None or raw <= 0 or any(value is None or value <= 0 for value in adjusted):
            outcome["status"] = "missing_price"
            continue
        # Both adjustment factors come from this one snapshot, never from
        # independently revised historical frames saved on different days.
        base = price * float(adjusted[0]) / raw  # type: ignore[arg-type]
        returns = [(float(value) / base - 1) * 10000 for value in adjusted]  # type: ignore[arg-type]
        signed = returns if event["side"] == "buy" else [-value for value in returns]
        outcome.update(
            status="matured",
            horizon_sessions=horizon,
            end_session=dates[end],
            observed_session=observed,
            asset_return_bps=returns[-1],
            signed_markout_bps=signed[-1],
            favourable_close_bps=max(0.0, *signed),
            adverse_close_bps=max(0.0, -min(signed)),
            basis="adjusted close price proxy; no fees or realized P&L allocation",
        )


def _summary(events: list[dict[str, Any]], as_of_session: str | None) -> dict[str, Any]:
    cohorts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        cohorts[event["model_id"], event["side"]].append(event)
    groups = []
    for (model, side), rows in sorted(cohorts.items()):
        mature = [row for row in rows if row["outcomes"]["5"]["status"] == "matured"]
        values: dict[str, list[float]] = defaultdict(list)
        for row in mature:
            values[row["execution_session"]].append(row["outcomes"]["5"]["signed_markout_bps"])
        groups.append(
            {
                "model_id": model,
                "side": side,
                "events": len(rows),
                "matured_5s": len(mature),
                "matured_dates": len(values),
                "date_weighted_mean_5s_bps": statistics.fmean(
                    statistics.fmean(v) for v in values.values()
                )
                if values
                else None,
                "sample_status": "descriptive_sample"
                if len(mature) >= MIN_EVENTS and len(values) >= MIN_DATES
                else "insufficient_sample",
            }
        )
    return {
        "events": len(events),
        "attention_events": sum(bool(row["alerts"]) for row in events),
        "events_without_forecasts": len(events),
        "matured_5s": sum(row["outcomes"]["5"]["status"] == "matured" for row in events),
        "alert_counts": dict(Counter(alert for row in events for alert in row["alerts"])),
        "cohorts": groups,
        "as_of_session": as_of_session,
        "latest_session_events": sum(row["execution_session"] == as_of_session for row in events),
        "latest_session_attention_events": sum(
            bool(row["alerts"]) and row["execution_session"] == as_of_session for row in events
        ),
    }


def _rebuild(state_root: Path) -> dict[str, Any]:
    events: dict[str, dict[str, Any]] = {}
    known_plans: dict[str, ExecutionPlan] = {}
    known_submissions: dict[str, Any] = {}
    inputs = []
    observations = state_root / "trade-observations"
    for path in sorted(observations.glob("????-??-??.json")):
        raw = path.read_bytes()
        observation = json.loads(raw)
        if (
            observation.get("schema") != "paper-trade-observation/1"
            or path.stem != observation["session"]
        ):
            raise ValueError(f"invalid paper trade observation: {path.name}")
        inputs.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for raw_plan in [*observation.get("prior_plans", []), observation.get("plan")]:
            if raw_plan:
                plan = parse_plan(raw_plan)
                for intent in plan.intents:
                    known_plans[client_order_id_for(plan, intent)] = plan
        known_submissions.update(
            {
                item["client_order_id"]: item
                for item in observation["submission"].get("outcomes", [])
                if item.get("client_order_id")
            }
        )
        for event in _events(observation, known_plans, known_submissions):
            previous = events.get(event["event_id"])
            unchanged = ("fill_price", "filled_quantity", "symbol", "side", "instruction_status")
            if previous and all(previous[key] == event[key] for key in unchanged):
                event["outcomes"] = previous["outcomes"]
            events[event["event_id"]] = event
        history = _history(observation)
        for event in events.values():
            _mature(event, history, observation["session"])
    rows = sorted(events.values(), key=lambda row: (row["execution_session"], row["event_id"]))
    payload: dict[str, Any] = {
        "schema": "trade-monitor/1",
        "metadata": {"source": "paper", "observations": len(inputs), "inputs": inputs},
        "notes": [
            "Events are order instructions, not closed positions. "
            "Quantities are cumulative broker snapshots.",
            "Positive signed markouts mean price rose after buying or fell after selling.",
            "Horizons count actual sessions in one price snapshot; "
            "1 ends at the fill-session close.",
            "Adjusted close markouts exclude fees and are not realized "
            "position P&L or NBBO markouts.",
            "Decision-to-fill gap includes overnight market movement "
            "and any corporate-action price gap.",
            "Paper plans currently emit targets, not return or probability forecasts. "
            "Forecast accuracy is unavailable.",
            "Price outcomes usually become available with the next session's plan; "
            "missing history stays explicit.",
            "Sample sizes count events and dates. Overlapping horizons are dependent; "
            "no significance claim is made.",
        ],
        "summary": _summary(rows, inputs[-1]["file"][:10] if inputs else None),
        "events": rows,
    }
    _write(state_root / "trade-monitor.json", payload)
    lines = ["# Paper trade monitor", "", "Instruction events, not closed position P&L.", ""]
    summary = payload["summary"]
    lines.extend(
        [
            f"- Events: {summary['events']}",
            f"- Events needing execution review: {summary['attention_events']}",
            f"- Matured five-session outcomes: {summary['matured_5s']}",
            f"- Events without explicit forecasts: {summary['events_without_forecasts']}",
            "",
            "| Model | Side | Matured / events | Dates | Mean 5s (bps) | Sample |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for group in summary["cohorts"]:
        mean = group["date_weighted_mean_5s_bps"]
        formatted = "unavailable" if mean is None else f"{mean:.1f}"
        lines.append(
            f"| {group['model_id'][:16]} | {group['side']} | "
            f"{group['matured_5s']} / {group['events']} | {group['matured_dates']} | "
            f"{formatted} | {group['sample_status']} |"
        )
    lines.extend(["", *[f"- {note}" for note in payload["notes"]], ""])
    temporary = state_root / "trade-monitor.md.tmp"
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(state_root / "trade-monitor.md")
    return payload


def rebuild_monitor(state_root: Path) -> dict[str, Any]:
    """Replay only local observations. No credentials, broker calls or emails."""
    state_root.mkdir(parents=True, exist_ok=True)
    with (state_root / "trade-monitor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _rebuild(state_root)


def capture_report(
    state_root: Path,
    report: DailyReport,
    plan: ExecutionPlan | None,
    *,
    orders_available: bool = True,
) -> dict[str, Any]:
    state_root.mkdir(parents=True, exist_ok=True)
    observation: dict[str, Any] = {
        "schema": "paper-trade-observation/1",
        "session": str(report.session),
        "generated_at": report.generated_at.isoformat(),
        "plan": plan.raw if plan else None,
        "submission": report.submission,
        "orders": list(report.orders),
        "orders_available": orders_available,
    }
    # Validate before replacing a previously usable observation.
    _events(observation)
    _history(observation)
    with (state_root / "trade-monitor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        directory = state_root / "trade-observations"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{report.session}.json"
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            prior_plans = previous.get("prior_plans", [])
            if previous["plan"] and previous["plan"] != observation["plan"]:
                prior_plans = [*prior_plans, previous["plan"]]
            observation["prior_plans"] = prior_plans
            if not orders_available:
                # A failed refresh must not erase the last observed fills.
                observation["orders"] = previous["orders"]
            if not observation["submission"]:
                observation["submission"] = previous["submission"]
            else:
                outcomes = {
                    item["client_order_id"]: item
                    for item in [
                        *previous["submission"].get("outcomes", []),
                        *observation["submission"].get("outcomes", []),
                    ]
                    if item.get("client_order_id")
                }
                observation["submission"] = {
                    **observation["submission"],
                    "outcomes": list(outcomes.values()),
                }
        _write(path, observation)
        return _rebuild(state_root)
