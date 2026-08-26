"""The v4 configuration: two frozen stances plus the rule that blends them.

A v4 file carries *complete* strategy configurations for both stances inline --
no references to other files -- so its content digest freezes the entire
definition, exactly like a v1 file freezes v1. The trend stance is expected to
be the v1 contract verbatim; the defensive stance is a restricted-universe
parameterization of the same core; the switching rule holds the only new
constants v4 introduces.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from amat.config.models import StrategyConfig
from amat.errors import ConfigurationError

__all__ = [
    "ResolvedSwitchingConfig",
    "SwitchingRule",
    "SwitchingStrategyConfig",
    "load_switching_config",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid", strict=False)


class SwitchingRule(BaseModel):
    """The declared constants of the breadth selector. Nothing here is fitted."""

    model_config = _MODEL_CONFIG

    breadth_horizon_sessions: int = Field(default=63, ge=1)
    smoothing_half_life_sessions: int = Field(default=10, ge=1)
    floor_breadth: float = Field(default=0.35, ge=0.0, le=1.0)
    full_breadth: float = Field(default=0.65, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _require_ordered_thresholds(self) -> Self:
        if self.floor_breadth >= self.full_breadth:
            message = (
                f"floor_breadth ({self.floor_breadth}) must be strictly below "
                f"full_breadth ({self.full_breadth})"
            )
            raise ValueError(message)
        return self


class SwitchingStrategyConfig(BaseModel):
    """Two stances of one core and the blend rule between them."""

    model_config = _MODEL_CONFIG

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    trend: StrategyConfig
    defensive: StrategyConfig
    switching: SwitchingRule = SwitchingRule()

    @model_validator(mode="after")
    def _require_compatible_stances(self) -> Self:
        trend_symbols = set(self.trend.universe.symbols)
        extra = [
            symbol for symbol in self.defensive.universe.symbols if symbol not in trend_symbols
        ]
        if extra:
            message = (
                f"defensive stance symbols {extra} are not in the trend universe; "
                "a blended instruction for them could never price against the panel"
            )
            raise ValueError(message)
        if self.defensive.warmup.min_completed_sessions > self.trend.warmup.min_completed_sessions:
            message = (
                "defensive warm-up exceeds the trend warm-up; every early blended "
                "decision would fail"
            )
            raise ValueError(message)
        # Timing and cost assumptions must match: a blend of two books filled
        # under different execution windows or cost models is not one strategy.
        if self.defensive.execution != self.trend.execution:
            message = "stances must share the execution configuration"
            raise ValueError(message)
        if self.defensive.costs != self.trend.costs:
            message = "stances must share the cost configuration"
            raise ValueError(message)
        if self.defensive.cash != self.trend.cash:
            message = "stances must share the cash configuration"
            raise ValueError(message)
        return self


@dataclass(frozen=True, slots=True)
class ResolvedSwitchingConfig:
    """A validated v4 configuration plus the provenance of its source bytes."""

    strategy: SwitchingStrategyConfig
    source_path: Path | None
    content_sha256: str


def parse_switching_config(
    text: str, *, source_path: Path | None = None
) -> ResolvedSwitchingConfig:
    """Parse and validate v4 TOML text. Unknown keys are errors."""
    raw = text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    where = f" in {source_path}" if source_path is not None else ""

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = f"invalid TOML{where}: {exc}"
        raise ConfigurationError(message) from exc

    try:
        strategy = SwitchingStrategyConfig.model_validate(document)
    except PydanticValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()
        )
        message = f"invalid switching configuration{where}: {details}"
        raise ConfigurationError(message) from exc

    return ResolvedSwitchingConfig(
        strategy=strategy, source_path=source_path, content_sha256=digest
    )


def load_switching_config(path: Path | str) -> ResolvedSwitchingConfig:
    """Load and validate a v4 switching configuration file."""
    resolved = Path(path).expanduser()
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        message = f"switching configuration not found: {resolved}"
        raise ConfigurationError(message) from exc
    except (OSError, UnicodeDecodeError) as exc:
        message = f"cannot read switching configuration {resolved}: {exc}"
        raise ConfigurationError(message) from exc
    return parse_switching_config(text, source_path=resolved)
