"""Retain exact vendor repeats without admitting conflicting corporate actions."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import polars as pl


def test_exact_event_repeats_collapse_and_conflicting_rates_quarantine(tmp_path: Path) -> None:
    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/build_screening_dataset.py")
    )
    batch = tmp_path / "batch-000"
    batch.mkdir()
    pl.DataFrame(
        {"symbol": ["QQQ", "SPY", "BAD"], "session_date": ["2026-01-02"] * 3}
    ).write_parquet(batch / "daily.parquet")
    pl.DataFrame(
        {
            "symbol": ["QQQ", "QQQ", "BAD", "BAD"],
            "ex_date": ["2026-01-02"] * 4,
            "kind": ["cash_dividend"] * 4,
            "value": [0.5, 0.5, 0.5, 0.7],
        }
    ).write_parquet(batch / "actions.parquet")
    (batch / "complete.json").write_text(
        json.dumps([{"symbol": s, "status": "accepted"} for s in ["QQQ", "SPY", "BAD"]])
    )
    daily, actions, audit = module["_validated_batches"](tmp_path)
    assert set(daily["symbol"]) == {"QQQ", "SPY"}
    assert actions["symbol"].to_list() == ["QQQ"]
    assert next(row for row in audit if row["symbol"] == "BAD")["status"] == "quarantined"
    assert next(row for row in audit if row["symbol"] == "QQQ")["status"] == "accepted"
