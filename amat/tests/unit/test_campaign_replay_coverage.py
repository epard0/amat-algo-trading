"""Source-bound replay receipts cannot silently bless missing costs or overlay arms."""

from copy import deepcopy
from pathlib import Path

from autoresearch.improvements.campaign_audit import replay_coverage, sealed
from autoresearch.improvements.study import digest, write

CAMPAIGN = Path("outputs/research-campaigns/2026-10-19-12h-performance")


def test_actual_partial_receipt_preserves_five_identities(tmp_path):
    audit = sealed(CAMPAIGN / "audit-003-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-003.json")
    path = tmp_path / "benchmark-replay-receipt-003.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert len(result["verified_identities"]) == 5
    assert not result["invalid_receipts"]
    assert {r["source_arm"] for r in result["verified_identities"]} == {
        "baseline",
        "B1",
        "groups",
        "core_daily",
        "quality",
    }


def test_resigned_receipt_cannot_hide_a_missing_cost_or_overlay(tmp_path):
    audit = sealed(CAMPAIGN / "audit-003-post-replay.json")
    original = sealed(CAMPAIGN / "benchmark-replay-receipt-003.json")
    for mutation in ["cost", "overlay", "economic"]:
        receipt = deepcopy(original)
        if mutation == "cost":
            receipt["mappings"][-1]["records"]["10"]["sha256"] = "0" * 64
        elif mutation == "overlay":
            receipt["mappings"][-1]["source_arm"] = "rules"
        else:
            receipt["mappings"][-1]["economic_sha256"] = "0" * 64
        path = tmp_path / "benchmark-replay-receipt-forged.json"
        write(path, receipt)
        path.with_suffix(".sha256").write_text(digest(receipt))
        result = replay_coverage(tmp_path, audit["records"])
        assert not result["verified_identities"]
        assert len(result["invalid_receipts"]) == 1


def test_v13_receipt_covers_native_arms_but_not_cash_policy(tmp_path):
    audit = sealed(CAMPAIGN / "audit-005-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-005.json")
    path = tmp_path / "benchmark-replay-receipt-005.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert len(result["verified_identities"]) == 8
    assert not result["invalid_receipts"]
    receipt["mappings"][-1]["source_arm"] = "D1"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["verified_identities"]
    assert "overlay semantics" in result["invalid_receipts"][0]["reason"]


def test_corrected_contract_preserves_legacy_evidence_but_resolves_v13_replays():
    audit = sealed(CAMPAIGN / "audit-006-post-correction.json")
    assert not audit["coverage_complete"]
    assert len(audit["replay_coverage"]["verified_identities"]) == 9
    resolved = audit["resolved_historical_contracts"]
    assert len(resolved) == 1
    assert set(resolved[0]["arms"]) == {"baseline", "A1", "A2", "A3", "B1", "B2", "C1", "C2", "D1"}
    diagnostics = [row for row in audit["records"] if row.get("arm") == "fractional_reference"]
    assert len(diagnostics) == 3
    assert all(row["status"] != "comparable" for row in diagnostics)


def test_frontier_and_reused_receipts_verify_without_hiding_source_identities(tmp_path):
    audit = sealed(CAMPAIGN / "audit-007-post-replay.json")
    for suffix in ["006", "007", "007-reused"]:
        receipt = sealed(CAMPAIGN / f"benchmark-replay-receipt-{suffix}.json")
        path = tmp_path / f"benchmark-replay-receipt-{suffix}.json"
        write(path, receipt)
        path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 22
    assert len(audit["resolved_historical_contracts"]) == 2
    assert not audit["coverage_complete"]


def test_same_config_external_gate_cannot_masquerade_as_always_on_replay(tmp_path):
    from autoresearch.improvements.campaign_audit import economic_digest

    audit = sealed(CAMPAIGN / "audit-007-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-007.json")
    mapping = next(item for item in receipt["mappings"] if item["source_arm"] == "all_always")
    origin = sealed(Path(mapping["source_protocol"]))
    # These two source-produced strategies have identical configs but different masks.
    assert economic_digest(origin["configs"]["all_always"]) == economic_digest(
        origin["configs"]["all_combined"]
    )
    mapping["source_arm"] = "all_combined"
    path = tmp_path / "benchmark-replay-receipt-forged.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["verified_identities"]
    assert "overlay semantics" in result["invalid_receipts"][0]["reason"]


def test_same_config_cash_budget_cannot_masquerade_as_native_reference(tmp_path):
    from autoresearch.improvements.campaign_audit import economic_digest

    audit = sealed(CAMPAIGN / "audit-007-post-replay.json")
    source = sealed(CAMPAIGN / "benchmark-replay-receipt-007.json")
    mapping = deepcopy(
        next(item for item in source["mappings"] if item["source_arm"] == "core_daily")
    )
    study = CAMPAIGN.resolve() / "cycles/006-whole-share-contract/study"
    replay_protocol = sealed(study / "protocol.json")
    origin = sealed(Path(mapping["source_protocol"]))
    assert economic_digest(origin["configs"]["core_daily"]) == economic_digest(
        replay_protocol["configs"]["proportional_budget"]
    )
    mapping["replay_arm"] = "proportional_budget"
    mapping["records"] = {
        str(cost): {
            "path": str(study / f"proportional_budget-{cost}bps.json"),
            "sha256": digest(sealed(study / f"proportional_budget-{cost}bps.json")),
        }
        for cost in [2, 6, 10]
    }
    receipt = {
        "study": str(study),
        "protocol_sha256": digest(replay_protocol),
        "mappings": [mapping],
    }
    path = tmp_path / "benchmark-replay-receipt-forged.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["verified_identities"]
    assert "cash policy" in result["invalid_receipts"][0]["reason"]


def test_complete_group_family_reuses_references_without_dropping_duplicate_identity(tmp_path):
    audit = sealed(CAMPAIGN / "audit-008-final.json")
    for suffix in ["006", "007", "007-reused", "008", "008-reused-0", "008-reused-1"]:
        receipt = sealed(CAMPAIGN / f"benchmark-replay-receipt-{suffix}.json")
        path = tmp_path / f"benchmark-replay-receipt-{suffix}.json"
        write(path, receipt)
        path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 31
    assert len(audit["resolved_historical_contracts"]) == 3
    study = CAMPAIGN / "cycles/008-group-family-replay/study"
    for cost in [2, 6, 10]:
        qqq = sealed(study / f"group_all_groups-{cost}bps.json")
        spy = sealed(study / f"group_all_groups_spy-{cost}bps.json")
        assert digest(qqq["sessions"]) == digest(spy["sessions"])
    source_arms = {item["source_arm"] for item in result["verified_identities"]}
    assert {"all_groups", "all_groups_spy"} <= source_arms


def test_original_mask_receipt_requires_registered_and_actual_gate_identity(tmp_path):
    audit = sealed(CAMPAIGN / "audit-009-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-009.json")
    path = tmp_path / "benchmark-replay-receipt-009.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 4
    for identity in result["verified_identities"]:
        assert identity["switch_gate_sha256"]
    for mutation in ["receipt_gate", "same_config_bare_arm", "missing_cost"]:
        forged = deepcopy(receipt)
        item = forged["mappings"][-1]
        if mutation == "receipt_gate":
            item["switch_gate_sha256"] = "0" * 64
        elif mutation == "same_config_bare_arm":
            # conditional source all_combined has the same config as all_always.
            item = next(x for x in forged["mappings"] if x["source_arm"] == "all_combined")
            item["source_arm"] = "all_always"
        else:
            del item["records"]["10"]
        write(path, forged)
        path.with_suffix(".sha256").write_text(digest(forged))
        result = replay_coverage(tmp_path, audit["records"])
        assert not result["verified_identities"]
        assert len(result["invalid_receipts"]) == 1


def test_original_volatility_mask_has_native_default_history_parity():
    study = CAMPAIGN / "cycles/009-original-condition-masks/study"
    for cost in [2, 6, 10]:
        native = sealed(study / f"core_daily-{cost}bps.json")
        external = sealed(study / f"all_volatility-{cost}bps.json")
        assert digest(native["sessions"]) == digest(external["sessions"])
        assert native["fractional_buy_count"] == external["fractional_buy_count"] == 0
    audit = sealed(CAMPAIGN / "audit-009-post-replay.json")
    assert len(audit["replay_coverage"]["verified_identities"]) == 35
    assert len(audit["resolved_historical_contracts"]) == 4
    assert not audit["coverage_complete"]


def test_mask_receipt_refuses_changed_recipe_or_registration():
    import pytest

    from autoresearch.improvements.campaign_audit import original_switch_gate

    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-009.json")
    item = receipt["mappings"][0]
    origin_path = Path(item["source_protocol"])
    origin = sealed(origin_path)
    replay = sealed(Path(receipt["study"]) / "protocol.json")
    assert (
        original_switch_gate(
            origin_path, origin, item["source_arm"], replay, item["replay_arm"], item
        )
        == item["switch_gate_sha256"]
    )
    for mutation in ["recipe", "registration"]:
        changed = deepcopy(replay)
        if mutation == "recipe":
            changed["source_sha256"][
                "src/autoresearch/improvements/conditional_switching_study.py"
            ] = "0" * 64
        else:
            changed["registered_switch_gate_sha256"][item["replay_arm"]] = "0" * 64
        with pytest.raises(ValueError):
            original_switch_gate(
                origin_path, origin, item["source_arm"], changed, item["replay_arm"], item
            )


def test_two_group_condition_family_has_all_nine_source_bound_replays(tmp_path):
    audit = sealed(CAMPAIGN / "audit-010-post-replay.json")
    receipts = ["010", "010-reused-0", "010-reused-1"]
    for suffix in receipts:
        receipt = sealed(CAMPAIGN / f"benchmark-replay-receipt-{suffix}.json")
        path = tmp_path / f"benchmark-replay-receipt-{suffix}.json"
        write(path, receipt)
        path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 9
    assert {x["source_arm"] for x in result["verified_identities"]} == {
        "core_daily",
        "volatility",
        "liquidity",
        "churn",
        "inefficient",
        "vol_or_liquidity",
        "vol_and_liquidity",
        "combined",
        "combined_double",
    }
    assert len(audit["replay_coverage"]["verified_identities"]) == 44
    assert len(audit["resolved_historical_contracts"]) == 5
    assert not audit["coverage_complete"]


def test_same_config_two_group_gates_cannot_be_interchanged(tmp_path):
    from autoresearch.improvements.campaign_audit import economic_digest

    audit = sealed(CAMPAIGN / "audit-010-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-010.json")
    item = next(x for x in receipt["mappings"] if x["source_arm"] == "churn")
    origin = sealed(Path(item["source_protocol"]))
    assert economic_digest(origin["configs"]["churn"]) == economic_digest(
        origin["configs"]["liquidity"]
    )
    item["source_arm"] = "liquidity"
    path = tmp_path / "benchmark-replay-receipt-forged.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["verified_identities"]
    assert "not registered as original" in result["invalid_receipts"][0]["reason"]


def test_native_switching_quality_and_static_efficiency_replay_identities(tmp_path):
    audit = sealed(CAMPAIGN / "audit-011-post-replay.json")
    for suffix in ["011", "011-reused-0"]:
        receipt = sealed(CAMPAIGN / f"benchmark-replay-receipt-{suffix}.json")
        path = tmp_path / f"benchmark-replay-receipt-{suffix}.json"
        write(path, receipt)
        path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 17
    assert len(audit["replay_coverage"]["verified_identities"]) == 61
    assert len(audit["resolved_historical_contracts"]) == 7
    assert not audit["coverage_complete"]
    efficiency = [
        x for x in result["verified_identities"] if "group-efficiency" in x["source_protocol"]
    ]
    assert {x["source_arm"] for x in efficiency} == {"core_daily", "band3", "band4", "band5"}


def test_group_risk_overlay_cannot_reuse_equal_unfiltered_core_configuration(tmp_path):
    from autoresearch.improvements.campaign_audit import economic_digest

    audit = sealed(CAMPAIGN / "audit-011-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-011-reused-0.json")
    item = next(x for x in receipt["mappings"] if "group-efficiency" in x["source_protocol"])
    origin = sealed(Path(item["source_protocol"]))
    assert economic_digest(origin["configs"]["core_daily"]) == economic_digest(
        origin["configs"]["group_trend"]
    )
    item["source_arm"] = "group_trend"
    path = tmp_path / "benchmark-replay-receipt-forged.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["verified_identities"]
    assert "overlay semantics" in result["invalid_receipts"][0]["reason"]


def test_group_overlay_replays_require_original_fractional_semantic_controls(tmp_path):
    audit = sealed(CAMPAIGN / "audit-012-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-012.json")
    path = tmp_path / "benchmark-replay-receipt-012.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 5
    assert all(x["decision_overlay_sha256"] for x in result["verified_identities"])
    assert len(audit["replay_coverage"]["verified_identities"]) == 66
    assert len(audit["resolved_historical_contracts"]) == 8
    probes = [x for x in audit["records"] if str(x.get("arm", "")).startswith("legacy_")]
    assert len(probes) == 15
    assert all(x["status"] != "comparable" for x in probes)
    assert not audit["coverage_complete"]
    forged = deepcopy(receipt)
    item = next(x for x in forged["mappings"] if x["source_arm"] == "group_shock")
    item["source_arm"] = "core_daily"  # Same config, different actual risk array.
    write(path, forged)
    path.with_suffix(".sha256").write_text(digest(forged))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["verified_identities"]
    assert "Decision overlay differs" in result["invalid_receipts"][0]["reason"]


def test_resigning_a_false_fractional_semantic_control_does_not_bless_overlay(tmp_path):
    import pytest

    from autoresearch.improvements.campaign_audit import original_decision_overlay

    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-012.json")
    item = receipt["mappings"][0]
    source_path = Path(item["source_protocol"])
    origin = sealed(source_path)
    replay_path = Path(receipt["study"])
    replay = sealed(replay_path / "protocol.json")
    probe = item["legacy_diagnostic_arm"]
    for cost in [2, 6, 10]:
        value = sealed(replay_path / f"{probe}-{cost}bps.json")
        if cost == 10:
            value["sessions"][-1]["equity"] += 1
        path = tmp_path / f"{probe}-{cost}bps.json"
        write(path, value)
        path.with_suffix(".sha256").write_text(digest(value))
    with pytest.raises(ValueError, match="fractional overlay semantics did not reproduce"):
        original_decision_overlay(
            source_path, origin, item["source_arm"], tmp_path, replay, item["replay_arm"], item
        )


def test_native_sizing_families_preserve_original_identities_and_shared_controls(tmp_path):
    audit = sealed(CAMPAIGN / "audit-013-post-replay.json")
    for suffix in ["013", "013-reused-0"]:
        receipt = sealed(CAMPAIGN / f"benchmark-replay-receipt-{suffix}.json")
        path = tmp_path / f"benchmark-replay-receipt-{suffix}.json"
        write(path, receipt)
        path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 15
    assert sum(x["source_arm"] == "core_daily" for x in result["verified_identities"]) == 3
    assert len(audit["replay_coverage"]["verified_identities"]) == 81
    assert len(audit["resolved_historical_contracts"]) == 11
    assert not audit["coverage_complete"]


def test_relative_sizing_cannot_masquerade_as_reduce_only_recipe(tmp_path):
    audit = sealed(CAMPAIGN / "audit-013-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-013.json")
    item = next(x for x in receipt["mappings"] if x["source_arm"] == "vol_relative")
    item["source_arm"] = "vol_reduce"
    path = tmp_path / "benchmark-replay-receipt-forged.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["verified_identities"]
    assert "economic configuration differs" in result["invalid_receipts"][0]["reason"]


def test_original_percentile_and_early_campaign_replays_are_source_bound(tmp_path):
    audit = sealed(CAMPAIGN / "audit-014-post-replay.json")
    for suffix in ["014", "014-reused-0"]:
        receipt = sealed(CAMPAIGN / f"benchmark-replay-receipt-{suffix}.json")
        path = tmp_path / f"benchmark-replay-receipt-{suffix}.json"
        write(path, receipt)
        path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 17
    assert sum(x["source_arm"] == "core_daily" for x in result["verified_identities"]) == 6
    assert len(audit["replay_coverage"]["verified_identities"]) == 98
    assert len(audit["resolved_historical_contracts"]) == 17
    assert not audit["coverage_complete"]


def test_generic_campaign_schema_and_known_arm_do_not_bypass_exact_source_review(tmp_path):
    audit = sealed(CAMPAIGN / "audit-014-post-replay.json")
    receipt = sealed(CAMPAIGN / "benchmark-replay-receipt-014.json")
    item = next(x for x in receipt["mappings"] if x["source_arm"] == "six_positions")
    origin = sealed(Path(item["source_protocol"]))
    assert origin["schema"] == "performance-campaign-cycle/1"
    origin["labels"]["six_positions"] += " modified source protocol"
    source_path = tmp_path / "source/protocol.json"
    source_path.parent.mkdir()
    write(source_path, origin)
    source_path.with_suffix(".sha256").write_text(digest(origin))
    item["source_protocol"] = str(source_path)
    item["source_protocol_sha256"] = digest(origin)
    path = tmp_path / "benchmark-replay-receipt-forged.json"
    write(path, receipt)
    path.with_suffix(".sha256").write_text(digest(receipt))
    result = replay_coverage(tmp_path, audit["records"])
    assert not result["verified_identities"]
    assert "overlay semantics" in result["invalid_receipts"][0]["reason"]
