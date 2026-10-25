"""One configuration selects execution mode; paper is the default."""

import os
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from .domain import dec, digest


@dataclass(frozen=True)
class Config:
    path: Path
    raw: dict

    @classmethod
    def load(cls, path):
        result = cls(Path(path).resolve(), tomllib.loads(Path(path).read_text()))
        result.validate()
        return result

    def resolve(self, value):
        p = Path(value).expanduser()
        # Preserve a venv's interpreter symlink: resolving it selects the base
        # interpreter and loses the research environment's installed packages.
        return Path(os.path.abspath(p if p.is_absolute() else self.path.parent / p))

    @property
    def mode(self):
        return self.raw.get("mode", "paper")

    @property
    def database(self):
        return self.resolve(self.raw["database"])

    @property
    def identity(self):
        return digest(self.raw)

    def select_strategy(self, name):
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
            raise ValueError("invalid strategy profile name")
        directory = self.path.parent / "strategies"
        if not directory.is_dir():
            directory = Path(__file__).resolve().parents[2] / "configs/strategies"
        profile = directory / (name + ".toml")
        if not profile.is_file():
            raise ValueError("unknown strategy profile: " + name)
        if (
            self.raw["strategy"].get("profile")
            and self.resolve(self.raw["strategy"]["profile"]) == profile
        ):
            return self
        raw = deepcopy(self.raw)
        raw["strategy"]["profile"] = str(profile)
        raw["account"] = f"{self.mode}:{name}"
        raw["database"] = str(self.database.parent / f"{self.mode}-{name}.sqlite")
        result = Config(self.path, raw)
        result.validate()
        return result

    def validate(self):
        if self.mode not in ("paper", "demo", "live"):
            raise ValueError("mode must be paper, demo, or live")
        for key in ("account", "database", "instruments", "strategy"):
            if not self.raw.get(key):
                raise ValueError(f"missing {key}")
        risk = self.raw["risk"]
        for key in (
            "max_gross",
            "max_order_notional",
            "max_spread_bps",
            "max_loss_fraction",
            "quote_max_age_ms",
        ):
            if dec(risk[key]) <= 0:
                raise ValueError(f"positive risk.{key} required")
        dynamic = self.raw.get("paper", {}).get("leverage_mode") == "venue_max"
        if dynamic and self.mode != "paper":
            raise ValueError("venue_max comparison overlay is paper-only")
        if dec(risk["max_loss_fraction"]) >= 1 or dec(risk["max_gross"]) > (100 if dynamic else 2):
            raise ValueError("invalid loss/gross limit")
        if self.mode == "live" and dec(risk["max_gross"]) > 1:
            raise ValueError("initial live policy capped at 1x gross")
        data = self.raw.get("data", {})
        if data.get("provider", "kalshi") not in ("kalshi", "kraken-perpetuals"):
            raise ValueError("unknown signal-history provider")
        if data.get("provider") == "kraken-perpetuals":
            if (
                type(data.get("history_days", 90)) is not int
                or not 32 <= data.get("history_days", 90) <= 3650
            ):
                raise ValueError("history_days must be 32..3650")
            if dec(data.get("max_basis_bps", "500")) <= 0:
                raise ValueError("positive maximum signal/execution basis required")
        if self.mode != "paper":
            venue = self.raw["venue"]
            if venue["environment"] != ("production" if self.mode == "live" else "demo"):
                raise ValueError("execution mode/environment mismatch")
            if not venue.get("key_id_env") or not venue.get("private_key_env"):
                raise ValueError("explicit package credential variable names required")
