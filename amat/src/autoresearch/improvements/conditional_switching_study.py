"""Fixed, causal condition gates for cost-aware membership switching."""

from __future__ import annotations

# Literal thresholds define this preregistered experiment; no threshold search.
# ruff: noqa: PLR2004, E501
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from autoresearch.improvements import switching_study as shared
from autoresearch.improvements.study import digest, write

LABELS = {
    "core_daily": "Daily core groups · reference",
    "volatility": "Cost hurdle during high volatility",
    "liquidity": "Cost hurdle during thin liquidity",
    "churn": "Cost hurdle during rank churn",
    "inefficient": "Cost hurdle during choppy trends",
    "vol_or_liquidity": "Volatility or thin liquidity",
    "vol_and_liquidity": "Volatility and thin liquidity",
    "combined": "Volatility, liquidity or rank churn",
    "combined_double": "Combined conditions · double hurdle",
}


def condition_masks(panel: MarketPanel, preferred: np.ndarray) -> dict[str, np.ndarray]:
    """At close t, use only observed prices/opening liquidity through t; no future fills."""
    prices = panel.adjusted_closes
    dollars = panel.window_vwaps * panel.window_volumes
    if (
        preferred.shape != prices.shape
        or preferred.dtype != np.bool_
        or not np.isfinite(prices).all()
        or np.any(prices <= 0)
        or not np.isfinite(dollars).all()
        or np.any(dollars <= 0)
    ):
        raise ValueError("Aligned finite prices/liquidity and boolean preferences required")
    returns = np.diff(np.log(prices), axis=0)
    masks = {
        k: np.zeros_like(prices, dtype=bool)
        for k in ["volatility", "liquidity", "churn", "inefficient"]
    }
    for t in range(60, len(prices)):
        recent = returns[t - 20 : t]
        fast = np.std(recent, axis=0, ddof=1)
        slow = np.std(returns[t - 60 : t], axis=0, ddof=1)
        masks["volatility"][t] = (fast * np.sqrt(252) > 0.25) | (fast > 1.5 * slow)
        # $25k intended allocation would use > half the 1%-volume budget.
        # This is a fixed $100k-account capacity proxy, not a realized order size.
        masks["liquidity"][t] = (dollars[t] < 0.5 * np.median(dollars[t - 60 : t], axis=0)) | (
            np.median(dollars[t - 19 : t + 1], axis=0) < 5_000_000
        )
        masks["churn"][t] = np.sum(preferred[t - 9 : t + 1] != preferred[t - 10 : t], axis=0) >= 3
        path = np.sum(np.abs(recent), axis=0)
        efficiency = np.divide(
            np.abs(np.sum(recent, axis=0)), path, out=np.zeros_like(path), where=path > 0
        )
        masks["inefficient"][t] = efficiency < 0.25
    masks["vol_or_liquidity"] = masks["volatility"] | masks["liquidity"]
    masks["vol_and_liquidity"] = masks["volatility"] & masks["liquidity"]
    masks["combined"] = masks["vol_or_liquidity"] | masks["churn"]
    masks["combined_double"] = masks["combined"].copy()
    return masks


def recipes(base: StrategyConfig) -> dict[str, Any]:
    values = {}
    for arm in LABELS:
        value = base.model_dump(mode="json")
        if arm != "core_daily":
            value["adaptive_rotation"]["switch_cost_bps"] = 2
            if arm == "combined_double":
                value["adaptive_rotation"]["switch_cost_multiple"] = 2
        values[arm] = StrategyConfig.model_validate(value).model_dump(mode="json")
    return values


def register(root: Path, snapshot: Path, output: Path) -> dict[str, Any]:
    # Use the shared immutable protocol writer, then add the fixed gate definitions
    # before any trial is permitted to run. The runner verifies this final digest.
    protocol = shared.register(root, snapshot, output, labels=LABELS, recipe_builder=recipes)
    protocol.update(
        schema="daily-group-conditional-switching/1",
        conditional_rules={
            "volatility": "20-day annualized vol >25% OR 20-day vol >1.5 times 60-day vol",
            "liquidity": "Latest observed opening dollar volume <50% of prior 60-session median OR trailing 20-session median opening dollar volume <$5m; $25k capacity proxy on $100k starting account",
            "churn": "At least three native preferred-membership changes in trailing ten sessions",
            "inefficient": "20-day absolute net log return / sum absolute daily log returns <0.25",
            "activation": "Hurdle active if either candidate or incumbent meets gate at decision close; otherwise native membership switching; required exits unchanged",
            "combinations": "Volatility OR liquidity; volatility AND liquidity; volatility OR liquidity OR churn; combined with twice round-trip hurdle",
            "scope": "Lagged observed liquidity is a proxy, not a spread estimate or next-session volume forecast; no tuning after results",
        },
    )
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def run(output: Path) -> None:
    shared.run(output, labels=LABELS, mask_builder=condition_masks)
