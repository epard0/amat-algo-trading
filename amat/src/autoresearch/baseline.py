"""Research envelopes for the preserved v6 and user-selected native core strategies."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import Field, model_validator

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.config.v6 import (
    AssetRegimeConfig,
    LiquidityConfig,
    RiskLearningConfig,
    V6Config,
    load_v6_config,
)
from llmrouter.contracts import Record


class CoreV13Config(Record):
    """Native core envelope; historical class name retained for compatibility."""

    strategy: StrategyConfig
    asset_regime: AssetRegimeConfig = Field(default_factory=AssetRegimeConfig)
    liquidity: LiquidityConfig = Field(default_factory=LiquidityConfig)
    risk_learning: RiskLearningConfig = Field(default_factory=RiskLearningConfig)

    @model_validator(mode="after")
    def core_only(self) -> CoreV13Config:
        if not self.strategy.version.startswith(("13.", "14.")):
            raise ValueError("core research envelope requires native v13 or v14")
        if self.asset_regime.capital_control_enabled or self.risk_learning.enabled:
            raise ValueError("native core baseline does not import v6 learned overlays")
        return self


def load_research_config(path: Path) -> V6Config | CoreV13Config:
    version = str(tomllib.loads(path.read_text()).get("version", ""))
    if version.startswith(("13.", "14.")):
        return CoreV13Config(strategy=load_strategy_config(path).strategy)
    return load_v6_config(path).config
