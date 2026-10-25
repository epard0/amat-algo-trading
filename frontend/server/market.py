"""Read-only market views over registered parquet snapshots.

Prices use unadjusted provider OHLC consistently. These observations are frozen
historical data and never represent an entitled live quote or order-book feed.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path
from threading import RLock
from typing import Any

import polars as pl


class MarketError(ValueError):
    """Invalid query or unavailable snapshot; map to HTTP 422/404 in the API."""


class MarketData:
    def __init__(self, workspace: Path):
        self.root = (Path(workspace) / "amat/data/snapshots").resolve()
        self._cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._lock = RLock()

    def _path(self, snapshot: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", snapshot):
            raise MarketError("Invalid snapshot identifier")
        path = self.root / snapshot / "daily.parquet"
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise MarketError("Snapshot is not registered")
        return path

    @staticmethod
    def _date_column(schema: pl.Schema) -> str:
        for key in ("session_date", "session", "date", "timestamp"):
            if key in schema:
                return key
        raise MarketError("Snapshot has no supported session column")

    def _metadata(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        identity = (stat.st_mtime_ns, stat.st_size)
        key = str(path)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == identity:
                return dict(cached[1])
        frame = pl.scan_parquet(path)
        schema = frame.collect_schema()
        datecol = self._date_column(schema)
        symbolcol = "symbol" if "symbol" in schema else "ticker"
        if symbolcol not in schema:
            raise MarketError("Snapshot has no symbol column")
        info = (
            frame.select(
                pl.col(datecol).min().alias("first"),
                pl.col(datecol).max().alias("last"),
                pl.col(symbolcol).unique().sort().implode().alias("symbols"),
                pl.len().alias("rows"),
            )
            .collect()
            .to_dicts()[0]
        )
        manifest = {}
        manifest_path = path.parent / "manifest.json"
        try:
            if not manifest_path.is_symlink() and manifest_path.stat().st_size < 2_000_000:
                manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            pass
        metadata = {
            "id": path.parent.name,
            "source": "frozen local daily.parquet",
            "status": "historical",
            "live": False,
            "first_session": str(info["first"]) if info["first"] else None,
            "last_session": str(info["last"]) if info["last"] else None,
            "as_of": str(info["last"]) if info["last"] else None,
            "symbols": info["symbols"],
            "row_count": info["rows"],
            "manifest_as_of": manifest.get("as_of"),
            "manifest_last_session": manifest.get("last_session"),
            "provider": manifest.get("provider"),
            "feed": manifest.get("feed"),
            "price_basis": "raw unadjusted OHLC; adjusted_close is separate",
        }
        with self._lock:
            self._cache[key] = (identity, metadata)
        return dict(metadata)

    def snapshots(self) -> dict[str, Any]:
        items = []
        errors = []
        for candidate in self.root.glob("*/daily.parquet"):
            try:
                path = self._path(candidate.parent.name)
                items.append(self._metadata(path))
            except (MarketError, OSError, pl.exceptions.PolarsError) as exc:
                errors.append({"snapshot": candidate.parent.name, "reason": type(exc).__name__})
        items.sort(key=lambda row: (row["last_session"] or "", row["id"]), reverse=True)
        return {
            "items": items,
            "errors": errors,
            "default_snapshot": items[0]["id"] if items else None,
        }

    def _selected(self, snapshot: str | None) -> tuple[Path, dict[str, Any]]:
        if snapshot is None:
            snapshot = self.snapshots()["default_snapshot"]
        if snapshot is None:
            raise MarketError("No readable registered snapshot")
        path = self._path(snapshot)
        return path, self._metadata(path)

    def symbols(self, snapshot: str | None = None) -> dict[str, Any]:
        _, metadata = self._selected(snapshot)
        return {
            "items": metadata["symbols"],
            "snapshot": metadata["id"],
            "as_of": metadata["as_of"],
            "status": "historical",
            "live": False,
        }

    def _normalized(self, path: Path) -> pl.LazyFrame:
        frame = pl.scan_parquet(path)
        schema = frame.collect_schema()
        datecol = self._date_column(schema)
        expressions = [
            pl.col(datecol).cast(pl.Date).alias("date"),
            pl.col("symbol" if "symbol" in schema else "ticker").alias("symbol"),
        ]
        for key in ("open", "high", "low", "close", "volume"):
            source = "raw_" + key if "raw_" + key in schema else key
            if source not in schema:
                raise MarketError(f"Missing supported {key} column")
            expressions.append(pl.col(source).cast(pl.Float64).alias(key))
        return frame.select(expressions)

    @staticmethod
    def _number(value: Any) -> str | None:
        if value is None or not math.isfinite(float(value)):
            return None
        return format(float(value), ".15g")

    def bars(
        self,
        symbol: str = "SPY",
        snapshot: str | None = None,
        limit: int = 250,
        resolution: str = "1d",
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Z0-9.\-]{1,16}", symbol):
            raise MarketError("Invalid symbol")
        if resolution not in {"5m", "1d", "1w"} or not 1 <= limit <= 2000:
            raise MarketError("Resolution must be 5m, 1d or 1w; limit must be 1..2000")
        if resolution == "5m":
            return self.intraday(symbol, limit, start, end)
        path, meta = self._selected(snapshot)
        if symbol not in meta["symbols"]:
            raise MarketError("Symbol is absent from selected snapshot")
        frame = self._normalized(path).filter(pl.col("symbol") == symbol)
        try:
            first = dt.date.fromisoformat(start) if start else None
            last = dt.date.fromisoformat(end) if end else None
        except ValueError as exc:
            raise MarketError("Dates must use YYYY-MM-DD") from exc
        if first and last and first > last:
            raise MarketError("start must not follow end")
        if first:
            frame = frame.filter(pl.col("date") >= first)
        if last:
            frame = frame.filter(pl.col("date") <= last)
        frame = frame.sort("date")
        if resolution == "1w":
            frame = frame.group_by_dynamic("date", every="1w", period="1w", start_by="monday").agg(
                pl.col("symbol").first(),
                pl.col("open").first(),
                pl.col("high").max(),
                pl.col("low").min(),
                pl.col("close").last(),
                pl.col("volume").sum(),
            )
        rows = frame.tail(limit).collect().to_dicts()
        items = [
            {
                "time": str(row["date"]),
                "date": str(row["date"]),
                "symbol": symbol,
                **{
                    key: row[key] if row[key] is not None and math.isfinite(row[key]) else None
                    for key in ("open", "high", "low", "close", "volume")
                },
            }
            for row in rows
        ]
        warnings = []
        for item in items:
            reasons = []
            prices = [item[key] for key in ("open", "high", "low", "close")]
            if any(value is None or value <= 0 for value in prices):
                reasons.append("missing_nonfinite_or_nonpositive_price")
            else:
                opening, high, low, close = prices
                if high < max(opening, close) or low > min(opening, close) or high < low:
                    reasons.append("invalid_ohlc_ordering")
                if high / low > 1.5:
                    reasons.append("extreme_intraperiod_range")
            if item["volume"] is None or item["volume"] < 0:
                reasons.append("missing_nonfinite_or_negative_volume")
            if reasons:
                warnings.append(
                    {
                        "time": item["time"],
                        "symbol": symbol,
                        "kinds": reasons,
                        "observed": {
                            key: item[key] for key in ("open", "high", "low", "close", "volume")
                        },
                        "message": "Source OHLCV requires review; values are displayed without correction",
                    }
                )
        return {
            "items": items,
            "status": "historical",
            "snapshot": meta["id"],
            "symbol": symbol,
            "as_of": meta["as_of"],
            "source": meta["source"],
            "resolution": resolution,
            "live": False,
            "delay": "frozen historical data",
            "price_basis": meta["price_basis"],
            "range": {"start": start, "end": end},
            "quality_warnings": warnings,
            "quality_status": "review_required" if warnings else "no_detected_anomaly",
            "quality_scope": "Returned bars only; OHLC ordering, positive prices, nonnegative volume and high/low ratio >1.5. Not independent provider verification.",
        }

    def intraday(self, symbol, limit, start=None, end=None):
        """Aggregate registered minute files; daily snapshot selection never fabricates minutes."""
        root = self.root.parent.parent / "data/minute"
        paths = [
            p
            for p in root.glob(f"*/{symbol}-*.parquet")
            if not p.is_symlink() and p.resolve().is_relative_to(root.resolve())
        ]
        if not paths:
            raise MarketError("No minute history for this symbol. Select Daily or another symbol.")
        # A single registered provider dataset, selected by its newest available year/file.
        path = max(paths, key=lambda p: (p.stem.rsplit("-", 1)[-1], p.stat().st_mtime_ns))
        dataset = path.parent
        paths = sorted(p for p in paths if p.parent == dataset)
        try:
            first = dt.date.fromisoformat(start) if start else None
            last = dt.date.fromisoformat(end) if end else None
        except ValueError as exc:
            raise MarketError("Dates must use YYYY-MM-DD") from exc
        if first and last and first > last:
            raise MarketError("start must not follow end")
        if not first and not last:
            paths = [path]  # avoid scanning prior years for the default recent window
        frames = []
        for source in paths:
            frame = pl.scan_parquet(source)
            schema = frame.collect_schema()
            timecol = next((c for c in ("t", "timestamp", "time") if c in schema), None)
            if not timecol:
                raise MarketError("Minute source has no timestamp column")
            stamp = pl.col(timecol)
            if schema[timecol] == pl.String:
                stamp = stamp.str.to_datetime(time_zone="UTC")
            else:
                stamp = stamp.cast(pl.Datetime("us", "UTC"))
            expr = [stamp.alias("date")]
            for name, short in (
                ("open", "o"),
                ("high", "h"),
                ("low", "l"),
                ("close", "c"),
                ("volume", "v"),
            ):
                column = name if name in schema else short
                if column not in schema:
                    raise MarketError("Minute source has incomplete OHLCV columns")
                expr.append(pl.col(column).cast(pl.Float64).alias(name))
            frames.append(frame.select(expr))
        frame = pl.concat(frames).unique("date").sort("date")
        if first:
            frame = frame.filter(pl.col("date").dt.date() >= first)
        if last:
            frame = frame.filter(pl.col("date").dt.date() <= last)
        # Only completed 5-minute buckets from this historical observation window.
        latest = frame.select(pl.col("date").max()).collect().item()
        grouped = frame.group_by_dynamic("date", every="5m").agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
        )
        if latest:
            grouped = grouped.filter(
                pl.col("date") + dt.timedelta(minutes=5) <= latest + dt.timedelta(minutes=1)
            )
        rows = grouped.tail(limit).collect().to_dicts()
        items = [
            {
                "time": r["date"].isoformat(),
                "date": r["date"].isoformat(),
                "symbol": symbol,
                **{
                    k: r[k] if r[k] is not None and math.isfinite(r[k]) else None
                    for k in ("open", "high", "low", "close", "volume")
                },
            }
            for r in rows
        ]
        return {
            "items": items,
            "symbol": symbol,
            "resolution": "5m",
            "status": "historical",
            "live": False,
            "source": f"local minute history / {dataset.name}",
            "snapshot": dataset.name,
            "as_of": latest.isoformat() if latest else None,
            "price_basis": "provider minute OHLCV aggregated to completed 5-minute buckets",
            "delay": "frozen historical data",
            "range": {"start": start, "end": end},
        }

    def quotes(self, snapshot: str | None = None, symbols: str | None = None) -> dict[str, Any]:
        path, meta = self._selected(snapshot)
        selected = symbols.split(",") if symbols else meta["symbols"]
        if (symbols is not None and len(selected) > 200) or any(
            symbol not in meta["symbols"] for symbol in selected
        ):
            raise MarketError("Unknown symbol or more than 200 requested symbols")
        rows = (
            self._normalized(path)
            .filter(pl.col("symbol").is_in(selected))
            .sort("date")
            .group_by("symbol")
            .tail(2)
            .collect()
            .to_dicts()
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["symbol"], []).append(row)
        items = []
        for symbol in selected:
            observations = sorted(grouped.get(symbol, []), key=lambda row: row["date"])
            if not observations:
                continue
            latest = observations[-1]
            previous = observations[-2]["close"] if len(observations) > 1 else None
            close = latest["close"]
            change = close - previous if close is not None and previous is not None else None
            items.append(
                {
                    "symbol": symbol,
                    "price": self._number(close),
                    "close": self._number(close),
                    "previous_close": self._number(previous),
                    "change": self._number(change),
                    "change_percent": self._number(change / previous * 100)
                    if change is not None and previous
                    else None,
                    "volume": self._number(latest["volume"]),
                    "as_of": str(latest["date"]),
                    "status": "historical",
                    "live": False,
                    "bid": None,
                    "ask": None,
                    "change_basis": "raw close versus preceding available session; corporate actions may affect change",
                }
            )
        return {
            "items": items,
            "status": "historical",
            "snapshot": meta["id"],
            "as_of": meta["as_of"],
            "source": meta["source"],
            "live": False,
            "delay": "frozen historical session closes",
            "price_basis": meta["price_basis"],
        }
