"""Numeric raw-bar cache with bulk validation equivalent to MinuteBar admission."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import exchange_calendars as xcals  # type: ignore[import-untyped]
import numpy as np

from autoresearch.improvements.minute import MinuteBar, MinuteBatch

FIELDS = ("open", "high", "low", "close", "volume")


class RawMinuteCache:
    """Only verified numeric arrays; never deserialize executable object formats."""

    def __init__(self, folder: Path, symbols: tuple[str, ...], partitions: dict[str, str]) -> None:
        self.folder = folder
        manifest = json.loads((folder / "manifest.json").read_text())
        if tuple(manifest["symbols"]) != symbols or manifest["minute_partitions"] != partitions:
            raise ValueError("raw cache universe or inputs changed")
        for name, sha in manifest["files"].items():
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != sha:
                raise ValueError("raw cache file changed")
        if (
            manifest.get("schema") != "verified-raw-minute-cache/1"
            or tuple(manifest.get("columns", ())) != FIELDS
        ):
            raise ValueError("raw cache schema changed")
        self.ordered = sorted(enumerate(symbols), key=lambda item: item[1])
        self.symbols = symbols

    @lru_cache(maxsize=2)  # noqa: B019 - job-scoped immutable cache
    def _year(self, year: int) -> tuple[np.ndarray, np.ndarray]:
        stamps = np.asarray(
            np.load(self.folder / f"{year}-stamps.npy", mmap_mode="r", allow_pickle=False)
        )
        bars = np.asarray(
            np.load(self.folder / f"{year}-bars.npy", mmap_mode="r", allow_pickle=False)
        )
        if stamps.dtype != np.int64 or bars.dtype != np.float64:
            raise ValueError("raw cache numeric types changed")
        if bars.shape != (len(stamps), len(self.symbols), len(FIELDS)):
            raise ValueError("raw cache shape changed")
        if np.any(stamps % 60) or np.any(np.diff(stamps) <= 0):
            raise ValueError("raw cache timestamps invalid")
        missing = np.isnan(bars).all(axis=-1)
        observed = bars[~missing]
        if (
            not np.isfinite(observed).all()
            or np.any(observed[:, :4] <= 0)
            or np.any(observed[:, 4] < 0)
            or np.any(observed[:, 4] > 2**53)
            or np.any(observed[:, 4] != np.floor(observed[:, 4]))
            or np.any(observed[:, 2] > np.minimum(observed[:, 0], observed[:, 3]))
            or np.any(observed[:, 1] < np.maximum(observed[:, 0], observed[:, 3]))
        ):
            raise ValueError("raw cache OHLCV invalid")
        return stamps, bars

    def batches(self, start: dt.date, end: dt.date) -> Iterator[MinuteBatch]:
        calendar = xcals.get_calendar(
            "XNYS", start=str(start - dt.timedelta(days=2)), end=str(end + dt.timedelta(days=2))
        )
        for session, times in calendar.schedule.loc[str(start) : str(end)].iterrows():
            stamps, data = self._year(session.year)
            opening = times["open"].to_pydatetime()
            closing = times["close"].to_pydatetime()
            first = int(np.searchsorted(stamps, int(opening.timestamp())))
            count = int((closing - opening).total_seconds()) // 60
            for index in range(first, first + count):
                stamp = dt.datetime.fromtimestamp(int(stamps[index]), dt.UTC)
                if stamp != opening + dt.timedelta(minutes=index - first):
                    raise ValueError("raw cache exchange grid changed")
                available = stamp + dt.timedelta(minutes=1, seconds=1)
                bars = []
                for col, symbol in self.ordered:
                    row = data[index, col]
                    if not math.isfinite(float(row[3])):
                        continue
                    values = row.tolist()
                    # Same immutable class/fields as the original reader. All
                    # constructor invariants were checked in bulk above.
                    bar = object.__new__(MinuteBar)
                    bar.__dict__.update(
                        symbol=symbol,
                        start=stamp,
                        open=values[0],
                        high=values[1],
                        low=values[2],
                        close=values[3],
                        volume=int(values[4]),
                        available=available,
                        revision=0,
                    )
                    bars.append(bar)
                yield MinuteBatch(stamp, available, tuple(bars), closing)
