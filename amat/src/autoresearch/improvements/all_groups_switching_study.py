"""Preregistered all-groups/conditional-cost switching interaction study."""

from __future__ import annotations

# Fixed inherited policies, no post-result threshold fitting.
# ruff: noqa: E501
import json
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from amat.portfolio import precompute_decision_inputs
from autoresearch.improvements import switching_study as shared
from autoresearch.improvements.conditional_switching_study import condition_masks
from autoresearch.improvements.study import digest, verify, write

LABELS = {
    "core_daily": "Daily core groups · original reference",
    "all_groups": "All groups · parent reference",
    "conditional_reference": "Conditional switching · parent reference",
    "all_volatility": "All groups + volatility cost hurdle",
    "all_combined": "All groups + combined-condition hurdle",
    "all_combined_double": "All groups + double combined hurdle",
    "all_always": "All groups + always-on cost hurdle",
    "all_always_double": "All groups + always-on double hurdle",
}
REFERENCES = ("core_daily", "all_groups", "conditional_reference")
CANDIDATES = tuple(a for a in LABELS if a not in REFERENCES)


def recipes(base: StrategyConfig) -> dict[str, Any]:
    result = {}
    for arm in LABELS:
        value = base.model_dump(mode="json")
        r = value["adaptive_rotation"]
        if arm not in {"core_daily", "conditional_reference"}:
            r["max_active_groups"] = 3
        if arm not in {"core_daily", "all_groups"}:
            r["switch_cost_bps"] = 2
        if arm.endswith("_double") or arm == "conditional_reference":
            r["switch_cost_multiple"] = 2
        result[arm] = StrategyConfig.model_validate(value).model_dump(mode="json")
    return result


def mask_panels(
    panel: MarketPanel, preferred: np.ndarray, all_groups: StrategyConfig
) -> dict[str, np.ndarray]:
    parent = condition_masks(panel, preferred)
    cache = precompute_decision_inputs(all_groups, panel.adjusted_closes, sessions=panel.sessions)
    if cache.adaptive_scores is None:
        raise ValueError("Missing all-groups native preferences")
    combined = condition_masks(panel, cache.adaptive_scores > 0)
    return {
        "conditional_reference": parent["combined"],
        "all_volatility": combined["volatility"],
        "all_combined": combined["combined"],
        "all_combined_double": combined["combined"],
    }
    # Always-on controls intentionally have no mask; None means active everywhere.


def register(root: Path, snapshot: Path, output: Path, reference: Path) -> dict[str, Any]:
    protocol = shared.register(root, snapshot, output, labels=LABELS, recipe_builder=recipes)
    refs = {}
    paths = {
        "core_daily": reference,
        "conditional_reference": reference,
        "all_groups": reference.parent.parent / "2026-10-19-native-group-recovery/study",
    }
    for arm, directory in paths.items():
        old_protocol = json.loads((directory / "protocol.json").read_text())
        if (
            digest(old_protocol) != (directory / "protocol.sha256").read_text().strip()
            or old_protocol["partition_sha256"] != protocol["partition_sha256"]
        ):
            raise ValueError("Parent protocol or data changed")
        key = "combined_double" if arm == "conditional_reference" else arm
        for c in [2, 6, 10]:
            p = directory / f"{key}-{c}bps.json"
            value = json.loads(p.read_text())
            if digest(value) != p.with_suffix(".sha256").read_text().strip() or value[
                "protocol_sha256"
            ] != digest(old_protocol):
                raise ValueError("Unsealed parent")
            refs[f"{arm}-{c}"] = {
                "path": str(p.resolve()),
                "sha256": digest(value),
                "sessions_sha256": digest(value["sessions"]),
            }
    protocol.update(
        schema="all-groups-conditional-switching/1",
        parent_records=refs,
        retention="Match or exceed BOTH all-groups and conditional-double parents plus original daily core net returns in full history AND 2020 onward at every 2/6/10bps; 6bps full-history drawdown no worse than every reference. At least one strict return gain over the all-groups parent. Also preserve or improve all-groups 2023 return at each cost, to avoid undoing the diagnosed recovery. Fixed inherited conditions; no threshold fitting.",
        conditional_rules={
            "volatility": "Trailing 20-day annualized vol >25% OR 20-day vol >1.5 times 60-day vol",
            "combined": "Volatility OR latest observed opening dollars <50% prior-60-day median OR 20-day median opening dollars <$5m OR at least three preferred-membership changes in ten sessions",
            "hurdle": "Five-day projection of trailing 20-session mean log-return advantage must exceed round-trip cost, optionally doubled; each trial uses its known cost assumption",
            "activation": "Either side meets condition at decision close. Required eligibility exits remain immediate. Comparisons replace eligible incumbents within the same available group.",
            "churn_basis": "Each parent uses its own unmodified native preferences: core for the conditional parent, all-groups for combinations. Never use candidate future fills or a two-group churn mask on a three-group strategy.",
            "controls": "The two always-on hurdle ablations distinguish condition selection from a blanket switching delay.",
        },
        history_scope=protocol["history_scope"]
        + " Previously exposed 2023 is a preservation guard, not a holdout.",
        research_only=True,
        promotion_authority=False,
    )
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def retain(
    records: dict[str, dict[int, dict[str, Any]]], protocol: dict[str, Any]
) -> dict[str, Any]:
    # Refuse attributing an interaction to changed controls or a different calendar.
    for arm in REFERENCES:
        for c in [2, 6, 10]:
            if (
                digest(records[arm][c]["sessions"])
                != protocol["parent_records"][f"{arm}-{c}"]["sessions_sha256"]
            ):
                raise ValueError(f"Parent did not reproduce: {arm} {c}bps")
    result: dict[str, Any] = {
        a: {
            "status": "incumbent" if a == "core_daily" else "reference",
            "reason": "Reproduced frozen parent",
        }
        for a in REFERENCES
    }
    for arm in CANDIDATES:
        checks = []
        for c in [2, 6, 10]:
            for period in ["full", "walk_forward"]:
                for ref in REFERENCES:
                    delta = (
                        records[arm][c]["metrics"][period]["net_return"]
                        - records[ref][c]["metrics"][period]["net_return"]
                    )
                    checks.append(
                        {
                            "cost_bps": c,
                            "period": period,
                            "reference": ref,
                            "difference": delta,
                            "passes": delta >= 0,
                        }
                    )
        dd = all(
            records[arm][6]["metrics"]["full"]["max_drawdown"]
            >= records[r][6]["metrics"]["full"]["max_drawdown"]
            for r in REFERENCES
        )
        year = all(
            records[arm][c]["metrics"]["2023"]["net_return"]
            >= records["all_groups"][c]["metrics"]["2023"]["net_return"]
            for c in [2, 6, 10]
        )
        strict = any(c["difference"] > 0 and c["reference"] == "all_groups" for c in checks)
        keep = all(c["passes"] for c in checks) and dd and year and strict
        result[arm] = {
            "status": "retained" if keep else "rejected",
            "return_checks": checks,
            "drawdown_passes": dd,
            "preserves_2023": year,
            "annualized_log_return_delta_ci95": shared.paired_monthly_interval(
                records[arm][6], records["all_groups"][6]
            ),
            "reason": "Passes every parent/cost/drawdown and 2023 guard"
            if keep
            else "Fails a parent return, drawdown or 2023 preservation guard",
        }
    return result


def run(output: Path) -> None:
    protocol = verify(output)
    all_groups = StrategyConfig.model_validate(protocol["configs"]["all_groups"])
    shared.run(
        output,
        labels=LABELS,
        mask_builder=lambda panel, preferred: mask_panels(panel, preferred, all_groups),
        retention_builder=lambda records: retain(records, protocol),
    )
