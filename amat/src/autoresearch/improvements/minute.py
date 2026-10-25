"""Event-time raw minute ingestion and prefix-only, corporate-action-safe features."""

from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from autoresearch.improvements.minute_data import audit_archive

FEATURE_WARMUP = 21


@dataclass(frozen=True)
class MinuteBar:
    symbol: str
    start: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    available: dt.datetime
    revision: int = 0

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.available.tzinfo is None:
            raise ValueError("minute timestamps must be aware")
        if self.start.second or self.start.microsecond:
            raise ValueError("minute starts must be aligned")
        if self.available < self.start + dt.timedelta(minutes=1):
            raise ValueError("uncompleted minute is unavailable")
        values = np.array([self.open, self.high, self.low, self.close, self.volume])
        if not np.isfinite(values).all() or min(values[:4]) <= 0 or self.volume < 0:
            raise ValueError("invalid raw minute")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("incoherent OHLC")


class FeatureStream:
    """Admission follows arrival time; a later missing bar cannot revoke the past."""

    def __init__(self, symbols: tuple[str, ...], window: int = 21) -> None:
        if not symbols or len(set(symbols)) != len(symbols) or window < FEATURE_WARMUP:
            raise ValueError("features require an ordered universe and at least 21 bars")
        self.symbols = symbols
        self.closes: dict[str, deque[float]] = {s: deque(maxlen=window) for s in symbols}
        self.volumes: dict[str, deque[float]] = {s: deque(maxlen=window) for s in symbols}
        self.last: dict[str, MinuteBar] = {}
        self.duplicates = 0
        self.revisions = 0
        self.late = 0
        self.invalidated: set[str] = set()

    def split(self, symbol: str, ratio: float) -> None:
        if not np.isfinite(ratio) or ratio <= 0:
            raise ValueError("split ratio must be finite and positive")
        self.closes[symbol] = deque(
            (v / ratio for v in self.closes[symbol]), maxlen=self.closes[symbol].maxlen
        )
        self.volumes[symbol] = deque(
            (v * ratio for v in self.volumes[symbol]), maxlen=self.volumes[symbol].maxlen
        )

    def admit(self, bar: MinuteBar, watermark: dt.datetime) -> bool:
        if bar.symbol not in self.closes or bar.available > watermark:
            raise ValueError("unavailable or unauthorized minute")
        previous = self.last.get(bar.symbol)
        if previous and bar.start <= previous.start:
            if bar.start == previous.start and bar == previous:
                self.duplicates += 1
            elif bar.start == previous.start and bar.revision > previous.revision:
                self.revisions += 1
                # Rebuilding the prefix is explicit; do not trade on a mixture
                # of revised and stale feature recursion or count another minute.
                self.invalidated.add(bar.symbol)
            else:
                self.late += 1
            return False
        if previous and bar.start - previous.start != dt.timedelta(minutes=1):
            # Restart feature warm-up after a gap (also across session boundaries).
            self.closes[bar.symbol].clear()
            self.volumes[bar.symbol].clear()
        self.last[bar.symbol] = bar
        self.closes[bar.symbol].append(bar.close)
        self.volumes[bar.symbol].append(bar.volume)
        return True

    def values(self, symbol: str, expected_start: dt.datetime) -> np.ndarray | None:
        previous = self.last.get(symbol)
        closes = np.array(self.closes[symbol])
        if (
            symbol in self.invalidated
            or previous is None
            or previous.start != expected_start
            or len(closes) < FEATURE_WARMUP
            or previous.volume <= 0
        ):
            return None
        returns = closes[1:] / closes[:-1] - 1
        sigma = max(float(returns.std()), 1e-6)
        volume = np.array(self.volumes[symbol])
        width = max(float(closes.max() - closes.min()), 1e-8)
        result = np.array(
            [
                (closes[-1] / closes[-6] - 1) / (sigma * np.sqrt(5)),
                (closes[-1] / closes[0] - 1) / (sigma * np.sqrt(20)),
                2 * (closes[-1] - closes.min()) / width - 1,
                volume[-1] / max(float(volume[:-1].mean()), 1) - 1,
            ]
        )
        return np.tanh(np.clip(result, -10, 10))


@dataclass(frozen=True)
class MinuteBatch:
    start: dt.datetime
    close_at: dt.datetime
    bars: tuple[MinuteBar, ...]
    session_close: dt.datetime


def iter_batches(  # noqa: PLR0912 - calendar/partition traversal
    archive: Path,
    symbols: tuple[str, ...],
    start: dt.date,
    end: dt.date,
    *,
    latency_seconds: int = 1,
) -> Any:
    """Preserve every exchange minute, including absent observations and half days."""
    import json  # noqa: PLC0415

    import exchange_calendars as xcals  # type: ignore[import-untyped] # noqa: PLC0415

    if latency_seconds < 0:
        raise ValueError("negative observation latency")
    audit = audit_archive(archive, symbols)
    if audit["missing_symbols"]:
        raise ValueError("raw minute universe is incomplete")
    manifest = json.loads((archive / "manifest.json").read_text())
    calendar = xcals.get_calendar(
        "XNYS", start=str(start - dt.timedelta(days=2)), end=str(end + dt.timedelta(days=2))
    )
    schedule = calendar.schedule.loc[str(start) : str(end)]
    # Scan one calendar year at a time. Do not convert the whole archive to
    # Python objects or inspect later completeness to make eligibility decisions.
    for year in range(start.year, end.year + 1):
        scans = []
        for name in manifest["partitions"]:
            symbol = name.split("-")[0]
            if symbol not in symbols or f"-{year}" not in name:
                continue
            scans.append(
                pl.scan_parquet(archive / name).select(
                    pl.col("t"),
                    pl.col("o"),
                    pl.col("h"),
                    pl.col("l"),
                    pl.col("c"),
                    pl.col("v"),
                    pl.lit(symbol).alias("symbol"),
                )
            )
        if not scans:
            continue
        data = (
            pl.concat(scans)
            .filter(
                (pl.col("t").str.slice(0, 4) == str(year))
                & pl.col("t").str.slice(0, 10).is_between(pl.lit(str(start)), pl.lit(str(end)))
            )
            .collect()
        )
        data = data.with_columns(pl.col("t").str.slice(0, 10).alias("date"))
        days = data.partition_by("date", as_dict=True)
        for session, times in schedule.iterrows():
            if session.year != year:
                continue
            opening, closing = times["open"].to_pydatetime(), times["close"].to_pydatetime()
            frame = days.get((str(session.date()),))
            records: dict[dt.datetime, list[MinuteBar]] = {}
            if frame is not None:
                for row in frame.iter_rows(named=True):
                    stamp = dt.datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
                    if not opening <= stamp < closing:
                        continue
                    bar = MinuteBar(
                        row["symbol"],
                        stamp,
                        row["o"],
                        row["h"],
                        row["l"],
                        row["c"],
                        row["v"],
                        stamp + dt.timedelta(minutes=1, seconds=latency_seconds),
                    )
                    records.setdefault(stamp, []).append(bar)
            stamp = opening
            while stamp < closing:
                bars = tuple(sorted(records.get(stamp, []), key=lambda b: b.symbol))
                if len({b.symbol for b in bars}) != len(bars):
                    raise ValueError("duplicate raw minute across partitions")
                yield MinuteBatch(
                    stamp, stamp + dt.timedelta(minutes=1, seconds=latency_seconds), bars, closing
                )
                stamp += dt.timedelta(minutes=1)
