"""Validated configuration for the isolated v6 research candidate."""

from __future__ import annotations

import datetime as dt
import hashlib
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from amat.config.models import StrategyConfig
from amat.errors import ConfigurationError

__all__ = [
    "AssetRegimeConfig",
    "LiquidityConfig",
    "ResolvedV6Config",
    "RiskLearningConfig",
    "V6Config",
    "load_v6_config",
    "parse_v6_config",
]

_MODEL = ConfigDict(frozen=True, extra="forbid", strict=False)


class AssetRegimeConfig(BaseModel):
    """Causal, asset-level regime thresholds used only to reduce risk."""

    model_config = _MODEL

    capital_control_enabled: bool = False
    atr_period: int = Field(default=14, ge=5)
    adx_period: int = Field(default=14, ge=5)
    ema_period: int = Field(default=20, ge=5)
    slope_sessions: int = Field(default=5, ge=2)
    percentile_lookback: int = Field(default=100, ge=40)
    efficiency_lookback: int = Field(default=21, ge=5)
    confirmation_sessions: int = Field(default=3, ge=1)
    low_vol_percentile: float = Field(default=0.30, gt=0.0, lt=1.0)
    high_vol_percentile: float = Field(default=0.70, gt=0.0, lt=1.0)
    range_adx: float = Field(default=20.0, gt=0.0)
    trend_adx: float = Field(default=25.0, gt=0.0)
    min_efficiency: float = Field(default=0.20, ge=0.0, le=1.0)
    cusum_threshold: float = Field(default=3.0, gt=0.0)
    quiet_trend_scalar: float = Field(default=1.0, gt=0.0, le=1.0)
    normal_trend_scalar: float = Field(default=0.85, gt=0.0, le=1.0)
    volatile_trend_scalar: float = Field(default=0.50, gt=0.0, le=1.0)
    transition_scalar: float = Field(default=0.50, gt=0.0, le=1.0)
    quiet_range_scalar: float = Field(default=0.50, gt=0.0, le=1.0)
    volatile_range_scalar: float = Field(default=0.25, gt=0.0, le=1.0)
    downtrend_scalar: float = Field(default=0.75, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered_thresholds(self) -> AssetRegimeConfig:
        if self.low_vol_percentile >= self.high_vol_percentile:
            raise ValueError("low_vol_percentile must be below high_vol_percentile")
        if self.range_adx >= self.trend_adx:
            raise ValueError("range_adx must be below trend_adx")
        return self


class LiquidityConfig(BaseModel):
    """Entry-only liquidity confirmation; exits are never withheld."""

    model_config = _MODEL

    lookback_sessions: int = Field(default=63, ge=20)
    min_volume_percentile: float = Field(default=0.50, ge=0.0, le=1.0)
    min_dollar_volume: float = Field(default=10_000_000.0, ge=0.0)
    minimum_increase_scalar: float = Field(default=0.75, gt=0.0, le=1.0)


class RiskLearningConfig(BaseModel):
    """Conservative offline contextual-bandit search space."""

    model_config = _MODEL

    enabled: bool = False
    train_end: dt.date = dt.date(2022, 12, 30)
    stop_losses: tuple[float, ...] = (0.05, 0.08, 0.10)
    reward_risk_ratios: tuple[float, ...] = (1.5, 2.0, 3.0)
    reentry_embargo_sessions: int = Field(default=21, ge=1)
    min_training_episodes: int = Field(default=20, ge=10)
    prior_strength: float = Field(default=20.0, ge=0.0)
    downside_aversion: float = Field(default=0.50, ge=0.0)
    min_validation_improvement: float = Field(default=0.001, ge=0.0)
    validation_confidence_z: float = Field(default=2.64, ge=0.0)

    @model_validator(mode="after")
    def _valid_grid(self) -> RiskLearningConfig:
        if any(not 0.0 < value < 1.0 for value in self.stop_losses):
            raise ValueError("every stop loss must lie inside (0, 1)")
        if any(value <= 0.0 for value in self.reward_risk_ratios):
            raise ValueError("every reward/risk ratio must be positive")
        return self


class V6Config(BaseModel):
    """The v1 core plus v6-only decision overlays."""

    model_config = _MODEL

    strategy: StrategyConfig
    asset_regime: AssetRegimeConfig
    liquidity: LiquidityConfig
    risk_learning: RiskLearningConfig

    @model_validator(mode="after")
    def _is_v6(self) -> V6Config:
        if not self.strategy.version.startswith("6."):
            raise ValueError("the isolated v6 loader requires a 6.x strategy version")
        return self


class ResolvedV6Config(BaseModel):
    """Validated v6 configuration with byte-level provenance."""

    model_config = _MODEL

    config: V6Config
    source_path: Path | None = None
    content_sha256: str


def parse_v6_config(text: str, *, source_path: Path | None = None) -> ResolvedV6Config:
    """Parse a v6 TOML file, keeping overlay keys out of the frozen core parser."""
    try:
        payload = tomllib.loads(text)
        asset_regime = payload.pop("asset_regime")
        liquidity = payload.pop("liquidity")
        risk_learning = payload.pop("risk_learning")
        parsed = V6Config(
            strategy=StrategyConfig.model_validate(payload),
            asset_regime=AssetRegimeConfig.model_validate(asset_regime),
            liquidity=LiquidityConfig.model_validate(liquidity),
            risk_learning=RiskLearningConfig.model_validate(risk_learning),
        )
    except (KeyError, tomllib.TOMLDecodeError, ValueError) as exc:
        where = f" {source_path}" if source_path is not None else ""
        raise ConfigurationError(f"invalid v6 configuration{where}: {exc}") from exc
    return ResolvedV6Config(
        config=parsed,
        source_path=source_path,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def load_v6_config(path: Path | str) -> ResolvedV6Config:
    """Load and validate an isolated v6 TOML file."""
    resolved = Path(path).expanduser().resolve()
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read v6 configuration {resolved}: {exc}") from exc
    return parse_v6_config(text, source_path=resolved)
