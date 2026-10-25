"""Verified, regular-grid candle inputs for research-only genetic discovery."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl

from amat.data.snapshot import load_panel
from autoresearch.calendar_audit import audit_sessions
from autoresearch.labels import adjusted_window_prices

DATA_VERSION = "genetic-candle-data/1"
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
FIELDS = ("open", "high", "low", "close")
ARRAY_DIMENSIONS = 2
MAX_ONE_WAY_BPS = 100


def digest_json(value: Any) -> str:
    """Hash JSON with no nonfinite values or unstable whitespace."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class CandleDataset:
    """All observations are within a declared training interval, inclusive."""

    timestamps: tuple[str, ...]
    symbols: tuple[str, ...]
    fields: dict[str, np.ndarray]
    execution_prices: np.ndarray
    timeframe: Literal["1d", "1h"]
    price_basis: Literal["corporate_action_adjusted", "spot_raw"]
    manifest: dict[str, Any]

    @property
    def digest(self) -> str:
        arrays = {**self.fields, "execution_prices": self.execution_prices}
        content = {}
        for name, value in arrays.items():
            canonical = np.array(value, dtype="<f8", order="C", copy=True)
            canonical[np.isnan(canonical)] = np.nan
            content[name] = {
                "shape": list(canonical.shape),
                "sha256": hashlib.sha256(canonical.tobytes()).hexdigest(),
            }
        return digest_json(
            {
                "manifest": self.manifest,
                "timestamps": self.timestamps,
                "symbols": self.symbols,
                "timeframe": self.timeframe,
                "price_basis": self.price_basis,
                "arrays": content,
            }
        )


def bar_validity(fields: Mapping[str, np.ndarray]) -> np.ndarray:
    """No synthetic bars: every OHLC price must be finite, positive and coherent."""
    arrays = [fields[name] for name in FIELDS]
    shape = arrays[0].shape
    if len(shape) != ARRAY_DIMENSIONS or any(values.shape != shape for values in arrays):
        raise ValueError("OHLC inputs require matching time-by-symbol arrays")
    o, h, low, c = arrays
    valid = np.logical_and.reduce([np.isfinite(x) & (x > 0) for x in arrays])
    return np.asarray(valid & (low <= np.minimum(o, c)) & (h >= np.maximum(o, c)))


def _check_interval(start: dt.date, end: dt.date, symbols: tuple[str, ...]) -> None:
    if start >= end:
        raise ValueError("training start must precede training end")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be nonempty and unique")


def load_daily(
    directory: Path, symbols: tuple[str, ...], *, start: dt.date, end: dt.date
) -> CandleDataset:
    """Use the existing verified snapshot loader and independent exchange calendar."""
    _check_interval(start, end, symbols)
    panel, snapshot = load_panel(directory, symbols, start=start, end=end, with_context=True)
    calendar = audit_sessions(list(panel.sessions), start=start, end=end)
    opens, highs, lows, closes = panel.candle_inputs()
    fields = dict(zip(FIELDS, (opens, highs, lows, closes), strict=True))
    prices = adjusted_window_prices(panel)
    valid = bar_validity(fields)
    manifest = {
        "version": DATA_VERSION,
        "source": "amat_snapshot",
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_manifest": snapshot.model_dump(mode="json"),
        "partitions": snapshot.partition_sha256,
        "symbols": list(symbols),
        "training_start": start.isoformat(),
        "training_end": end.isoformat(),
        "rows": len(panel.sessions),
        "invalid_candle_cells": int((~valid).sum()),
        "unavailable_execution_cells": int((~np.isfinite(prices)).sum()),
        "calendar_sha256": digest_json(calendar),
        "execution": "next-session opening-window VWAP on adjusted research basis",
        "limitations": "event-return proxy; not portfolio cash/dividend/fill accounting",
    }
    return CandleDataset(
        tuple(day.isoformat() for day in panel.sessions),
        symbols,
        fields,
        prices,
        "1d",
        "corporate_action_adjusted",
        manifest,
    )


def _verified_csv(directory: Path, record: dict[str, Any]) -> pl.DataFrame:
    name = record["file"]
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("dataset manifest must reference a local filename")
    path = directory / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError(f"dataset checksum mismatch: {name}")
    frame = pl.read_csv(path)
    if frame.height != record["rows"]:
        raise ValueError(f"dataset row count mismatch: {name}")
    if frame["open_time_ms"].n_unique() != frame.height:
        raise ValueError(f"duplicate candle opening timestamps: {name}")
    return frame


def load_crypto(
    directory: Path, symbols: tuple[str, ...], *, start: dt.date, end: dt.date
) -> CandleDataset:
    """Verify merged CSVs, preserve every UTC hour, mask irregular/zero-volume bars."""
    _check_interval(start, end, symbols)
    source = json.loads((directory / "manifest.json").read_text())
    if source["interval"] != "1h" or source["timestamp_unit"] != "milliseconds":
        raise ValueError("only hourly millisecond crypto manifests are supported")
    if source["timezone"] != "UTC":
        raise ValueError("crypto manifest must use UTC")
    if start < dt.date.fromisoformat(source["start_inclusive"]) or end >= dt.date.fromisoformat(
        source["end_exclusive"]
    ):
        raise ValueError("training interval is outside the verified dataset")
    records = {record["symbol"]: record for record in source["datasets"]}
    if not set(symbols).issubset(records):
        raise ValueError("requested symbol is absent from crypto manifest")
    begin_ms = int(dt.datetime.combine(start, dt.time(), dt.UTC).timestamp() * 1000)
    stop_ms = int(dt.datetime.combine(end, dt.time(), dt.UTC).timestamp() * 1000) + DAY_MS
    grid = np.arange(begin_ms, stop_ms, HOUR_MS, dtype=np.int64)
    shape = (len(grid), len(symbols))
    fields = {name: np.full(shape, np.nan) for name in FIELDS}
    counts = {}
    for col, symbol in enumerate(symbols):
        frame = _verified_csv(directory, records[symbol]).filter(
            (pl.col("open_time_ms") >= begin_ms) & (pl.col("open_time_ms") < stop_ms)
        )
        opens = frame["open_time_ms"].to_numpy()
        closes = frame["close_time_ms"].to_numpy()
        volume = frame["volume"].to_numpy()
        aligned = (opens - begin_ms) % HOUR_MS == 0
        valid = aligned & (closes == opens + HOUR_MS - 1) & np.isfinite(volume) & (volume > 0)
        for field in FIELDS:
            values = frame[field].to_numpy()
            fields[field][((opens[valid] - begin_ms) // HOUR_MS).astype(int), col] = values[valid]
        counts[symbol] = {
            "source_rows_in_interval": frame.height,
            "off_grid_open_rows": int((~aligned).sum()),
            "irregular_close_rows": int((closes != opens + HOUR_MS - 1).sum()),
            "nonpositive_or_missing_volume_rows": int(
                (~(np.isfinite(volume) & (volume > 0))).sum()
            ),
        }
    valid = bar_validity(fields)
    for field in FIELDS:
        fields[field][~valid] = np.nan
    manifest = {
        "version": DATA_VERSION,
        "source": "binance_spot_hourly",
        "csv_sha256": {symbol: records[symbol]["sha256"] for symbol in symbols},
        "symbols": list(symbols),
        "training_start": start.isoformat(),
        "training_end": end.isoformat(),
        "grid_rows": len(grid),
        "quality": counts,
        "invalid_candle_cells": int((~valid).sum()),
        "gap_policy": "retain UTC grid; unavailable; no imputation",
        "execution": "next-bar open proxy; no quote or order-book simulation",
        "limitations": "spot long-only research proxy; no borrow/funding; no actual fills",
    }
    timestamps = tuple(dt.datetime.fromtimestamp(int(x) / 1000, dt.UTC).isoformat() for x in grid)
    return CandleDataset(
        timestamps, symbols, fields, fields["open"].copy(), "1h", "spot_raw", manifest
    )


def training_labels(
    dataset: CandleDataset, *, max_lag: int, one_way_bps: float
) -> tuple[np.ndarray, np.ndarray]:
    """One-bar event: decision t, entry t+1, exit t+2; all mature within train.

    A common max-lag window masks every contender identically. Any invalid candle
    between t-max_lag and t+2 makes the label unavailable. Labels are research
    diagnostics; adjacent events pay separate round trips, not netted turnover.
    """
    if max_lag < 0 or not np.isfinite(one_way_bps) or not 0 <= one_way_bps <= MAX_ONE_WAY_BPS:
        raise ValueError("invalid lag or one-way cost")
    valid = bar_validity(dataset.fields)
    prices = dataset.execution_prices
    if prices.shape != valid.shape:
        raise ValueError("execution observations must align with candles")
    valid &= np.isfinite(prices) & (prices > 0)
    labels = np.full(valid.shape, np.nan)
    support = np.zeros(valid.shape, dtype=bool)
    delay_and_horizon = 2
    cost = one_way_bps / 10_000
    for row in range(max_lag, len(prices) - delay_and_horizon):
        usable = valid[row - max_lag : row + delay_and_horizon + 1].all(axis=0)
        support[row] = usable
        labels[row, usable] = (
            prices[row + delay_and_horizon, usable]
            / prices[row + 1, usable]
            * (1 - cost)
            / (1 + cost)
            - 1
        )
    return labels, support
