"""Shared metadata-only attempt ledger. Unknown prices/usage are never zero cost."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class CostLedger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(
            os.getenv("AMAT_COST_LEDGER", str(Path.home() / ".local/state/amat/costs.sqlite3"))
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, created_at TEXT, "
                "category TEXT, provider TEXT, model TEXT, status TEXT, usage TEXT)"
            )

    def reserve(self, *, category: str, provider: str, model: str = "") -> int:
        if category not in {"model", "search", "market_data"}:
            raise ValueError("unknown cost category")
        with sqlite3.connect(self.path, timeout=30) as db:
            cursor = db.execute(
                "INSERT INTO attempts(created_at,category,provider,model,status,usage) "
                "VALUES(?,?,?,?,?,?)",
                (dt.datetime.now(dt.UTC).isoformat(), category, provider, model, "pending", "{}"),
            )
            if cursor.lastrowid is None:
                raise ValueError("cost reservation failed")
            return cursor.lastrowid

    def settle(self, identity: int, *, status: str, usage: dict[str, Any]) -> None:
        allowed = {
            "input_tokens",
            "output_tokens",
            "served_model",
            "elapsed_seconds",
            "requests",
            "usd",
            "pricing_source",
        }
        if usage.keys() - allowed:
            raise ValueError("cost ledger accepts metadata only")
        with sqlite3.connect(self.path, timeout=30) as db:
            changed = db.execute(
                "UPDATE attempts SET status=?,usage=? WHERE id=? AND status='pending'",
                (status, json.dumps(usage, allow_nan=False), identity),
            ).rowcount
            if changed != 1:
                raise ValueError("unknown or already-settled cost attempt")
