"""Read-only SIP minute ingestion with atomic, hash-verified cached partitions."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from amat.data.alpaca import AlpacaMarketData
from autoresearch.workspace import digest_file, write_json

SYMBOLS = ("SPY", "QQQ", "IWM", "GLD")


def fetch(root: Path, start: dt.date, end: dt.date) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    api = AlpacaMarketData(feed="sip")
    partitions = {}
    for year in range(start.year, end.year + 1):
        for symbol in SYMBOLS:
            first, last = max(start, dt.date(year, 1, 1)), min(end, dt.date(year, 12, 31))
            if first > last:
                continue
            path = root / f"{symbol}-{year}.parquet"
            meta = path.with_suffix(".json")
            request = {
                "symbol": symbol,
                "start": str(first),
                "end": str(last),
                "feed": "sip",
                "adjustment": "raw",
                "timeframe": "1Min",
            }
            if meta.exists():
                saved = json.loads(meta.read_text())
                if saved["request"] != request or digest_file(path) != saved["sha256"]:
                    raise ValueError("minute cache differs from recorded request/hash")
            else:
                records = []
                for page in api._paginate(
                    "/v2/stocks/bars",
                    {
                        "symbols": symbol,
                        "timeframe": "1Min",
                        "feed": "sip",
                        "adjustment": "raw",
                        "start": str(first) + "T00:00:00Z",
                        "end": str(last) + "T23:59:59Z",
                        "limit": 10000,
                        "sort": "asc",
                    },
                ):
                    records.extend(page.get("bars", {}).get(symbol, []))
                if not records:
                    raise ValueError(f"no minute data for {symbol} {year}")
                frame = pl.DataFrame(records).sort("t")
                temp = path.with_suffix(".tmp")
                frame.write_parquet(temp)
                temp.replace(path)
                write_json(
                    meta, {"request": request, "rows": len(frame), "sha256": digest_file(path)}
                )
            partitions[path.name] = digest_file(path)
    manifest = {
        "schema": "intraday-data/1",
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "raw",
        "bar_minutes": 1,
        "symbols": SYMBOLS,
        "start": str(start),
        "end": str(end),
        "partitions": partitions,
        "timestamp_semantics": "bar start; close available one minute later",
    }
    write_json(root / "manifest.json", manifest)
    return manifest


def freeze_calendar(target: Path, source: Path) -> None:
    """Use the existing daily dataset's session calendar to expose wholly missing days."""
    daily = source / "data/snapshots/alpaca-sip-2026-08-11/daily.parquet"
    frame = pl.read_parquet(daily).filter(pl.col("symbol") == "SPY")
    dates = sorted(frame["session_date"].cast(pl.String).unique().to_list())
    write_json(
        target / "calendar.json",
        {
            "dates": dates,
            "source": "existing daily SPY session calendar",
            "source_sha256": digest_file(daily),
        },
    )


class Panel:
    """Regular-session dense data; incomplete asset-days cannot trade and are audited."""

    def __init__(self, root: Path, *, start: str, end: str) -> None:
        manifest = json.loads((root / "manifest.json").read_text())
        self.symbols = tuple(manifest["symbols"])
        frames = []
        for name, expected in manifest["partitions"].items():
            path = root / name
            if not path.resolve().is_relative_to(root.resolve()) or digest_file(path) != expected:
                raise ValueError("minute data hash mismatch")
            frame = pl.read_parquet(path).with_columns(pl.lit(name.split("-")[0]).alias("symbol"))
            frames.append(frame)
        frame = (
            pl.concat(frames, how="diagonal_relaxed")
            .with_columns(
                pl.col("t")
                .str.to_datetime(time_zone="UTC")
                .dt.convert_time_zone("America/New_York")
                .alias("local")
            )
            .with_columns(
                pl.col("local").dt.date().cast(pl.String).alias("date"),
                (
                    pl.col("local").dt.hour().cast(pl.Int32) * 60
                    + pl.col("local").dt.minute().cast(pl.Int32)
                    - 570
                ).alias("minute"),
            )
            .filter(
                (pl.col("date") >= start)
                & (pl.col("date") <= end)
                & pl.col("minute").is_between(0, 389)
            )
        )
        if frame.is_empty():
            raise ValueError("no regular-session minute bars in requested interval")
        if frame.select(pl.struct("symbol", "date", "minute").is_duplicated().any()).item():
            raise ValueError("duplicate minute bars")
        observed = sorted(frame["date"].unique().to_list())
        calendar_path = root / "calendar.json"
        self.dates = (
            [
                date
                for date in json.loads(calendar_path.read_text())["dates"]
                if start <= date <= end
            ]
            if calendar_path.is_file()
            else observed
        )
        if not set(observed).issubset(self.dates):
            raise ValueError("minute bars outside the frozen exchange session calendar")
        lookup = {value: i for i, value in enumerate(self.dates)}
        self.values = np.full((len(self.dates), 390, len(self.symbols), 5), np.nan)
        for a, symbol in enumerate(self.symbols):
            rows = frame.filter(pl.col("symbol") == symbol)
            d = np.array([lookup[value] for value in rows["date"].to_list()])
            self.values[d, rows["minute"].to_numpy(), a] = rows.select(
                "o", "h", "l", "c", "v"
            ).to_numpy()
        finite = np.isfinite(self.values).all(axis=(1, 3))
        o, h, low, c, v = (self.values[:, :, :, i] for i in range(5))
        valid = (
            (o > 0)
            & (c > 0)
            & (h >= np.maximum(o, c))
            & (low <= np.minimum(o, c))
            & (low > 0)
            & (v > 0)
        ).all(axis=1)
        self.valid = finite & valid
        self.audit = {
            "sessions": len(self.dates),
            "symbols": self.symbols,
            "first": self.dates[0],
            "last": self.dates[-1],
            "complete_asset_days": int(self.valid.sum()),
            "excluded_asset_days": int((~self.valid).sum()),
            "coverage": float(self.valid.mean()),
            "policy": (
                "Incomplete/early-close asset-days abstain; no price imputation. All da"
                "tes remain in returns."
            ),
            "excluded": [
                {"date": date, "symbol": symbol}
                for d, date in enumerate(self.dates)
                for a, symbol in enumerate(self.symbols)
                if not self.valid[d, a]
            ],
        }
        self.cache: dict[str, np.ndarray] = {}
