"""Versioned process bridge to the frozen primary BTC/ETH crypto perps engine."""

import hashlib
import itertools
import json
import os
import subprocess
from pathlib import Path

from .domain import HOUR, dec, digest, timestamp
from .profiles import StrategyProfile

PRIMARY = {"timeframe": 4, "lookback": 20, "exit_ratio": 2, "sizing": "vol20", "leverage": 2}
SYMBOLS = ("KXBTCPERP", "KXETHPERP")


class HistoryUnavailable(ValueError):
    """Observed input cannot yet support the frozen strategy's warm-up."""


class CryptoStrategy:
    def __init__(self, amat_root, python, source_sha256):
        self.root, self.python = Path(amat_root).resolve(), Path(os.path.abspath(python))
        self.source_sha256 = source_sha256
        self.symbols = SYMBOLS
        self.profile = None
        if len(source_sha256) != 64:
            raise ValueError("exact primary strategy source hash required")
        source = self.root / "src/autoresearch/crypto_perpetual_study.py"
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha256:
            raise ValueError("primary crypto strategy source hash mismatch")
        self.identity = self.policy_identity(source_sha256)

    @staticmethod
    def policy_identity(source_sha256):
        return digest(
            {
                "engine": "autoresearch.crypto_perpetual_study.strategy_weights",
                "config": PRIMARY,
                "source": source_sha256,
                "symbols": SYMBOLS,
                "sleeve_weights": ["0.5", "0.5"],
            }
        )

    def decide(self, candles, as_of_ms):
        timestamp(as_of_ms)
        if as_of_ms % HOUR or set(candles) != set(self.symbols):
            raise ValueError(
                "strategy requires its configured instruments and an hourly decision boundary"
            )
        grids = []
        clean = {}
        for ticker in self.symbols:
            records = candles[ticker]
            times = [timestamp(r["timestamp_ms"]) for r in records]
            if (
                not times
                or times[-1] != as_of_ms - HOUR
                or any(t % HOUR or t >= as_of_ms for t in times)
                or any(b - a != HOUR for a, b in itertools.pairwise(times))
            ):
                raise HistoryUnavailable("completed contiguous hourly candles required")
            for row in records:
                o, hi, lo, c = (dec(row[k]) for k in ("open", "high", "low", "close"))
                if (
                    lo <= 0
                    or lo > min(o, c)
                    or hi < max(o, c)
                    or dec(row["tradable"]) not in (0, 1)
                ):
                    raise ValueError("invalid candle")
            grids.append(times)
            clean[ticker] = [
                {k: r[k] for k in ("timestamp_ms", "open", "high", "low", "close", "tradable")}
                for r in records
            ]
        if any(grid != grids[0] for grid in grids[1:]):
            raise ValueError("primary strategy candle grids differ")
        request = {
            "version": "crypto-perps-decision/1",
            "amat_root": str(self.root),
            "source_sha256": self.source_sha256,
            "config": PRIMARY,
            "as_of_ms": as_of_ms,
            "candles": clean,
        }
        if self.profile is not None:
            request["profile"] = self.profile.payload()
        request["request_id"] = digest(request)
        worker = Path(__file__).with_name("strategy_worker.py")
        completed = subprocess.run(
            [str(self.python), str(worker)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
            cwd=self.root,
        )
        if completed.returncode:
            raise RuntimeError("primary strategy process failed; no new orders admitted")
        result = json.loads(completed.stdout)
        if (
            result["version"] != request["version"]
            or result["request_id"] != request["request_id"]
            or result["source_sha256"] != self.source_sha256
            or result["as_of_ms"] != as_of_ms
            or type(result["ready"]) is not bool
        ):
            raise ValueError("strategy response identity mismatch")
        if result["ready"]:
            if set(result["weights"]) != set(self.symbols):
                raise ValueError("strategy response symbols mismatch")
            if any(abs(dec(v)) > 2 for v in result["weights"].values()):
                raise ValueError("strategy weights outside frozen policy bounds")
        result["policy_hash"] = self.identity
        result["input_hash"] = request["request_id"]
        return result


class ProfileStrategy(CryptoStrategy):
    """Clean strategy API: immutable profile + decide(completed_candles, boundary)."""

    def __init__(self, profile: StrategyProfile, amat_root, python):
        self.profile = profile
        self.root, self.python = Path(amat_root).resolve(), Path(os.path.abspath(python))
        self.symbols = profile.symbols
        self.source_sha256 = digest(dict(profile.sources))
        for name, expected in profile.sources.items():
            source = self.root / "src/autoresearch" / name
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError(f"strategy source hash mismatch: {name}")
        self.identity = profile.identity
