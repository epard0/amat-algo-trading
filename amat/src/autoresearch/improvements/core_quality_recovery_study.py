"""Preregistered native trend-agreement/lag ablations for the 2023 investigation."""

# Fixed preregistered scope descriptions.
# ruff: noqa: E501
from pathlib import Path
from typing import Any

from amat.config.models import StrategyConfig
from autoresearch.improvements import core_recovery_study as shared
from autoresearch.improvements.study import digest, write

LABELS = {
    "core_daily": "Daily core groups · reference",
    "agreement3": "Require all three trend horizons",
    "shorter_trend": "Trend horizons · 21/63/126 days",
    "agreement3_shorter": "Shorter trends + full agreement",
}


def recipes(base: StrategyConfig) -> dict[str, Any]:
    result = {}
    for arm in LABELS:
        cfg = base.model_dump(mode="json")
        if arm in {"agreement3", "agreement3_shorter"}:
            cfg["cross_sectional"]["min_horizon_agreement"] = 3
        if arm in {"shorter_trend", "agreement3_shorter"}:
            cfg["trend"]["horizons_sessions"] = [21, 63, 126]
        result[arm] = StrategyConfig.model_validate(cfg).model_dump(mode="json")
    return result


def register(root: Path, snapshot: Path, output: Path, reference: Path) -> dict[str, Any]:
    protocol = shared.register(
        root, snapshot, output, reference, labels=LABELS, recipe_builder=recipes
    )
    protocol.update(
        schema="native-trend-quality-recovery/1",
        investigation_scope="Previously exposed 2023 diagnosis; fixed all-horizon agreement and shorter trend-horizon ablations applied throughout history. Group count/lookback, risk sizing and execution remain native. No single-asset ban or calendar-specific rule.",
    )
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def run(output: Path) -> None:
    shared.run(output, labels=LABELS)
