"""Read-only evidence inventory and conservative return frontier for campaign research."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from amat.config.models import StrategyConfig
from autoresearch.improvements.group_replay_proof import verify_core_group_replay
from autoresearch.improvements.study import digest, source_hashes, write
from autoresearch.improvements.switching_study import CASH_POLICY_SPEC

NATIVE_SCHEMAS = {
    "daily-group-switching/1",
    "daily-group-conditional-switching/1",
    "all-groups-conditional-switching/1",
    "native-group-recovery/1",
    "native-trend-quality-recovery/1",
    "daily-group-efficiency/1",
    "conditional-sizing/1",
    "group-conditional-sizing/1",
    "asset-conditional-sizing/1",
    "asset-percentile-sizing/1",
    "volatility-next/1",
    "performance-campaign-cycle/1",
}


# Only these reviewed original config-native campaign proposals may use this schema.
# Later campaign runs can contain overlays and must never be admitted by arm name alone.
REVIEWED_CAMPAIGN_ARMS = {
    "cd9a9faadebc75ce2bf588dccd198e249a65c7fc61b6544cb70c163f0f5eb14d": {
        "core_daily",
        "six_positions",
    },
    "92a285b3384888ce94b62c2b8f9acb4e34f4d8a023939dbffd944f24172a5be1": {
        "core_daily",
        "unanimous_trends",
    },
    "acd60a7589b6a29a026123103108f97a7eb7abdce7af9a954f22e0d20bc4f50b": {
        "core_daily",
        "trade_floor250",
    },
    "a389624a6601b636e4db61d55ee1b61547c88d6f3d2dfe6f6d126e1e1bdeb73b": {
        "core_daily",
        "metals_cap30",
    },
    "dd071bbaaf66ff12cf9ff448963cf85c221b2957200cf1f92ecbe85d5e44e55c": {
        "core_daily",
        "faster_trends",
    },
}


def sealed(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected a sealed JSON object")
    if digest(value) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError(f"Unsealed evidence: {path}")
    return value


def economic_digest(value: dict[str, Any]) -> str:
    config = StrategyConfig.model_validate(value).model_dump(mode="json")
    config.pop("name", None)
    config.pop("version", None)
    return digest(config)


def execution_identity(
    config: dict[str, Any], policy: dict[str, str] | None = None, *, whole_share_buys: bool = False
) -> str:
    economic = economic_digest(config)
    return (
        digest(
            {
                "economic_sha256": economic,
                "cash_policy": policy,
                "whole_share_buys": whole_share_buys,
            }
        )
        if policy or whole_share_buys
        else economic
    )


def original_switch_gate(  # noqa: PLR0917 - source and replay identities are explicit
    origin_path: Path,
    origin: dict[str, Any],
    arm: str,
    replay: dict[str, Any],
    alias: str,
    item: dict[str, Any],
) -> str | None:
    """Bind reviewed external masks to the original source-produced condition history."""
    schema = origin.get("schema")
    all_group_arms = {
        "conditional_reference",
        "all_volatility",
        "all_combined",
        "all_combined_double",
    }
    daily_arms = {
        "volatility",
        "liquidity",
        "churn",
        "inefficient",
        "vol_or_liquidity",
        "vol_and_liquidity",
        "combined",
        "combined_double",
    }
    if schema == "all-groups-conditional-switching/1" and arm in all_group_arms:
        modules = ["all_groups_switching_study.py", "conditional_switching_study.py"]
    elif schema == "daily-group-conditional-switching/1" and arm in daily_arms:
        modules = ["conditional_switching_study.py"]
    else:
        return None
    for module in modules:
        key = "src/autoresearch/improvements/" + module
        if not origin["source_sha256"].get(key) or (
            origin["source_sha256"][key] != replay["source_sha256"].get(key)
        ):
            raise ValueError("Condition recipe source differs from original")
    gates = set()
    for cost in [2, 6, 10]:
        original = sealed(origin_path.parent / f"{arm}-{cost}bps.json")
        if (
            original.get("protocol_sha256") != digest(origin)
            or original.get("arm") != arm
            or original.get("cost_bps") != cost
        ):
            raise ValueError("Original condition record protocol or arm differs")
        gates.add(original.get("switch_gate_sha256"))
    if len(gates) != 1 or None in gates:
        raise ValueError("Original condition history differs across costs")
    gate = gates.pop()
    if (
        item.get("switch_gate_sha256") != gate
        or replay.get("registered_switch_gate_sha256", {}).get(alias) != gate
    ):
        raise ValueError(
            "Historical overlay semantics: condition history was not registered as original"
        )
    return str(gate)


def original_decision_overlay(  # noqa: PLR0917 - explicit source/replay evidence
    origin_path: Path,
    origin: dict[str, Any],
    arm: str,
    replay_path: Path,
    replay: dict[str, Any],
    alias: str,
    item: dict[str, Any],
) -> str | None:
    """Require original legacy semantics plus registered array proof for reviewed filters."""
    if origin.get("schema") != "daily-group-efficiency/1" or arm not in {
        "group_trend",
        "group_shock",
        "group_stress",
        "band3_trend",
        "band4_trend",
    }:
        return None
    proposal = replay.get("proposal", {})
    if not proposal.get("overlay_replay_design_path"):
        raise ValueError("Historical overlay semantics require original diagnostic proof")
    design = sealed(Path(proposal["overlay_replay_design_path"]))
    if (
        digest(design) != proposal["overlay_replay_design_sha256"]
        or design["origin_protocol"] != str(origin_path)
        or design["origin_protocol_sha256"] != digest(origin)
        or design["partition_sha256"] != replay["partition_sha256"]
    ):
        raise ValueError("Original overlay design/source/data identity differs")
    key = "src/autoresearch/improvements/efficiency_study.py"
    if (
        origin["source_sha256"].get(key) != design["recipe_module_sha256"][key]
        or replay["source_sha256"].get(key) != design["current_recipe_module_sha256"][key]
    ):
        raise ValueError("Original or replay overlay recipe bytes differ")
    expected = design["arms"][arm]["decision_overlay_sha256"]
    probe = item["legacy_diagnostic_arm"]
    if (
        item.get("decision_overlay_sha256") != expected
        or replay.get("registered_decision_overlay_sha256", {}).get(alias) != expected
        or replay.get("registered_decision_overlay_sha256", {}).get(probe) != expected
        or replay.get("whole_share_buys_by_arm", {}).get(probe) is not False
        or economic_digest(replay["configs"][probe]) != economic_digest(origin["configs"][arm])
    ):
        raise ValueError("Registered overlay or diagnostic identity differs")
    for cost in [2, 6, 10]:
        original = sealed(origin_path.parent / f"{arm}-{cost}bps.json")
        diagnostic = sealed(replay_path / f"{probe}-{cost}bps.json")
        if (
            digest(original) != design["arms"][arm]["original_record_sha256"][str(cost)]
            or original.get("protocol_sha256") != digest(origin)
            or original.get("arm") != arm
            or original.get("cost_bps") != cost
            or diagnostic.get("protocol_sha256") != digest(replay)
            or diagnostic.get("arm") != probe
            or diagnostic.get("cost_bps") != cost
            or diagnostic.get("decision_overlay_sha256") != expected
            or digest(diagnostic["sessions"]) != digest(original["sessions"])
            or diagnostic.get("switch_gate_sha256") is not None
            or diagnostic.get("execution_cash_policy_sha256") is not None
        ):
            raise ValueError("Original fractional overlay semantics did not reproduce")
    return str(expected)


def receipt_supersession(  # noqa: PLR0912, PLR0915 - atomic all-identity proof
    campaign: Path,
    verified: dict[tuple[str, str], dict[str, Any]],
    invalid: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retire an incompatible receipt only with a complete, currently verified replacement."""
    remaining = list(invalid)
    superseded: list[dict[str, Any]] = []
    for proof_path in sorted(campaign.glob("receipt-supersession-proof-*.json")):
        try:
            proof = sealed(proof_path)
            if proof["schema"] != "receipt-supersession-proof/1":
                raise ValueError("Unknown supersession proof schema")
            staged = []
            for spec in proof["receipts"]:
                oldpath = Path(spec["old_receipt"])
                old = sealed(oldpath)
                if oldpath.parent != campaign or digest(old) != spec["old_receipt_sha256"]:
                    raise ValueError("Original receipt identity differs")
                failures = [v for v in remaining if v.get("receipt") == str(oldpath)]
                if len(failures) != 1:
                    raise ValueError("Original receipt is not currently incompatible")
                original = old["mappings"]
                mapped = [x["original_identity"] for x in spec["mappings"]]
                if not original or sorted(map(digest, original)) != sorted(map(digest, mapped)):
                    raise ValueError("Supersession must cover every original identity exactly")
                replay = sealed(Path(old["study"]) / "protocol.json")
                if digest(replay) != old["protocol_sha256"]:
                    raise ValueError("Original replay protocol differs")
                if replay.get("whole_share_buys", False) and (
                    failures[0]["reason"]
                    != "Adaptive replay native engine differs from semantic proof"
                    or {x["source_protocol_sha256"] for x in original}
                    != {"167599fcbcdf17ef77097b5d7667df7f7f7471bfdbcc45f4543c8db28fab26e9"}
                ):
                    raise ValueError(
                        "Whole-share supersession needs reviewed semantic-engine failure"
                    )
                for mapping in spec["mappings"]:
                    item = mapping["original_identity"]
                    origin = sealed(Path(item["source_protocol"]))
                    key = (item["source_protocol"], item["source_arm"])
                    replacement = mapping["replacement"]
                    if verified.get(key) != replacement:
                        raise ValueError("Replacement is not the verified source identity")
                    if (
                        digest(sealed(Path(replacement["receipt"])))
                        != mapping["replacement_receipt_sha256"]
                    ):
                        raise ValueError("Replacement receipt seal differs")
                    replacement_receipt = sealed(Path(replacement["receipt"]))
                    replacement_protocol = sealed(
                        Path(replacement_receipt["study"]) / "protocol.json"
                    )
                    replacement_items = [
                        x
                        for x in replacement_receipt["mappings"]
                        if (x["source_protocol"], x["source_arm"]) == key
                    ]
                    if len(replacement_items) != 1:
                        raise ValueError("Ambiguous replacement receipt mapping")
                    replacement_alias = replacement_items[0]["replay_arm"]
                    if (
                        replacement_protocol.get("execution_cash_policies", {}).get(
                            replacement_alias
                        )
                        is not None
                    ):
                        raise ValueError("Cash-policy supersession needs separate review")
                    if (
                        digest(origin) != item["source_protocol_sha256"]
                        or source_hashes(Path(origin["root"])) != origin["source_sha256"]
                        or economic_digest(origin["configs"][item["source_arm"]])
                        != item["economic_sha256"]
                        or replacement["economic_sha256"] != item["economic_sha256"]
                        or economic_digest(replay["configs"][item["replay_arm"]])
                        != item["economic_sha256"]
                    ):
                        raise ValueError("Supersession source/config identity differs")
                    # Bare native recipes only; external execution semantics need separate review.
                    if any(
                        replacement.get(k) is not None
                        for k in (
                            "switch_gate_sha256",
                            "decision_overlay_sha256",
                            "execution_cash_policy_sha256",
                        )
                    ):
                        raise ValueError("Supersession cannot waive external execution semantics")
                    for cost in [2, 6, 10]:
                        bound = item["records"][str(cost)]
                        expected = Path(old["study"]) / f"{item['replay_arm']}-{cost}bps.json"
                        record = sealed(expected)
                        if bound["path"] != str(expected) or digest(record) != bound["sha256"]:
                            raise ValueError("Original cost evidence differs")
                staged.append(
                    {
                        **failures[0],
                        "proof": str(proof_path),
                        "proof_sha256": digest(proof),
                        "replacement_count": len(original),
                        "status": "superseded_incompatible_receipt",
                    }
                )
            if not staged or len({x["receipt"] for x in staged}) != len(staged):
                raise ValueError("Empty or duplicate supersession")
            superseded.extend(staged)
            retired = {x["receipt"] for x in staged}
            remaining = [x for x in remaining if x.get("receipt") not in retired]
        except (ValueError, OSError, KeyError, TypeError) as exc:
            remaining.append({"receipt": str(proof_path), "reason": str(exc)})
    return remaining, superseded


def verify_adaptive_native_replay(
    origin: dict[str, Any],
    replay: dict[str, Any],
    receipt: dict[str, Any],
    arm: str,
) -> bool:
    """Recognize the reviewed native family only with its immutable full-panel parity proof."""
    if origin.get("schema") != "adaptive-rotation-study/1":
        return False
    if digest(origin) != "167599fcbcdf17ef77097b5d7667df7f7f7471bfdbcc45f4543c8db28fab26e9":
        raise ValueError("Unreviewed adaptive source protocol")
    design = sealed(Path(receipt["native_semantic_design"]))
    if digest(design) != "4b46020972895a71282bcc7e351e8be068998702f74c98399e01885a9445fd92":
        raise ValueError("Adaptive semantic design differs from reviewed evidence")
    proof = sealed(Path(design["semantic_protocol_path"]))
    result = sealed(Path(design["semantic_result_path"]))
    if (
        digest(proof) != design["semantic_protocol_sha256"]
        or digest(result) != design["semantic_result_sha256"]
        or result["protocol_sha256"] != digest(proof)
        or proof["source_protocol_sha256"] != digest(origin)
        or proof["original_source_sha256"] != origin["source_sha256"]
        or proof["partition_sha256"] != replay["partition_sha256"]
        or result["all_exact"] is not True
    ):
        raise ValueError("Adaptive semantic proof source/data identity differs")

    def native(values: dict[str, str]) -> dict[str, str]:
        return {k: v for k, v in values.items() if k.startswith("src/amat/")}

    if native(proof["current_source_sha256"]) != native(replay["source_sha256"]):
        raise ValueError("Adaptive replay native engine differs from semantic proof")

    folder = Path(design["semantic_result_path"]).parent
    with (
        np.load(folder / "original.npz", allow_pickle=False) as old,
        np.load(folder / "current.npz", allow_pickle=False) as new,
    ):
        expected = {
            f"{a}_{kind}"
            for a in origin["configs"]
            for kind in ["scores", "scalars", "sessions", "enabled"]
        }
        if set(old.files) != expected or set(new.files) != expected:
            raise ValueError("Adaptive semantic arrays omit source arms")
        for key in expected:
            bound = result["arrays"][key]
            if (
                not np.array_equal(old[key], new[key])
                or hashlib.sha256(old[key].tobytes()).hexdigest() != bound["original_sha256"]
                or hashlib.sha256(new[key].tobytes()).hexdigest() != bound["current_sha256"]
            ):
                raise ValueError("Adaptive semantic array evidence differs")
    return arm in origin["configs"]


def replay_coverage(  # noqa: PLR0912, PLR0915 - one atomic receipt validation pass
    campaign: Path, records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Verify partial historical mappings; never waive unresolved study contracts."""
    comparable = {r["path"]: r for r in records if r.get("status") == "comparable"}
    verified: dict[tuple[str, str], dict[str, Any]] = {}
    invalid = []
    # Source-reviewed fixed arms; external gates and cash policies need separate proof.
    native_arms = {
        "daily-group-validation/1": {"baseline", "B1", "groups", "core_daily", "quality"},
        "daily-group-conditional-switching/1": {"core_daily"},
        "asset-percentile-sizing/1": {
            "core_daily",
            "p80_reduce",
            "p80_relative",
            "p90_reduce",
            "p90_relative",
            "p95_reduce",
            "p95_relative",
        },
        "conditional-sizing/1": {"core_daily", "weak_trend", "volatility", "either"},
        "group-conditional-sizing/1": {"core_daily", "weak_trend", "volatility", "either"},
        "asset-conditional-sizing/1": {
            "core_daily",
            "weak_reduce",
            "vol_reduce",
            "either_reduce",
            "weak_relative",
            "vol_relative",
            "either_relative",
        },
        "daily-group-switching/1": {
            "core_daily",
            "confirm2",
            "confirm3",
            "cost1",
            "cost2",
            "confirm2_cost1",
            "confirm3_cost1",
            "confirm2_cost2",
            "confirm3_cost2",
        },
        "native-trend-quality-recovery/1": {
            "core_daily",
            "agreement3",
            "shorter_trend",
            "agreement3_shorter",
        },
        # Other efficiency arms apply external target multipliers, despite equal configs.
        "daily-group-efficiency/1": {"core_daily", "band3", "band4", "band5"},
        "strategy-improvement-study/1": {"baseline", "A1", "A2", "A3", "B1", "B2", "C1", "C2"},
        # These source-reviewed arms use native configurations, with no external mask.
        "volatility-next/1": {
            "core_daily",
            "concentration_cap",
            "confirm_two",
            "inverse_volatility",
            "larger_hurdle",
            "wider_band",
        },
        "native-group-recovery/1": {
            "core_daily",
            "faster4",
            "faster6",
            "broad_benchmark",
            "all_groups",
            "faster6_spy",
            "all_groups_spy",
        },
        "all-groups-conditional-switching/1": {
            "core_daily",
            "all_groups",
            "all_always",
            "all_always_double",
        },
    }
    for receipt_path in sorted(campaign.glob("benchmark-replay-receipt-*.json")):
        try:
            receipt = sealed(receipt_path)
            replay_path = Path(receipt["study"])
            replay = sealed(replay_path / "protocol.json")
            if digest(replay) != receipt["protocol_sha256"]:
                raise ValueError("Replay protocol identity differs")
            receipt_verified = {}
            for item in receipt["mappings"]:
                origin_path = Path(item["source_protocol"])
                origin = sealed(origin_path)
                arm, alias = item["source_arm"], item["replay_arm"]
                gate = original_switch_gate(origin_path, origin, arm, replay, alias, item)
                overlay = original_decision_overlay(
                    origin_path, origin, arm, replay_path, replay, alias, item
                )
                cash_replay = origin.get("schema") == "strategy-improvement-study/1" and arm == "D1"
                if cash_replay:
                    if replay.get("execution_cash_policies", {}).get(
                        alias
                    ) != CASH_POLICY_SPEC or item.get("execution_cash_policy_sha256") != digest(
                        CASH_POLICY_SPEC
                    ):
                        raise ValueError(
                            "Historical overlay semantics require an explicit cash policy"
                        )
                elif (
                    gate is None
                    and overlay is None
                    and not verify_adaptive_native_replay(origin, replay, receipt, arm)
                    and not verify_core_group_replay(origin, replay, receipt, arm)
                    and arm
                    not in (
                        REVIEWED_CAMPAIGN_ARMS.get(digest(origin), set())
                        if origin.get("schema") == "performance-campaign-cycle/1"
                        else native_arms.get(str(origin.get("schema", "")), set())
                    )
                ):
                    raise ValueError("Historical overlay semantics require separate review")
                if digest(origin) != item["source_protocol_sha256"]:
                    raise ValueError("Historical source protocol differs")
                if source_hashes(Path(origin["root"])) != origin["source_sha256"]:
                    raise ValueError("Historical source bytes differ")
                if (
                    not cash_replay
                    and replay.get("execution_cash_policies", {}).get(alias) is not None
                ):
                    raise ValueError("Unexpected cash policy on a bare native recipe replay")
                economic = economic_digest(origin["configs"][arm])
                if economic != item["economic_sha256"] or economic != economic_digest(
                    replay["configs"][alias]
                ):
                    raise ValueError("Historical economic configuration differs")
                evidence = []
                for cost in [2, 6, 10]:
                    bound = item["records"][str(cost)]
                    expected_path = replay_path / f"{alias}-{cost}bps.json"
                    row = comparable.get(str(expected_path))
                    if (
                        bound["path"] != str(expected_path)
                        or row is None
                        or row["sha256"] != bound["sha256"]
                        or row["arm"] != alias
                        or row["cost_bps"] != cost
                    ):
                        raise ValueError("Missing or mismatched comparable replay cost evidence")
                    replay_record = sealed(expected_path)
                    if replay_record.get("decision_overlay_sha256") != overlay:
                        raise ValueError(
                            "Decision overlay differs from registered or bare native recipe"
                        )
                    if replay_record.get("switch_gate_sha256") != gate:
                        raise ValueError(
                            "External switch gate differs from registered or bare native recipe"
                        )
                    if (
                        not cash_replay
                        and replay_record.get("execution_cash_policy_sha256") is not None
                    ):
                        raise ValueError(
                            "Cash-policy record cannot stand in for a bare native recipe"
                        )
                    if cash_replay and replay_record.get("execution_cash_policy_sha256") != digest(
                        CASH_POLICY_SPEC
                    ):
                        raise ValueError("Cash-policy execution record identity differs")
                    evidence.append(row["sha256"])
                receipt_verified[(str(origin_path), arm)] = {
                    "source_protocol": str(origin_path),
                    "source_arm": arm,
                    "economic_sha256": economic,
                    "switch_gate_sha256": gate,
                    "decision_overlay_sha256": overlay,
                    "receipt": str(receipt_path),
                    "record_sha256": evidence,
                }
            verified.update(receipt_verified)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            invalid.append({"receipt": str(receipt_path), "reason": str(exc)})
    invalid, superseded = receipt_supersession(campaign, verified, invalid)
    return {
        "superseded_receipts": superseded,
        "verified_identities": list(verified.values()),
        "invalid_receipts": invalid,
        "policy": (
            "Partial source/config/record proof only; unresolved contracts still block promotion."
        ),
    }


def inventory_global_strategy_ledger(path: Path) -> dict[str, Any]:
    """Snapshot registry identities and raw metrics; never disclose stored payload text."""
    item: dict[str, Any] = {
        "database": str(path),
        "kind": "global_strategy_registry",
        "status": "requires_contract_replay",
        "experiments": [],
        "strategies": [],
        "return_references": [],
        "reason": "Global source/return registry needs contract and source review; raw annual "
        "objectives are not common-contract return floors.",
    }
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.execute("BEGIN")
        for session, candidate, group, annual, score, eligible, payload in db.execute(
            "SELECT session,candidate,comparison_group,CAST(annual_return AS TEXT),"
            "CAST(score AS TEXT),eligible,payload FROM strategies "
            "ORDER BY session,candidate,comparison_group"
        ):
            item["strategies"].append(
                {
                    "session": session,
                    "candidate": candidate,
                    "comparison_group": group,
                    "annual_return_text": annual,
                    "score_text": score,
                    "eligible": eligible,
                    "payload_sha256": hashlib.sha256(str(payload).encode()).hexdigest(),
                }
            )
        for identifier, payload in db.execute(
            "SELECT id,payload FROM return_references ORDER BY id"
        ):
            item["return_references"].append(
                {
                    "id": identifier,
                    "payload_sha256": hashlib.sha256(str(payload).encode()).hexdigest(),
                }
            )
    finally:
        db.close()
    item["logical_snapshot_sha256"] = digest(
        {"strategies": item["strategies"], "return_references": item["return_references"]}
    )
    return item


def audit(root: Path, campaign: Path, destination: Path) -> dict[str, Any]:  # noqa: PLR0912, PLR0915 - one read-only inventory pass
    """Inventory all study protocols and legacy experiment identities without writing ledgers."""
    if destination.exists():
        raise ValueError("Audit evidence already exists")
    contract = sealed(campaign / "protocol.json")
    reference = sealed(Path(contract["baseline_study"]) / "protocol.json")
    paths = list((root / "amat/outputs/analysis").rglob("protocol.json"))
    paths += (
        list((campaign / "cycles").rglob("protocol.json")) if (campaign / "cycles").exists() else []
    )
    paths = sorted(p for p in paths if "frozen" not in p.parts)
    studies: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    configs: list[dict[str, Any]] = []
    unique: dict[str, str] = {}
    frontier: dict[str, dict[str, dict[str, Any]]] = {
        str(c): {p: {"net_return": None, "references": []} for p in ["full", "walk_forward"]}
        for c in [2, 6, 10]
    }
    pending = []
    for path in paths:
        info: dict[str, Any] = {"path": str(path), "records": 0, "status": "requires_replay"}
        try:
            protocol = sealed(path)
            info.update(schema=protocol.get("schema"), protocol_sha256=digest(protocol))
            if protocol.get("schema") in {
                "adaptive-semantic-probe-protocol/1",
                "core-source-semantic-probe-protocol/1",
            }:
                # A source signal diagnostic has no returns and cannot resolve a strategy.
                if protocol.get("configs") or list(path.parent.glob("*bps*.json")):
                    raise ValueError("Signal diagnostic cannot carry strategy return evidence")
                result = sealed(path.parent / "result.json")
                if result.get("protocol_sha256") != digest(protocol):
                    raise ValueError("Signal diagnostic result protocol differs")
                info.update(
                    status="non_return_signal_diagnostic",
                    result_sha256=digest(result),
                    all_exact=result.get("all_exact") is True,
                    reason="Signal parity only; no return frontier or source coverage waiver",
                )
                studies.append(info)
                continue
            for arm, cfg in protocol.get("configs", {}).items():
                with contextlib.suppress(ValueError, TypeError):
                    configs.append(
                        {
                            "study": str(path.parent),
                            "arm": arm,
                            "economic_sha256": economic_digest(cfg),
                            "execution_sha256": execution_identity(
                                cfg,
                                protocol.get("execution_cash_policies", {}).get(arm),
                                whole_share_buys=protocol.get("whole_share_buys_by_arm", {}).get(
                                    arm, protocol.get("whole_share_buys", False)
                                ),
                            ),
                        }
                    )
            checks = {
                "native_ledger_schema": protocol.get("schema") in NATIVE_SCHEMAS,
                "data": protocol.get("partition_sha256") == reference["partition_sha256"],
                "start": protocol.get("execution_start") == contract["execution_start"],
                "cash": protocol.get("initial_cash") == contract["initial_cash"],
                "participation": protocol.get("participation_cap")
                == contract["entry_opening_volume_cap"],
                "whole_share_buys": protocol.get("whole_share_buys") is True,
                "complete": (path.parent / "report.md").is_file(),
                "source": bool(protocol.get("source_sha256"))
                and source_hashes(Path(protocol.get("root", "/missing")))
                == protocol.get("source_sha256"),
            }
            info["contract_checks"] = checks
            comparable = all(checks.values())
            info["status"] = "comparable_source_verified" if comparable else "requires_replay"
            if not comparable:
                pending.append(
                    {
                        "study": str(path.parent),
                        "reason": "; ".join(k for k, v in checks.items() if not v),
                    }
                )
            for record_path in sorted(path.parent.glob("*bps*.json")):
                entry: dict[str, Any] = {
                    "path": str(record_path),
                    "study": str(path.parent),
                    "status": "incompatible",
                }
                try:
                    record = sealed(record_path)
                    if record.get("protocol_sha256") != digest(protocol):
                        raise ValueError("Record protocol differs")
                    entry.update(
                        sha256=digest(record),
                        arm=record.get("arm"),
                        cost_bps=record.get("cost_bps"),
                        switch_gate_sha256=record.get("switch_gate_sha256"),
                        decision_overlay_sha256=record.get("decision_overlay_sha256"),
                        execution_cash_policy_sha256=record.get("execution_cash_policy_sha256"),
                        whole_share_buys=protocol.get("whole_share_buys_by_arm", {}).get(
                            record.get("arm"), protocol.get("whole_share_buys", False)
                        ),
                    )
                    metrics = record.get("metrics", {})
                    if all(p in metrics for p in ["full", "walk_forward"]):
                        entry["returns"] = {
                            p: metrics[p]["net_return"] for p in ["full", "walk_forward"]
                        }
                    rows = record.get("sessions", [])
                    if (
                        comparable
                        and protocol.get("whole_share_buys_by_arm", {}).get(record.get("arm"), True)
                        is True
                        and rows
                        and entry.get("cost_bps") in [2, 6, 10]
                        and entry.get("returns")
                    ):
                        entry["status"] = "comparable"
                        history = digest(rows)
                        entry["sessions_sha256"] = history
                        entry["duplicate_of"] = unique.get(history)
                        unique.setdefault(history, str(record_path))
                        for period in ["full", "walk_forward"]:
                            item = frontier[str(entry["cost_bps"])][period]
                            value = metrics[period]["net_return"]
                            if item["net_return"] is None or value > item["net_return"]:
                                item.update(net_return=value, references=[str(record_path)])
                            elif value == item["net_return"]:
                                item["references"].append(str(record_path))
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    entry.update(status="invalid_or_unverifiable", reason=str(exc))
                    pending.append({"record": str(record_path), "reason": str(exc)})
                records.append(entry)
                info["records"] += 1
        except (ValueError, OSError, KeyError, TypeError) as exc:
            info.update(status="invalid_or_unverifiable", reason=str(exc))
            pending.append({"study": str(path.parent), "reason": str(exc)})
        studies.append(info)
    # Ledger SQL is read-only; no provider requests/responses or secrets are queried.
    legacy = []
    external = root.parent / "amat-research"
    for database in sorted(external.glob("*/research.sqlite3")):
        ledger_item: dict[str, Any] = {
            "database": str(database),
            "status": "requires_contract_replay",
            "experiments": [],
        }
        try:
            db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                db.execute("BEGIN")
                for identifier, candidate, status, result in db.execute(
                    "SELECT id,candidate,status,result FROM experiments ORDER BY id"
                ):
                    ledger_item["experiments"].append(
                        {
                            "id": identifier,
                            "candidate": candidate,
                            "status": status,
                            "result_sha256": digest(json.loads(result)) if result else None,
                        }
                    )
            finally:
                db.close()
            program_path = database.parent / "program.json"
            if program_path.is_file():
                program = json.loads(program_path.read_text())
                ledger_item["program_sha256"] = digest(program)
                ledger_item["program_fields"] = sorted(program)
            ledger_item["reason"] = (
                "Legacy feature/intraday evaluation and data need contract review or replay; "
                "raw objectives are not common-contract floors."
            )
        except (sqlite3.Error, ValueError, OSError) as exc:
            ledger_item.update(status="unverifiable", reason=str(exc))
        legacy.append(ledger_item)
    global_ledger = external / "best-strategies.sqlite3"
    if global_ledger.is_file():
        try:
            legacy.append(inventory_global_strategy_ledger(global_ledger))
        except (sqlite3.Error, OSError, ValueError) as exc:
            legacy.append(
                {
                    "database": str(global_ledger),
                    "status": "unverifiable",
                    "reason": str(exc),
                    "kind": "global_strategy_registry",
                }
            )
    replay = replay_coverage(campaign, records)
    resolved = []
    by_origin: dict[str, set[str]] = {}
    for identity in replay["verified_identities"]:
        by_origin.setdefault(identity["source_protocol"], set()).add(identity["source_arm"])
    for origin_path, arms in by_origin.items():
        origin = sealed(Path(origin_path))
        if origin.get("configs") and set(origin["configs"]) <= arms:
            resolved.append(
                {
                    "source_protocol": origin_path,
                    "source_protocol_sha256": digest(origin),
                    "arms": sorted(arms),
                    "reason": "Every original arm has a source-bound common-contract replay",
                }
            )
    resolved_paths = {item["source_protocol"] for item in resolved}
    pending = [
        item
        for item in pending
        if str(Path(item.get("study", "/unresolved")) / "protocol.json") not in resolved_paths
    ]
    for item in studies:
        if item["path"] in resolved_paths:
            item["status"] = "replayed_under_common_contract"
    pending.extend(replay["invalid_receipts"])
    report = {
        "resolved_historical_contracts": resolved,
        "replay_coverage": replay,
        "schema": "campaign-evidence-audit/1",
        "recorded_utc": dt.datetime.now(dt.UTC).isoformat(),
        "campaign_protocol_sha256": digest(contract),
        "reference_protocol_sha256": digest(reference),
        "studies": studies,
        "records": records,
        "economic_configs": configs,
        "frontier": frontier,
        "unique_comparable_histories": len(unique),
        "legacy_ledgers": legacy,
        "pending_replay_or_review": pending,
        "coverage_complete": not pending and not legacy,
        "promotion_block": "Historical contract/replay coverage remains unresolved"
        if pending or legacy
        else None,
        "scope": (
            "Conservative source/record-seal and execution-contract audit; no claim of "
            "complete independent trade-accounting replay. All rejected arms retained. "
            "No source text or ledger prose is treated as instructions."
        ),
    }
    write(destination, report)
    destination.with_suffix(".sha256").write_text(digest(report))
    return report
