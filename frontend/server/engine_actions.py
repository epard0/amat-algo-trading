"""Typed command builders for offline historical measurement.

Planning is deliberately absent: the executor daemon builds each session's plan
automatically from live account state, so no manual plan action exists here."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import HTTPException

from server.strategy_defaults import default_snapshot, default_strategy


def capabilities(root: Path, storage: Path):
    root = Path(root)
    snapshots = [
        p.parent.name
        for p in sorted((root / "amat/data/snapshots").glob("*/daily.parquet"))
        if not p.is_symlink()
        and p.resolve().is_relative_to((root / "amat/data/snapshots").resolve())
    ]
    common = {
        "snapshot": {
            "type": "select",
            "options": snapshots,
            "default": default_snapshot(snapshots),
        }
    }
    ready = (root / "amat/.venv/bin/python").exists() and bool(snapshots)
    return [
        {
            "id": "amat.backtest",
            "capability": "amat.backtest",
            "supported": ready,
            "reason": None if ready else "AMAT runtime and frozen snapshot required",
            "effects": [
                "Runs offline historical measurement on registered frozen data",
                "Writes PDF report and native run evidence under terminal storage",
                "Preserves native holdout guards; a dataset crossing its boundary can be refused",
            ],
            "inputs": {
                **common,
                "version": {
                    "type": "select",
                    "options": [
                        *(
                            ["daily-volatility"]
                            if (
                                root / "amat/configs/strategy/amat_group_selection_volatility.toml"
                            ).is_file()
                            else []
                        ),
                        "daily-core",
                        *(
                            ["daily-all-groups"]
                            if (
                                root / "amat/configs/strategy/amat_group_selection_all_groups.toml"
                            ).is_file()
                            else []
                        ),
                        "v1",
                        "v4",
                        "v5",
                        "v6",
                    ],
                    "default": "daily-volatility"
                    if default_strategy(root) == "amat_group_selection_volatility.toml"
                    else "daily-core"
                    if default_strategy(root) == "amat_group_selection_daily.toml"
                    else "v6",
                },
                "start": {"type": "string", "default": "2019-01-01"},
                "end": {"type": "string", "default": "2024-12-31"},
                "initial_cash": {"type": "string", "default": "100000"},
            },
        },
    ]


def _money(value, field):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise HTTPException(422, f"{field} must be a decimal string")
    try:
        money = Decimal(str(value))
        if not money.is_finite() or not Decimal("1") <= money <= Decimal("1000000000"):
            raise InvalidOperation
    except InvalidOperation:
        raise HTTPException(422, f"{field} must be between 1 and 1000000000")
    return format(money, "f")


def command(service, action):
    if action.capability != "amat.backtest":
        return None
    root = Path(service.root).resolve()
    storage = Path(service.storage).resolve()
    cap = next(c for c in capabilities(root, storage) if c["id"] == action.capability)
    if not cap["supported"]:
        raise HTTPException(422, cap["reason"])
    inputs = action.inputs
    if set(inputs) - {"snapshot", "version", "start", "end", "initial_cash"}:
        raise HTTPException(422, "Unknown engine input fields")
    snapshot = inputs.get("snapshot", cap["inputs"]["snapshot"]["default"])
    if snapshot not in cap["inputs"]["snapshot"]["options"]:
        raise HTTPException(422, "Unknown registered snapshot")
    dataset = root / "amat/data/snapshots" / snapshot
    base = [str(root / "amat/.venv/bin/python"), "-m", "amat.cli.main"]
    fingerprint = hashlib.sha256(
        json.dumps({"capability": action.capability, "inputs": inputs}, sort_keys=True).encode()
    ).hexdigest()[:24]
    output = storage / "engine-artifacts" / fingerprint
    version = inputs.get(
        "version",
        "daily-volatility"
        if default_strategy(root) == "amat_group_selection_volatility.toml"
        else "daily-core"
        if default_strategy(root) == "amat_group_selection_daily.toml"
        else "v6",
    )
    if version not in {
        "daily-volatility",
        "daily-core",
        "daily-all-groups",
        "v1",
        "v4",
        "v5",
        "v6",
    }:
        raise HTTPException(422, "Unsupported historical strategy version")
    try:
        start = dt.date.fromisoformat(inputs.get("start", "2019-01-01"))
        end = dt.date.fromisoformat(inputs.get("end", "2024-12-31"))
        if (
            start > end
            or start < dt.date(1990, 1, 1)
            or end > dt.date.today()
            or (end - start).days > 30 * 366
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid historical range; use YYYY-MM-DD and at most 30 years")
    cash = _money(inputs.get("initial_cash", "100000"), "initial_cash")
    args = [
        "backtest",
        "--strategy-version",
        version,
        "--snapshot",
        str(dataset),
        "--start",
        start.isoformat(),
        "--end",
        end.isoformat(),
        "--initial-cash",
        cash,
        "--no-sleeves",
        "--out",
        str(output / "report.pdf"),
        "--run-root",
        str(output / "runs"),
        "--json",
    ]
    return base + args, root / "amat", cap
