"""Preregistered native campaign cycles with a historical candidate frontier."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from autoresearch.improvements import switching_study as shared
from autoresearch.improvements.campaign_audit import economic_digest, execution_identity, sealed
from autoresearch.improvements.study import digest, verify, write


def before_deadline(campaign: dict[str, Any]) -> None:
    if dt.datetime.now(dt.UTC) >= dt.datetime.fromisoformat(campaign["deadline_utc"]):
        raise ValueError("Campaign deadline reached; no new measurement or promotion")


def register(root: Path, snapshot: Path, output: Path, proposal_path: Path) -> None:
    proposal = sealed(proposal_path)
    campaign = sealed(Path(proposal["campaign_root"]) / "protocol.json")
    before_deadline(campaign)
    audit = sealed(Path(proposal["audit_path"]))
    if audit["campaign_protocol_sha256"] != digest(campaign):
        raise ValueError("Audit belongs to another campaign contract")
    prior = {c.get("execution_sha256", c["economic_sha256"]) for c in audit["economic_configs"]}
    policies = proposal.get("execution_cash_policies", {})
    if "core_daily" in policies or set(policies) - set(proposal["configs"]):
        raise ValueError("Execution policy must preserve the incumbent control")
    for policy in policies.values():
        shared.research_cash_policy(policy)
    for arm, config in proposal["configs"].items():
        if (
            arm != "core_daily"
            and execution_identity(config, policies.get(arm), whole_share_buys=True) in prior
        ):
            raise ValueError("Duplicate historical economic configuration")
    protocol = shared.register(
        root,
        snapshot,
        output,
        labels=proposal["labels"],
        recipe_builder=lambda _base: proposal["configs"],
    )
    parent = Path(proposal.get("incumbent_study", campaign["baseline_study"]))
    parent_arm = proposal.get("incumbent_arm", "core_daily")
    parent_protocol = sealed(parent / "protocol.json")
    if (
        parent_protocol.get("partition_sha256") != protocol["partition_sha256"]
        or parent_protocol.get("execution_start") != campaign["execution_start"]
        or parent_protocol.get("initial_cash") != campaign["initial_cash"]
        or parent_protocol.get("participation_cap") != campaign["entry_opening_volume_cap"]
    ):
        raise ValueError("Current incumbent reference uses a different execution contract")
    if economic_digest(proposal["configs"]["core_daily"]) != economic_digest(
        parent_protocol["configs"][parent_arm]
    ):
        raise ValueError("Control configuration does not match the declared incumbent")
    refs = {}
    for cost in [2, 6, 10]:
        record = sealed(parent / f"{parent_arm}-{cost}bps.json")
        if record["protocol_sha256"] != digest(parent_protocol):
            raise ValueError("Parent protocol identity mismatch")
        refs[str(cost)] = digest(record["sessions"])
    protocol.update(
        schema="performance-campaign-cycle/1",
        execution_cash_policies=policies,
        proposal=proposal,
        campaign_protocol_sha256=digest(campaign),
        campaign_audit_sha256=digest(audit),
        frontier=audit["frontier"],
        coverage_complete=audit["coverage_complete"],
        pending_replay_count=len(audit["pending_replay_or_review"]),
        legacy_ledger_count=len(audit["legacy_ledgers"]),
        source_verified_history_count=audit["unique_comparable_histories"],
        parent_sessions_sha256=refs,
        retention=campaign["promotion_policy"],
        hypothesis=proposal["hypothesis"],
        history_scope=protocol["history_scope"]
        + (
            " Campaign historical frontier includes rejected arms. Legacy/incompatible "
            "coverage blocks promotion until reviewed or replayed."
        ),
    )
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))


def retain(
    records: dict[str, dict[int, dict[str, Any]]], protocol: dict[str, Any]
) -> dict[str, Any]:
    for cost in [2, 6, 10]:
        if (
            digest(records["core_daily"][cost]["sessions"])
            != protocol["parent_sessions_sha256"][str(cost)]
        ):
            raise ValueError(f"Current best failed exact session reproduction at {cost}bps")
    decisions: dict[str, Any] = {
        "core_daily": {
            "status": "incumbent",
            "reason": "Exact current-best session history reproduced at every cost",
        }
    }
    for arm in protocol["labels"]:
        if arm == "core_daily":
            continue
        checks = []
        for cost in [2, 6, 10]:
            for period in ["full", "walk_forward"]:
                floor = protocol["frontier"][str(cost)][period]["net_return"]
                if floor is None:
                    raise ValueError("Missing historical frontier")
                value = records[arm][cost]["metrics"][period]["net_return"]
                checks.append(
                    {
                        "cost_bps": cost,
                        "period": period,
                        "reference": "historical_frontier",
                        "floor": floor,
                        "difference": value - floor,
                        "passes": value >= floor,
                    }
                )
        dd = (
            records[arm][6]["metrics"]["full"]["max_drawdown"]
            >= records["core_daily"][6]["metrics"]["full"]["max_drawdown"]
        )
        year = all(
            records[arm][c]["metrics"]["2023"]["net_return"]
            >= records["core_daily"][c]["metrics"]["2023"]["net_return"]
            for c in [2, 6, 10]
        )
        numeric = (
            all(c["passes"] for c in checks)
            and any(c["difference"] > 0 for c in checks)
            and dd
            and year
        )
        status = (
            ("retained" if protocol["coverage_complete"] else "pending") if numeric else "rejected"
        )
        decisions[arm] = {
            "status": status,
            "return_checks": checks,
            "drawdown_passes": dd,
            "preserves_2023": year,
            "coverage_complete": protocol["coverage_complete"],
            "annualized_log_return_delta_ci95": shared.paired_monthly_interval(
                records[arm][6], records["core_daily"][6]
            ),
            "reason": "Fails a historical-frontier return, drawdown or 2023 guard"
            if not numeric
            else "Measured gates pass, but historical benchmark review/replay is incomplete"
            if not protocol["coverage_complete"]
            else (
                "Passes measured frontier and coverage gates; requires versioned "
                "native-profile verification before activation"
            ),
        }
    return decisions


def run(output: Path) -> None:
    protocol = verify(output)
    before_deadline(sealed(Path(protocol["proposal"]["campaign_root"]) / "protocol.json"))
    shared.run(
        output,
        labels=protocol["labels"],
        retention_builder=lambda records: retain(records, protocol),
    )
