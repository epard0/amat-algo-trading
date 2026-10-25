"""Five preregistered, independent native improvements to the selected default."""

import json
from pathlib import Path
from typing import Any

from amat.config.models import StrategyConfig
from autoresearch.improvements import switching_study as shared
from autoresearch.improvements.study import digest, verify, write

LABELS = {
    "core_daily": "Current best · all groups + volatility switching",
    "wider_band": "Wider rebalance band · 3%",
    "confirm_two": "Two-day replacement confirmation",
    "larger_hurdle": "Cost hurdle · 1.5 times round-trip cost",
    "concentration_cap": "Lower single-asset cap · 40%",
    "inverse_volatility": "Inverse-volatility allocation",
}


def recipes(base: StrategyConfig) -> dict[str, Any]:
    values = {}
    for arm in LABELS:
        v = base.model_dump(mode="json")
        v["adaptive_rotation"].update(
            max_active_groups=3, switch_cost_bps=2, switch_condition="volatility"
        )
        if arm == "wider_band":
            v["rebalance"]["min_weight_delta"] = 0.03
        if arm == "confirm_two":
            v["adaptive_rotation"]["switch_confirmation_sessions"] = 2
        if arm == "larger_hurdle":
            v["adaptive_rotation"]["switch_cost_multiple"] = 1.5
        if arm == "concentration_cap":
            v["constraints"]["max_symbol_weight"] = 0.4
        if arm == "inverse_volatility":
            v["allocation"]["volatility_power"] = 1.0
        values[arm] = StrategyConfig.model_validate(v).model_dump(mode="json")
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
        path = reference / f"all_volatility-{cost}bps.json"
        value = json.loads(path.read_text())
        if digest(value) != path.with_suffix(".sha256").read_text().strip() or value[
            "protocol_sha256"
        ] != digest(parent):
            raise ValueError("Unsealed parent")
        refs[str(cost)] = digest(value["sessions"])
    p.update(
        schema="volatility-next/1",
        parent_sessions_sha256=refs,
        hypotheses={k: v for k, v in LABELS.items() if k != "core_daily"},
        retention=(
            "Match or exceed current best net return for full history AND 2020 onward at "
            "every 2/6/10bps; 6bps full-history drawdown no worse; preserve 2023 at every "
            "cost; at least one strict return gain. Five independent fixed ablations, "
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
