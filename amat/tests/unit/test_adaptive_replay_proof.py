"""Historical adaptive recipes require the reviewed original/current semantic evidence."""

from copy import deepcopy
from pathlib import Path

import pytest

from autoresearch.improvements.campaign_audit import sealed, verify_adaptive_native_replay
from autoresearch.improvements.study import digest, write

C = Path(__file__).resolve().parents[2] / "outputs/research-campaigns/2026-10-19-12h-performance"


@pytest.mark.parametrize("mutation", ["none", "source", "engine", "data", "design", "unknown_arm"])
def test_adaptive_native_receipt_proof_cannot_be_relabelled(tmp_path, mutation):
    design_path = C / "native-adaptive-replay-design-018.json"
    design = sealed(design_path)
    origin = sealed(Path(design["source_protocol"]))
    proof = sealed(Path(design["semantic_protocol_path"]))
    replay = {
        "partition_sha256": deepcopy(proof["partition_sha256"]),
        "source_sha256": deepcopy(proof["current_source_sha256"]),
    }
    receipt = {"native_semantic_design": str(design_path)}
    arm = "combined"
    if mutation == "source":
        origin["configs"]["combined"]["name"] = "forged"
    elif mutation == "engine":
        key = next(k for k in replay["source_sha256"] if k.startswith("src/amat/"))
        replay["source_sha256"][key] = "0" * 64
    elif mutation == "data":
        replay["partition_sha256"] = {}
    elif mutation == "design":
        design["semantic_result_sha256"] = "0" * 64
        path = tmp_path / "resigned-design.json"
        write(path, design)
        path.with_suffix(".sha256").write_text(digest(design))
        receipt["native_semantic_design"] = str(path)
    elif mutation == "unknown_arm":
        arm = "unreviewed_overlay"
    if mutation == "unknown_arm":
        assert not verify_adaptive_native_replay(origin, replay, receipt, arm)
    elif mutation == "none":
        assert verify_adaptive_native_replay(origin, replay, receipt, arm)
    else:
        with pytest.raises(ValueError):
            verify_adaptive_native_replay(origin, replay, receipt, arm)


@pytest.mark.parametrize(
    "reason",
    ["Adaptive replay native engine differs from semantic proof", "unrelated validation failure"],
)
def test_only_reviewed_engine_failure_can_be_superseded(tmp_path, reason):
    from autoresearch.improvements.campaign_audit import receipt_supersession

    old = sealed(C / "benchmark-replay-receipt-019-0.json")
    replacement_path = C / "benchmark-replay-receipt-019-repair.json"
    replacement = sealed(replacement_path)
    verified = {}
    mappings = []
    for item in old["mappings"]:
        current = next(x for x in replacement["mappings"] if x["source_arm"] == item["source_arm"])
        row = {k: current[k] for k in ["source_protocol", "source_arm", "economic_sha256"]}
        row.update(
            receipt=str(replacement_path),
            switch_gate_sha256=None,
            decision_overlay_sha256=None,
            record_sha256=[current["records"][str(c)]["sha256"] for c in [2, 6, 10]],
        )
        verified[(row["source_protocol"], row["source_arm"])] = row
        mappings.append(
            {
                "original_identity": item,
                "replacement": row,
                "replacement_receipt_sha256": digest(replacement),
            }
        )
    oldpath = tmp_path / "benchmark-replay-receipt-old.json"
    write(oldpath, old)
    oldpath.with_suffix(".sha256").write_text(digest(old))
    proof = {
        "schema": "receipt-supersession-proof/1",
        "receipts": [
            {"old_receipt": str(oldpath), "old_receipt_sha256": digest(old), "mappings": mappings}
        ],
    }
    path = tmp_path / "receipt-supersession-proof-native.json"
    write(path, proof)
    path.with_suffix(".sha256").write_text(digest(proof))
    pending, retired = receipt_supersession(
        tmp_path, verified, [{"receipt": str(oldpath), "reason": reason}]
    )
    if reason.startswith("Adaptive"):
        assert not pending and len(retired) == 1
        assert retired[0]["replacement_count"] == 3
        assert retired[0]["reason"] == reason
    else:
        assert not retired and len(pending) == 2


def test_all_ten_original_identities_have_verified_corrected_cost_evidence(tmp_path):
    from autoresearch.improvements.campaign_audit import replay_coverage

    evidence = sealed(C / "audit-019-verified.json")
    for suffix in ["1", "2", "repair"]:
        value = sealed(C / f"benchmark-replay-receipt-019-{suffix}.json")
        path = tmp_path / f"benchmark-replay-receipt-{suffix}.json"
        write(path, value)
        path.with_suffix(".sha256").write_text(digest(value))
    result = replay_coverage(tmp_path, evidence["records"])
    assert not result["invalid_receipts"]
    assert len(result["verified_identities"]) == 10
    assert {x["source_arm"] for x in result["verified_identities"]} == {
        "baseline",
        "B1",
        "groups",
        "allocation",
        "combined",
        "daily_buffer",
        "hysteresis",
        "regime",
        "residual",
        "structure",
    }
