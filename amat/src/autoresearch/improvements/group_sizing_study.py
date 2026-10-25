"""Preregistered group-specific unfavorable-condition sizing study."""

import json
from pathlib import Path
from typing import Any

from amat.config.models import StrategyConfig
from amat.data.snapshot import load_panel
from amat.portfolio import group_condition_prices, unfavorable_sizing_condition
from autoresearch.improvements import switching_study as shared
from autoresearch.improvements.study import digest, verify, write

LABELS = {
    "core_daily": "Current best · all groups + volatility switching",
    "weak_trend": "Group sizing · own weak trend",
    "volatility": "Group sizing · own volatility stress",
    "either": "Group sizing · own weak trend or volatility stress",
}


def recipes(base: StrategyConfig) -> dict[str, Any]:
    values = {}
    for arm in LABELS:
        value = base.model_dump(mode="json")
        value["adaptive_rotation"].update(
            max_active_groups=3, switch_cost_bps=2, switch_condition="volatility"
        )
        if arm != "core_daily":
            value["allocation"].update(group_condition=arm)
        values[arm] = StrategyConfig.model_validate(value).model_dump(mode="json")
    return values


def register(root: Path, snapshot: Path, output: Path, reference: Path) -> None:
    p = shared.register(root, snapshot, output, labels=LABELS, recipe_builder=recipes)
    parent = json.loads((reference / "protocol.json").read_text())
    if (
        digest(parent) != (reference / "protocol.sha256").read_text().strip()
        or parent["partition_sha256"] != p["partition_sha256"]
    ):
        raise ValueError("Parent evidence changed")
    refs = {}
    for cost in [2, 6, 10]:
        path = reference / f"core_daily-{cost}bps.json"
        value = json.loads(path.read_text())
        if digest(value) != path.with_suffix(".sha256").read_text().strip() or value[
            "protocol_sha256"
        ] != digest(parent):
            raise ValueError("Unsealed parent")
        refs[str(cost)] = digest(value["sessions"])
    p.update(
        schema="group-conditional-sizing/1",
        parent_sessions_sha256=refs,
        condition_rules={
            "weak_trend": (
                "Group geometric index completed close below trailing 200-session mean "
                "including that close"
            ),
            "volatility": (
                "Group index trailing 20-day annualized log-return volatility >25% "
                "OR >1.5 times trailing 60-day volatility, sample std"
            ),
            "either": "Weak trend OR volatility stress",
            "allocation": (
                "Each group uses its own equal-member geometric adjusted-price index. "
                "Only selected members of an unfavorable group are reweighted by score/sigma, "
                "normalized to preserve that group score mass before native portfolio risk "
                "targeting and caps. Other groups retain power-0 sizing. Completed closes "
                "only, next-session execution; group membership and switching unchanged."
            ),
        },
        hypotheses={k: v for k, v in LABELS.items() if k != "core_daily"},
        retention=(
            "Match or exceed current best net return for full history AND 2020 onward at "
            "every 2/6/10bps; 6bps full-history drawdown no worse; preserve 2023 at every "
            "cost; at least one strict return gain. Three independent fixed ablations, "
            "no threshold tuning."
        ),
    )
    write(output / "protocol.json", p)
    (output / "protocol.sha256").write_text(digest(p))


def retain(
    records: dict[str, dict[int, dict[str, Any]]], protocol: dict[str, Any]
) -> dict[str, Any]:
    for c in [2, 6, 10]:
        if (
            digest(records["core_daily"][c]["sessions"])
            != protocol["parent_sessions_sha256"][str(c)]
        ):
            raise ValueError(f"Native default differs from frozen winner at {c}bps")
    result: dict[str, Any] = {
        "core_daily": {"status": "incumbent", "reason": "Exact frozen winner reproduced natively"}
    }
    for arm in LABELS:
        if arm == "core_daily":
            continue
        checks = [
            {
                "cost_bps": c,
                "period": period,
                "difference": records[arm][c]["metrics"][period]["net_return"]
                - records["core_daily"][c]["metrics"][period]["net_return"],
            }
            for c in [2, 6, 10]
            for period in ["full", "walk_forward"]
        ]
        for check in checks:
            check["passes"] = check["difference"] >= 0
        dd = (
            records[arm][6]["metrics"]["full"]["max_drawdown"]
            >= records["core_daily"][6]["metrics"]["full"]["max_drawdown"]
        )
        year = all(
            records[arm][c]["metrics"]["2023"]["net_return"]
            >= records["core_daily"][c]["metrics"]["2023"]["net_return"]
            for c in [2, 6, 10]
        )
        keep = (
            all(x["passes"] for x in checks)
            and dd
            and year
            and any(x["difference"] > 0 for x in checks)
        )
        result[arm] = {
            "status": "retained" if keep else "rejected",
            "return_checks": checks,
            "drawdown_passes": dd,
            "preserves_2023": year,
            "annualized_log_return_delta_ci95": shared.paired_monthly_interval(
                records[arm][6], records["core_daily"][6]
            ),
            "reason": "Passes all fixed checks"
            if keep
            else "Fails return, drawdown or 2023 preservation guard",
        }
    return result


def run(output: Path) -> None:
    protocol = verify(output)
    shared.run(output, labels=LABELS, retention_builder=lambda records: retain(records, protocol))
    write_activation(output)


def write_activation(output: Path) -> None:
    protocol = verify(output)
    base = StrategyConfig.model_validate(protocol["configs"]["core_daily"])
    symbols = list(map(str, base.universe.symbols))
    panel, _ = load_panel(Path(protocol["snapshot"]), symbols, with_context=True)
    activation: dict[str, Any] = {"protocol_sha256": digest(protocol), "conditions": {}}
    for arm in LABELS:
        if arm == "core_daily":
            continue
        group_rows = {}
        for group in base.adaptive_rotation.groups:
            columns = [symbols.index(symbol) for symbol in group.symbols]
            proxy = group_condition_prices(panel.adjusted_closes, columns)
            group_rows[group.name] = [
                {"session": str(date), "active": unfavorable_sizing_condition(proxy, t, 0, arm)}
                for t, date in enumerate(panel.sessions)
                if str(date) >= protocol["execution_start"]
            ]
        activation["conditions"][arm] = group_rows
    write(output / "activation.json", activation)
    (output / "activation.sha256").write_text(digest(activation))
