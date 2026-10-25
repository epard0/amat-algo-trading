"""Validated, immutable strategy descriptions; no execution or provider dependencies."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from .domain import dec, digest

ENGINES = {
    "trend_short": (
        "crypto_shadow_trend.py",
        "crypto_trend_short.py",
        "crypto_breakout_windows.py",
        "crypto_adx_switch.py",
        "crypto_volatility_switch.py",
        "crypto_doge_sol.py",
        "crypto_directional.py",
        "crypto_partial_exits.py",
        "crypto_adx_leverage_study.py",
    ),
    "symmetric": ("crypto_perpetual_study.py",),
    "directional": ("crypto_directional.py",),
    "profit": ("crypto_doge_sol.py", "crypto_directional.py"),
    "adx_exits": (
        "crypto_adx_switch.py",
        "crypto_volatility_switch.py",
        "crypto_doge_sol.py",
        "crypto_directional.py",
        "crypto_partial_exits.py",
    ),
}


class Strategy(Protocol):
    identity: str
    symbols: tuple[str, ...]

    def decide(self, candles: dict, as_of_ms: int) -> dict: ...


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    legs: tuple[dict, ...]
    sources: dict[str, str]
    evidence: dict

    @classmethod
    def load(cls, path):
        raw = tomllib.loads(Path(path).read_text())
        return cls.parse(raw)

    @classmethod
    def parse(cls, raw):
        name, legs, sources = raw["name"], tuple(raw["legs"]), raw["sources"]
        if not name or not legs or len({r["ticker"] for r in legs}) != len(legs):
            raise ValueError("strategy name and unique instrument legs required")
        if abs(sum((dec(r["allocation"]) for r in legs), dec(0)) - 1) > dec(".00000001"):
            raise ValueError("strategy allocations must sum to one")
        needed = set()
        for leg in legs:
            engine = leg["engine"]
            if engine not in ENGINES:
                raise ValueError("unsupported strategy engine")
            common = {"ticker", "engine", "allocation", "leverage"}
            parameters = {
                "symmetric": {"sizing"},
                "directional": {"sizing", "direction", "tp_atr"},
                "profit": {"tp_atr", "max_hold_hours"},
                "adx_exits": set(),
                "trend_short": {"template", "adx_threshold", "ema_bars"},
            }[engine]
            unknown = set(leg) - common - parameters
            if unknown:
                raise ValueError(f"unsupported {engine} strategy parameters: {sorted(unknown)}")
            if engine == "trend_short" and (
                leg.get("template") not in ("DOGE", "SOL")
                or leg.get("adx_threshold") != 30
                or leg.get("ema_bars") != (25 if leg.get("template") == "DOGE" else 50)
                or dec(leg["leverage"]) != 1
            ):
                raise ValueError("shadow trend settings are frozen")
            needed.update(ENGINES[engine])
            if not 0 < dec(leg["allocation"]) <= 1 or not 0 < dec(leg["leverage"]) <= 2:
                raise ValueError("invalid allocation or policy leverage")
            if leg.get("direction", 1) not in (-1, 1):
                raise ValueError("strategy direction must be -1 or 1")
            if leg.get("tp_atr") is not None and dec(leg["tp_atr"]) <= 0:
                raise ValueError("take-profit ATR must be positive")
            if engine == "profit" and leg.get("tp_atr") is not None:
                hold = leg.get("max_hold_hours")
                if type(hold) is not int or hold <= 0 or hold % 4:
                    raise ValueError("holding cap must be positive and divisible by four")
            if engine == "symmetric" and leg.get("sizing", "vol20") != "vol20":
                raise ValueError("symmetric reference sizing must be vol20")
            if engine == "directional":
                sizing = leg.get("sizing", "full")
                if sizing != "full" and (not sizing.startswith("vol") or dec(sizing[3:]) <= 0):
                    raise ValueError("invalid volatility sizing")
        if set(sources) != needed or any(
            len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in sources.values()
        ):
            raise ValueError("exact engine source hashes required")
        return cls(
            name,
            tuple(MappingProxyType(dict(leg)) for leg in legs),
            MappingProxyType(dict(sources)),
            MappingProxyType(dict(raw.get("evidence", {}))),
        )

    @property
    def symbols(self):
        return tuple(r["ticker"] for r in self.legs)

    @property
    def identity(self):
        return digest(self.payload())

    def payload(self):
        return dict(
            name=self.name,
            legs=[dict(leg) for leg in self.legs],
            sources=dict(self.sources),
            evidence=dict(self.evidence),
        )
