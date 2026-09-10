"""Typed configuration and its resolution into a content-addressed record."""

from __future__ import annotations

from amat.config.loading import ResolvedConfig, load_strategy_config, parse_strategy_config
from amat.config.models import (
    CashConfig,
    ClusterSpec,
    ConstraintConfig,
    CostConfig,
    CovarianceConfig,
    ExecutionConfig,
    LearnedRegimeConfig,
    RebalanceConfig,
    StrategyConfig,
    StructureFilterConfig,
    TrendConfig,
    UniverseSpec,
    VolatilityConfig,
    WarmupConfig,
)

__all__ = [
    "CashConfig",
    "ClusterSpec",
    "ConstraintConfig",
    "CostConfig",
    "CovarianceConfig",
    "ExecutionConfig",
    "LearnedRegimeConfig",
    "RebalanceConfig",
    "ResolvedConfig",
    "StrategyConfig",
    "StructureFilterConfig",
    "TrendConfig",
    "UniverseSpec",
    "VolatilityConfig",
    "WarmupConfig",
    "load_strategy_config",
    "parse_strategy_config",
]
