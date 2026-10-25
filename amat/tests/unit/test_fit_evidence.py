"""Declared fit evidence must fail closed; valid metadata is not causal reproduction."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from autoresearch.improvements.fit_evidence import compact, inspect_fit
from autoresearch.improvements.nested_inventory import file_hash


def save(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": value, "sha256": compact(value)}))


def fixture(folder: Path) -> tuple[dict[str, Any], list[str], dict[str, Any], dict[str, Any]]:
    calendar = [f"2020-01-0{i}" for i in range(1, 7)]
    fold = {
        "name": "test",
        "train_end": calendar[2],
        "selection_end": calendar[3],
        "test_end": calendar[5],
    }
    protocol = {"folds": [fold], "gp_seeds": [1]}
    template = {"name": "template"}
    pattern = {"genome": "fixed"}
    arrays: dict[str, Any] = {"gp:0": np.ones((6, 1))}
    np.savez_compressed(folder / "features.npz", **arrays)
    receipt = {
        "fold": fold,
        "world": "historical",
        "training_only": True,
        "training_end": calendar[2],
        "last_eligible_feature_date": calendar[0],
        "label_horizon_with_execution_lag": 2,
        "purged_final_feature_rows": 2,
        "patterns": {"gp:0": pattern},
        "eligible_assets": {"gp:0": [True]},
        "features_sha256": file_hash(folder / "features.npz"),
        "dataset_sha256": "dataset",
        "fit_panel_sha256": "panel",
        "gp_slots": 1,
    }
    state = {
        "version": "symbolic-search/0.1.0",
        "protocol": {"seed": 1},
        "ledger": [{"pattern": pattern, "fitness": 1.0}],
    }
    evaluator = compact({"parent": compact(protocol), "fold": fold, "world": receipt["world"]})
    state["context_hash"] = compact(
        {
            "version": state["version"],
            "protocol": state["protocol"],
            "template": template,
            "evaluator": evaluator,
            "data": "dataset",
        }
    )
    state["checkpoint_hash"] = compact(state)
    save(folder / "gp-seed1.json", state)
    save(folder / "receipt.json", receipt)
    return protocol, calendar, template, receipt


def test_valid_declared_fit_keeps_causal_reproduction_unresolved(tmp_path: Path) -> None:
    protocol, calendar, template, _ = fixture(tmp_path)
    result = inspect_fit(tmp_path, protocol, calendar, template)
    assert result["gp_slots"] == 1
    assert result["physical_training_reproduced"] is False
    assert result["campaign_comparable"] is False


@pytest.mark.parametrize(
    "mutation", ["future", "purge", "overlap", "context", "feature", "selected"]
)
def test_fit_evidence_rejects_temporal_or_crypto_mismatch(tmp_path: Path, mutation: str) -> None:
    protocol, calendar, template, receipt = fixture(tmp_path)
    if mutation == "future":
        receipt["training_end"] = calendar[4]
    elif mutation == "purge":
        receipt["purged_final_feature_rows"] = 0
    elif mutation == "overlap":
        receipt["last_eligible_feature_date"] = calendar[2]
    elif mutation == "context":
        state = json.loads((tmp_path / "gp-seed1.json").read_text())["payload"]
        state.pop("checkpoint_hash")
        state["context_hash"] = "forged"
        state["checkpoint_hash"] = compact(state)
        save(tmp_path / "gp-seed1.json", state)
    elif mutation == "feature":
        (tmp_path / "features.npz").write_bytes(b"changed")
    else:
        receipt["patterns"]["gp:0"] = {"genome": "not_fitted"}
    save(tmp_path / "receipt.json", receipt)
    with pytest.raises(ValueError):
        inspect_fit(tmp_path, protocol, calendar, template)
