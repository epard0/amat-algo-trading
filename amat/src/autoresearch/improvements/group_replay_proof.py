"""The reviewed original core group recipes require their full feature parity evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from autoresearch.improvements.study import digest

CORE_ARMS = frozenset({"baseline", "B1", "groups", "core_daily", "core_quality", "core_both"})
SOURCE_SHA = "bb88cc2268c9ec293072dfad2fd9f54e6f9d524779dd267b176781d750cfdc4d"
PROOF_SHA = "3f965eb28665c87dbb7db9bab8cc319492b69d73306d4d48f487686c143947c7"
RESULT_SHA = "dc19d02973c3eceba7e3b760bcd089157485636fbf09c5a3c4521621d24f0dc6"
FIELDS = (
    "returns",
    "sigma_panel",
    "long_scores",
    "horizon_scores",
    "structure_multipliers",
    "adaptive_scores",
    "adaptive_scalars",
    "sessions",
)


def verify_core_group_replay(
    origin: dict[str, Any],
    replay: dict[str, Any],
    receipt: dict[str, Any],
    arm: str,
) -> bool:
    """Authorize only six fixed native arms; broader arms remain unresolved."""
    if origin.get("schema") != "group-selection-study/1":
        return False
    if digest(origin) != SOURCE_SHA:
        raise ValueError("Unreviewed group source protocol")
    if arm not in CORE_ARMS:
        return False
    from autoresearch.improvements.campaign_audit import (  # noqa: PLC0415
        sealed,
    )

    folder = Path(receipt["core_semantic_folder"])
    proof = sealed(folder / "protocol.json")
    result = sealed(folder / "result.json")
    if digest(proof) != PROOF_SHA or digest(result) != RESULT_SHA:
        raise ValueError("Core group semantic evidence differs")
    if (
        result["protocol_sha256"] != digest(proof)
        or proof["source_protocol_sha256"] != digest(origin)
        or proof["original_source_sha256"] != origin["source_sha256"]
        or proof["partition_sha256"] != replay["partition_sha256"]
        or result["all_exact"] is not True
    ):
        raise ValueError("Core group source/data identity differs")

    def native(values: dict[str, str]) -> dict[str, str]:
        return {k: v for k, v in values.items() if k.startswith("src/amat/")}

    if native(proof["current_source_sha256"]) != native(replay["source_sha256"]):
        raise ValueError("Core group replay native engine differs")
    expected = {f"{a}_{field}" for a in CORE_ARMS for field in FIELDS}
    with (
        np.load(folder / "original.npz", allow_pickle=False) as old,
        np.load(folder / "current.npz", allow_pickle=False) as new,
    ):
        if set(old.files) != expected or set(new.files) != expected:
            raise ValueError("Core group arrays omit reviewed feature families")
        for key in expected:
            bound = result["arrays"][key]
            if (
                old[key].shape != new[key].shape
                or old[key].dtype != new[key].dtype
                or old[key].tobytes() != new[key].tobytes()
                or hashlib.sha256(old[key].tobytes()).hexdigest() != bound["original_sha256"]
                or hashlib.sha256(new[key].tobytes()).hexdigest() != bound["current_sha256"]
            ):
                raise ValueError("Core group feature array differs")
    return True
