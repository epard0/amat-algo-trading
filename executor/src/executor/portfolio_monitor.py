"""Read-only cash/share reconciliation against a fixed, observed broker baseline.

This checks account changes, not strategy approval or pre-baseline history. It
never rebases a discrepancy away and has no order submission/cancellation path.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from executor.broker import AlpacaPaperClient, Clock
from executor.corporate_actions import share_effect
from executor.errors import ExecutorError
from executor.journal import Journal
from executor.portfolio_checkpoint import checkpoint, checkpoint_activities, verify_checkpoint
from executor.safety import atomic_json

CASH_TOLERANCE = Decimal("0.01")
SHARE_TOLERANCE = Decimal("0.000001")
# Only documented cash-only events. Share corporate actions need their own adapter.
_CASH_TYPES = frozenset(
    {
        "ACATC",
        "CGD",
        "CSD",
        "CSW",
        "DIV",
        "DIVCGL",
        "DIVCGS",
        "DIVFEE",
        "DIVFT",
        "DIVNRA",
        "DIVROC",
        "DIVTW",
        "DIVTXEX",
        "FEE",
        "INT",
        "INTNRA",
        "INTTW",
        "JNLC",
        "PTC",
        "PTR",
    }
)


def _number(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid_numeric_value") from exc
    if not result.is_finite():
        raise ValueError("nonfinite_numeric_value")
    return result


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _snapshot(client: AlpacaPaperClient) -> dict[str, Any]:
    account = client.get_account()
    if not account.account_number:
        raise ValueError("missing_account_identity")
    positions = client.list_positions()
    if len({p.symbol for p in positions}) != len(positions):
        raise ValueError("duplicate_position_symbol")
    return {
        "account_hash": _digest(account.account_number),
        "cash": str(account.cash),
        "positions": {p.symbol: str(p.quantity) for p in positions if p.quantity},
    }


def _activity_map(rows: tuple[dict[str, Any], ...]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("id")
        if not isinstance(key, str) or not key or key in result:
            raise ValueError("invalid_or_duplicate_activity_identity")
        result[key] = row
    return result


def _effect(row: dict[str, Any]) -> tuple[str, Decimal, Decimal]:
    kind = row.get("activity_type")
    if kind == "SPLIT" or (kind == "DIV" and row.get("activity_subtype") == "SDIV"):
        return share_effect(row)
    if kind in _CASH_TYPES:
        if row.get("status") not in (None, "executed"):
            raise ValueError("unsettled_nontrade_activity")
        return "", Decimal(0), _number(row.get("net_amount"))
    if kind != "FILL":
        raise ValueError("unsupported_activity_type")
    symbol = row.get("symbol")
    quantity, price = _number(row.get("qty")), _number(row.get("price"))
    if (
        not isinstance(symbol, str)
        or not symbol
        or quantity <= 0
        or price <= 0
        or row.get("side") not in {"buy", "sell"}
    ):
        raise ValueError("invalid_fill")
    delta = quantity if row["side"] == "buy" else -quantity
    # Alpaca posts USD fill cash in cents, using nearest/ties-to-even rounding.
    # Round each activity, not the aggregate: fractional executions otherwise
    # accumulate fictitious sub-cent cash across independently posted fills.
    # Posted cash-only activities (including regulatory fees) stay authoritative.
    return symbol, delta, (-delta * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def reconcile(
    baseline: dict[str, Any], observed: dict[str, Any], activities: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Explain incremental cash and shares; unsupported evidence fails explicitly."""
    if baseline["snapshot"]["account_hash"] != observed["account_hash"]:
        raise ValueError("account_identity_changed")
    for key, digest in baseline["activities"].items():
        if key not in activities or _digest(activities[key]) != digest:
            raise ValueError("baseline_activity_missing_or_changed")
    expected_cash = _number(baseline["snapshot"]["cash"])
    expected_positions = {
        symbol: _number(qty) for symbol, qty in baseline["snapshot"]["positions"].items()
    }
    new = {key: row for key, row in activities.items() if key not in baseline["activities"]}
    rounding_adjustment = Decimal(0)
    for row in new.values():
        symbol, shares, cash = _effect(row)
        if row.get("activity_type") == "FILL":
            rounding_adjustment += cash - (-shares * _number(row["price"]))
        expected_cash += cash
        if symbol:
            expected_positions[symbol] = expected_positions.get(symbol, Decimal(0)) + shares
    actual = {symbol: _number(qty) for symbol, qty in observed["positions"].items()}
    differences = {
        symbol: str(actual.get(symbol, Decimal(0)) - expected_positions.get(symbol, Decimal(0)))
        for symbol in sorted(actual.keys() | expected_positions.keys())
        if abs(actual.get(symbol, Decimal(0)) - expected_positions.get(symbol, Decimal(0)))
        > SHARE_TOLERANCE
    }
    cash_difference = _number(observed["cash"]) - expected_cash
    return {
        "status": "discrepancy"
        if differences or abs(cash_difference) > CASH_TOLERANCE
        else "reconciled",
        "exact_match": cash_difference == 0
        and all(
            actual.get(symbol, Decimal(0)) == expected_positions.get(symbol, Decimal(0))
            for symbol in actual.keys() | expected_positions.keys()
        ),
        "cash_difference": str(cash_difference),
        "position_differences": differences,
        "expected_cash": str(expected_cash),
        "observed_cash": observed["cash"],
        "fill_cash_rounding": "USD cents per fill; nearest, ties to even",
        "fill_cash_rounding_adjustment": str(rounding_adjustment),
        "activity_count_since_baseline": len(new),
        "activity_digest": _digest(activities),
    }


def _observe(root: Path, client: AlpacaPaperClient, clock: Clock) -> dict[str, Any]:
    path = root / "portfolio-baseline.json"
    baseline = json.loads(path.read_text()) if path.exists() else None
    if baseline is not None:
        verify_checkpoint(root, baseline)
    after = (
        dt.datetime.fromisoformat(baseline["activities_after"])
        if baseline
        else (
            clock.timestamp.astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            - dt.timedelta(days=1)
        )
    )
    # Broker endpoints are not an atomic snapshot. Reject visible races, then
    # retry naturally next tick; cash/share mismatches also catch delayed posting.
    first = _snapshot(client)
    activities = _activity_map(client.activity_history(after=after, until=clock.timestamp))
    second = _snapshot(client)
    check = _activity_map(client.activity_history(after=after, until=clock.timestamp))
    if first != second or activities != check:
        raise ValueError("broker_snapshot_changed_during_observation")
    if baseline is None:
        baseline = {
            "schema": "portfolio-baseline/1",
            "observed_at": clock.timestamp.isoformat(),
            "activities_after": after.isoformat(),
            "snapshot": second,
            "activities": {key: _digest(row) for key, row in activities.items()},
        }
        atomic_json(path, baseline)
        result: dict[str, Any] = {"status": "baseline_recorded"}
    else:
        if baseline["schema"] not in {"portfolio-baseline/1", "portfolio-baseline/2"}:
            raise ValueError("unsupported_baseline_schema")
        result = reconcile(
            {**baseline, "activities": checkpoint_activities(root, baseline)}, second, activities
        )
        replacement = checkpoint(root, baseline, second, activities, result, now=clock.timestamp)
        if replacement is not None:
            atomic_json(path, replacement)
            baseline = replacement
            result["checkpoint_created"] = True
    return {
        **result,
        "baseline_at": baseline["observed_at"],
        "origin_at": baseline.get("origin_at", baseline["observed_at"]),
        "checkpoint_sha256": baseline.get("checkpoint_sha256"),
        "scope": "cash_and_share_changes_since_observed_baseline",
        "cash_tolerance": str(CASH_TOLERANCE),
        "share_tolerance": str(SHARE_TOLERANCE),
    }


def refresh_portfolio(
    root: Path, client: AlpacaPaperClient, clock: Clock, journal: Journal
) -> dict[str, Any]:
    """Persist a fresh result even on broker failure; never leave stale green status."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "portfolio-monitor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "portfolio-monitor.json"
        try:
            previous = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        try:
            result = _observe(root, client, clock)
        except (ExecutorError, ValueError, KeyError, TypeError, OSError) as exc:
            result = {"status": "observation_unavailable", "error_type": type(exc).__name__}
            if isinstance(exc, ValueError) and str(exc) in {
                "account_identity_changed",
                "baseline_activity_missing_or_changed",
                "unsupported_activity_type",
                "unsettled_nontrade_activity",
                "invalid_fill",
                "broker_snapshot_changed_during_observation",
                "unsupported_baseline_schema",
            }:
                result["reason"] = str(exc)
        result.update(schema="portfolio-monitor/1", observed_at=clock.timestamp.isoformat())
        if result["status"] == "reconciled":
            result["last_reconciled_at"] = clock.timestamp.isoformat()
        else:
            result["last_reconciled_at"] = previous.get("last_reconciled_at")
        if (result["status"], result.get("reason")) != (
            previous.get("status"),
            previous.get("reason"),
        ):
            journal.record("portfolio_monitor_changed", **result)
        atomic_json(path, result)
        return result
