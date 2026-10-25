"""Legacy envelopes are data: verify paths and slices without importing source."""

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from autoresearch.improvements.nested_inventory import file_hash, inspect_nested


def save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "payload": payload,
                "sha256": hashlib.sha256(
                    json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
                    ).encode()
                ).hexdigest(),
            }
        )
    )


def fixture(root: Path) -> None:
    save(root / "protocol.json", {"worlds": {"historical": {}}, "runtime_source_sha256": {}})
    (root / "driver.py").write_text("raise RuntimeError('This source must never execute')")
    for index, phase in enumerate(["train", "test"]):
        folder = root / "runs" / str(index)
        folder.mkdir(parents=True)
        np.savez_compressed(
            folder / "path.npz",
            dates=np.array(["2020-01-01", "2020-01-02", "2020-01-03"]),
            equity=np.array([100.0, 110.0, 99.0]),
            exposure=np.ones(3),
        )
        save(
            folder / "receipt.json",
            {
                "path_sha256": file_hash(folder / "path.npz"),
                "market_sha256": "market",
                "task": {
                    "world": "historical",
                    "phase": phase,
                    "fold": "fold",
                    "cost": 2,
                    "lower": "2020-01-01",
                    "upper": "2020-01-03",
                    "recipe": {"value": index},
                },
                "metrics": {"dates": ["2020-01-02", "2020-01-03"], "n": 2, "returns": [0.1, -0.1]},
            },
        )


def test_valid_paths_preserve_phases_and_do_not_grant_comparability(tmp_path: Path) -> None:
    fixture(tmp_path)
    before = {str(p): file_hash(p) for p in tmp_path.rglob("*") if p.is_file()}
    result = inspect_nested(tmp_path)
    assert len(result["rows"]) == 2 and not result["failures"]
    assert len(result["groups"]) == 2
    assert all(abs(x["net_return"] + 0.01) < 1e-12 for x in result["rows"])
    assert result["comparison_coverage"] is False
    assert before == {str(p): file_hash(p) for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation", ["envelope", "path", "returns", "escape"])
def test_tampering_cannot_be_a_valid_path(tmp_path: Path, mutation: str) -> None:
    root = tmp_path / "study"
    fixture(root)
    receipt = root / "runs/0/receipt.json"
    value = json.loads(receipt.read_text())
    if mutation == "envelope":
        value["sha256"] = "0" * 64
        receipt.write_text(json.dumps(value))
    elif mutation == "path":
        (receipt.parent / "path.npz").write_bytes(b"changed")
    elif mutation == "returns":
        value["payload"]["metrics"]["returns"] = [0.2, -0.1]
        save(receipt, value["payload"])
    else:
        target = tmp_path / "outside.npz"
        (receipt.parent / "path.npz").rename(target)
        (receipt.parent / "path.npz").symlink_to(target)
    result = inspect_nested(root)
    assert len(result["rows"]) == 1
    assert len(result["failures"]) == 1
    assert result["comparison_coverage"] is False
