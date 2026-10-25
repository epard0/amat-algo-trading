"""Lossless materialization of the original chronological minute feature stream."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np


class FeatureCache:
    """Source/data-bound cached prefixes; absence remains an explicit abstention."""

    def __init__(self, folder: Path, symbols: tuple[str, ...], partitions: dict[str, str]) -> None:
        self.folder = folder
        manifest = json.loads((folder / "manifest.json").read_text())
        if tuple(manifest["symbols"]) != symbols or manifest["minute_partitions"] != partitions:
            raise ValueError("feature cache universe or inputs changed")
        for name, sha in manifest["files"].items():
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != sha:
                raise ValueError("feature cache file changed")
        source = Path(__file__).with_name("minute.py")
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["feature_source_sha256"]:
            raise ValueError("feature implementation changed")
        self.symbols = symbols

    @lru_cache(maxsize=2)  # noqa: B019 - research cache object lives for one job
    def _year(self, year: int) -> tuple[np.ndarray, np.ndarray]:
        return (
            np.asarray(
                np.load(self.folder / f"{year}-stamps.npy", mmap_mode="r", allow_pickle=False)
            ),
            np.asarray(
                np.load(self.folder / f"{year}-values.npy", mmap_mode="r", allow_pickle=False)
            ),
        )

    def values(self, stamp: dt.datetime) -> dict[str, np.ndarray | None]:
        stamps, values = self._year(stamp.year)
        key = int(stamp.timestamp())
        index = int(np.searchsorted(stamps, key))
        if index >= len(stamps) or int(stamps[index]) != key:
            raise ValueError("feature cache missing exchange minute")
        row = values[index]
        return {
            symbol: row[i] if np.isfinite(row[i]).all() else None
            for i, symbol in enumerate(self.symbols)
        }
