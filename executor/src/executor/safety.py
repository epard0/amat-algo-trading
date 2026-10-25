"""Durable local interlocks. These records are never retention-pruned."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from executor.errors import ConfigurationError

if TYPE_CHECKING:
    from executor.broker import AlpacaPaperClient

MIN_FREE_BYTES = 64 * 1024 * 1024


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish a complete record and sync both data and directory metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def read_receipt(path: Path) -> dict[str, Any]:
    """An unreadable order record blocks submission rather than becoming absent."""
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict) or payload.get("status") not in {
            "pending",
            "unknown",
            "submitted",
            "rejected",
            "duplicate",
        }:
            raise ValueError("invalid receipt")
        if payload.get("client_order_id") != path.stem:
            raise ValueError("receipt identity mismatch")
        return payload
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigurationError(f"unreadable submission receipt: {path.name}") from exc


def submission_interlock(root: Path) -> str:
    """Rechecked before each POST; failures are sticky until reconciled by an operator."""
    if (root / "STOP_SUBMISSIONS").exists():
        return "STOP_SUBMISSIONS exists: runtime operator stop is active"
    if shutil.disk_usage(root).free < MIN_FREE_BYTES:
        return "insufficient free state disk space (requires 64 MiB)"
    for path in sorted((root / "submissions").glob("*.json")):
        receipt = read_receipt(path)
        if receipt["status"] in {"pending", "unknown"}:
            return f"unresolved submission {path.stem}; reconcile broker outcome before trading"
    return ""


def bind_account(root: Path, account_number: str) -> None:
    """Moving state is allowed; silently moving it to another account is not."""
    if not account_number:
        raise ConfigurationError("broker account identity is missing")
    path = root / "account-identity.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            if payload["account_number"] != account_number:
                raise ConfigurationError("state directory belongs to a different paper account")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ConfigurationError("account identity record unreadable") from exc
    else:
        atomic_json(path, {"account_number": account_number})


def require_reconciled_portfolio(root: Path, now: dt.datetime) -> None:
    """Observations may block submission; they never grant submission authority."""
    try:
        payload = json.loads((root / "portfolio-monitor.json").read_text())
        observed = dt.datetime.fromisoformat(payload["observed_at"])
        age = (now - observed).total_seconds()
        if payload["status"] != "reconciled" or not 0 <= age <= 720:  # noqa: PLR2004
            raise ValueError("portfolio is stale or unreconciled")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ConfigurationError("fresh reconciled portfolio observation required") from exc


def reconcile_submissions(root: Path, client: AlpacaPaperClient) -> dict[str, list[str]]:
    """Resolve only broker-confirmed identities. Absence never permits another POST."""
    bind_account(root, client.get_account().account_number)
    result: dict[str, list[str]] = {"resolved": [], "unresolved": []}
    for path in sorted((root / "submissions").glob("*.json")):
        receipt = read_receipt(path)
        if receipt["status"] not in {"pending", "unknown"}:
            continue
        order = client.get_order_by_client_id(receipt["client_order_id"])
        if order is None:
            result["unresolved"].append(path.stem)
            continue
        if (
            not order.id
            or not order.status
            or order.client_order_id != receipt["client_order_id"]
            or order.symbol != receipt["symbol"]
            or order.side != receipt["side"]
            or order.quantity != Decimal(receipt["quantity"])
        ):
            raise ConfigurationError(f"broker identity mismatch for {path.stem}")
        atomic_json(
            path,
            {
                **receipt,
                "status": "submitted",
                "order_id": order.id,
                "reconciled_broker_status": order.status,
            },
        )
        result["resolved"].append(path.stem)
    return result


def bind_trading_mode(root: Path, mode: str) -> None:
    """Prevent paper/live state reuse, including legacy unlabelled paper evidence."""
    if mode not in {"paper", "live"}:
        raise ConfigurationError("Unknown trading mode")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "trading-mode.json"
    # Exclusive flock serializes first adoption across processes.
    import fcntl  # noqa: PLC0415

    with (root / ".trading-mode.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            try:
                if json.loads(path.read_text()) != {"mode": mode}:
                    raise ConfigurationError("State root belongs to another trading mode")
            except (OSError, ValueError) as exc:
                raise ConfigurationError("Invalid trading mode binding") from exc
            return
        if mode == "live" and any(p.name != ".trading-mode.lock" for p in root.iterdir()):
            raise ConfigurationError("Live trading requires a fresh isolated state root")
        atomic_json(path, {"mode": mode})
