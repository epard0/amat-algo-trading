"""Source-proof receipts cannot grant missing wider-asset coverage or relabel returns."""

from copy import deepcopy
from pathlib import Path

import pytest

from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.group_replay_proof import verify_core_group_replay

C = Path(__file__).resolve().parents[2] / "outputs/research-campaigns/2026-10-19-12h-performance"


@pytest.mark.parametrize("mutation", ["none", "source", "engine", "data", "wide", "unknown"])
def test_core_receipt_scope_and_identity(mutation: str) -> None:
    design = sealed(C / "broad-universe-replay-design-019.json")
    origin = sealed(Path(design["source_protocol"]))
    folder = C / "cycles/026-core-source-coverage/semantic"
    proof = sealed(folder / "protocol.json")
    replay = {
        "partition_sha256": deepcopy(proof["partition_sha256"]),
        "source_sha256": deepcopy(proof["current_source_sha256"]),
    }
    arm = "core_quality"
    if mutation == "source":
        origin["configs"][arm]["name"] = "forged"
    elif mutation == "engine":
        key = next(k for k in replay["source_sha256"] if k.startswith("src/amat/"))
        replay["source_sha256"][key] = "0" * 64
    elif mutation == "data":
        replay["partition_sha256"] = {}
    elif mutation == "wide":
        arm = "wide_both"
    elif mutation == "unknown":
        arm = "new_overlay"
    receipt = {"core_semantic_folder": str(folder)}
    if mutation in {"source", "engine", "data"}:
        with pytest.raises(ValueError):
            verify_core_group_replay(origin, replay, receipt, arm)
    else:
        assert verify_core_group_replay(origin, replay, receipt, arm) == (mutation == "none")


@pytest.mark.parametrize("mutation", ["array", "resigned_proof"])
def test_modified_core_evidence_is_rejected(tmp_path: Path, mutation: str) -> None:
    import shutil

    import numpy as np

    from autoresearch.improvements.study import digest, write

    folder = C / "cycles/026-core-source-coverage/semantic"
    target = tmp_path / "proof"
    shutil.copytree(folder, target)
    design = sealed(C / "broad-universe-replay-design-019.json")
    origin = sealed(Path(design["source_protocol"]))
    proof = sealed(target / "protocol.json")
    replay = {
        "partition_sha256": proof["partition_sha256"],
        "source_sha256": proof["current_source_sha256"],
    }
    if mutation == "array":
        with np.load(target / "current.npz", allow_pickle=False) as arrays:
            changed = {key: arrays[key].copy() for key in arrays.files}
        changed["core_quality_long_scores"][500, 0] += 1
        np.savez_compressed(target / "current.npz", **changed)
    else:
        proof["scope"] = "relabelled"
        write(target / "protocol.json", proof)
        (target / "protocol.sha256").write_text(digest(proof))
    with pytest.raises(ValueError):
        verify_core_group_replay(
            origin, replay, {"core_semantic_folder": str(target)}, "core_quality"
        )
