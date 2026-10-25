"""Verify necessary legacy fit evidence without fitting models or granting causality."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from autoresearch.improvements.nested_inventory import envelope, file_hash, within


def compact(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def inspect_fit(  # noqa: PLR0912 - explicit independent evidence checks
    folder: Path, protocol: dict[str, Any], calendar: list[str], template: dict[str, Any]
) -> dict[str, Any]:
    """Check declared dates and cryptographic links; physical fitting is a separate test."""
    receipt = envelope(within(folder, folder / "receipt.json"))
    fold = receipt["fold"]
    if fold not in protocol["folds"] or not (
        fold["train_end"] < fold["selection_end"] < fold["test_end"]
    ):
        raise ValueError("Unregistered or nonchronological fit fold")
    if receipt["training_only"] is not True or receipt["training_end"] > fold["train_end"]:
        raise ValueError("Declared fit training cutoff is not past-only")
    if (
        receipt["training_end"] not in calendar
        or receipt["last_eligible_feature_date"] not in calendar
    ):
        raise ValueError("Declared fit dates are not observed sessions")
    horizon = receipt["label_horizon_with_execution_lag"]
    end, last = (
        calendar.index(receipt["training_end"]),
        calendar.index(receipt["last_eligible_feature_date"]),
    )
    if horizon != 2 or receipt["purged_final_feature_rows"] < horizon or last + horizon > end:  # noqa: PLR2004
        raise ValueError("Declared overlapping labels cross training boundary")
    features = within(folder, folder / "features.npz")
    if file_hash(features) != receipt["features_sha256"]:
        raise ValueError("Fit feature checksum differs")
    with np.load(features, allow_pickle=False) as arrays:
        if set(arrays.files) != set(receipt["patterns"]) or set(arrays.files) != set(
            receipt["eligible_assets"]
        ):
            raise ValueError("Fit feature and pattern identities differ")
        for key in arrays.files:
            if arrays[key].shape != (len(calendar), len(receipt["eligible_assets"][key])):
                raise ValueError("Fit feature calendar or asset dimensions differ")
    checkpoints = []
    programs: set[str] = set()
    slots = 0
    for seed in protocol["gp_seeds"]:
        path = within(folder, folder / f"gp-seed{seed}.json")
        state = envelope(path)
        checkpoint = state.pop("checkpoint_hash")
        if compact(state) != checkpoint or state["version"] != "symbolic-search/0.1.0":
            raise ValueError("Genetic checkpoint identity differs")
        if state["protocol"]["seed"] != seed:
            raise ValueError("Genetic seed identity differs")
        evaluator = compact({"parent": compact(protocol), "fold": fold, "world": receipt["world"]})
        context = compact(
            {
                "version": state["version"],
                "protocol": state["protocol"],
                "template": template,
                "evaluator": evaluator,
                "data": receipt["dataset_sha256"],
            }
        )
        if context != state["context_hash"]:
            raise ValueError("Genetic context not bound to registered fit data/fold")
        checkpoints.append(
            {"seed": seed, "file_sha256": file_hash(path), "context_sha256": context}
        )
        slots += len(state["ledger"])
        programs.update(
            compact(row["pattern"]) for row in state["ledger"] if row.get("fitness") is not None
        )
    if slots != receipt["gp_slots"]:
        raise ValueError("Genetic slot accounting differs")
    for name, pattern in receipt["patterns"].items():
        if name.startswith("gp:") and compact(pattern) not in programs:
            raise ValueError("Selected GP feature is absent from fitted ledgers")
    return {
        "status": "declared_fit_evidence_verified",
        "world": receipt["world"],
        "fold": fold,
        "training_end": receipt["training_end"],
        "last_eligible_feature_date": receipt["last_eligible_feature_date"],
        "purged_rows": receipt["purged_final_feature_rows"],
        "label_horizon": horizon,
        "receipt_sha256": file_hash(folder / "receipt.json"),
        "features_sha256": receipt["features_sha256"],
        "dataset_sha256": receipt["dataset_sha256"],
        "fit_panel_sha256": receipt["fit_panel_sha256"],
        "checkpoints": checkpoints,
        "gp_slots": slots,
        "feature_count": len(receipt["patterns"]),
        "feature_rows": len(calendar),
        "training_sessions": end + 1,
        "physical_training_reproduced": False,
        "campaign_comparable": False,
    }
