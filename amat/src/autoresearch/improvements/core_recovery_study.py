"""Fixed native group-selection ablations prompted by the 2023 loss investigation."""

from __future__ import annotations

# ruff: noqa: E501
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from amat.config.models import StrategyConfig
from autoresearch.improvements import switching_study as shared
from autoresearch.improvements.study import digest, verify, write

LABELS = {
    "core_daily": "Daily core groups · reference",
    "faster6": "Group ranking · six completed weeks",
    "faster4": "Group ranking · four completed weeks",
    "broad_benchmark": "Group ranking · SPY reference",
    "all_groups": "Allow all three groups",
    "faster6_spy": "Six-week groups + SPY reference",
    "all_groups_spy": "All groups + SPY reference",
}


def recipes(base: StrategyConfig) -> dict[str, Any]:
    result = {}
    for arm in LABELS:
        cfg = base.model_dump(mode="json")
        r = cfg["adaptive_rotation"]
        if arm in {"faster6", "faster6_spy"}:
            r["lookback_weeks"] = 6
        if arm == "faster4":
            r["lookback_weeks"] = 4
        if arm in {"broad_benchmark", "faster6_spy", "all_groups_spy"}:
            r["benchmark"] = "SPY"
        if arm in {"all_groups", "all_groups_spy"}:
            r["max_active_groups"] = 3
        result[arm] = StrategyConfig.model_validate(cfg).model_dump(mode="json")
    return result


def register(
    root: Path,
    snapshot: Path,
    output: Path,
    reference: Path,
    *,
    labels: dict[str, str] | None = None,
    recipe_builder: Callable[[StrategyConfig], dict[str, Any]] = recipes,
) -> dict[str, Any]:
    protocol = shared.register(
        root,
        snapshot,
        output,
        labels=LABELS if labels is None else labels,
        recipe_builder=recipe_builder,
    )
    refs = {}
    for c in [2, 6, 10]:
        p = reference / f"combined_double-{c}bps.json"
        v = json.loads(p.read_text())
        if digest(v) != p.with_suffix(".sha256").read_text().strip():
            raise ValueError("Reference changed")
        refs[str(c)] = {"path": str(p.resolve()), "sha256": digest(v)}
    protocol.update(
        schema="native-group-recovery/1",
        strong_reference=refs,
        retention="Match or exceed daily core AND retained conditional-double full-history and 2020-onward returns at every 2/6/10bps; 6bps full drawdown no worse than either reference. 2023 return must match core at all costs and strictly improve at 6bps. Rules address group responsiveness/benchmark dependence; no per-year switching or tuned thresholds.",
        investigation_scope="2023 is an exposed diagnostic year, not a holdout. Only native group lookback, group count and benchmark are changed. Signal horizons, sizing, execution, membership eligibility and caps are unchanged. No cash recovery quota or forced trading to recoup losses.",
    )
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def run(output: Path, *, labels: dict[str, str] | None = None) -> None:
    labels = LABELS if labels is None else labels
    protocol = verify(output)
    refs = {}
    for c in [2, 6, 10]:
        meta = protocol["strong_reference"][str(c)]
        v = json.loads(Path(meta["path"]).read_text())
        if digest(v) != meta["sha256"]:
            raise ValueError("Reference changed")
        refs[c] = v

    def retention(records: dict[str, dict[int, dict[str, Any]]]) -> dict[str, Any]:
        result = shared.retention(records, labels=labels)
        for arm in labels:
            if arm == "core_daily":
                continue
            strong = all(
                records[arm][c]["metrics"][period]["net_return"]
                >= refs[c]["metrics"][period]["net_return"]
                for c in [2, 6, 10]
                for period in ["full", "walk_forward"]
            )
            dd = (
                records[arm][6]["metrics"]["full"]["max_drawdown"]
                >= refs[6]["metrics"]["full"]["max_drawdown"]
            )
            recovery = (
                all(
                    records[arm][c]["metrics"]["2023"]["net_return"]
                    >= records["core_daily"][c]["metrics"]["2023"]["net_return"]
                    for c in [2, 6, 10]
                )
                and records[arm][6]["metrics"]["2023"]["net_return"]
                > records["core_daily"][6]["metrics"]["2023"]["net_return"]
            )
            keep = result[arm]["status"] == "retained" and strong and dd and recovery
            result[arm].update(
                status="retained" if keep else "rejected",
                strong_reference_passes=strong and dd,
                recovery_2023_passes=recovery,
                reason="Passes full-history, costs, drawdown and exposed-year improvement guards"
                if keep
                else "Fails overall return/drawdown or exposed-year improvement guard",
            )
        return result

    shared.run(output, labels=labels, retention_builder=retention)
