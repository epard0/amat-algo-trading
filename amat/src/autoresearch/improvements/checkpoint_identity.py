"""Explain source-bound checkpoint changes without erasing archived identities."""

from __future__ import annotations

import copy
from typing import Any

from autoresearch.improvements.fit_evidence import compact


def verify_registry_transition(
    original: dict[str, Any], current: dict[str, Any], old_registry: str, new_registry: str
) -> dict[str, Any]:
    """Require checksums and equality to a narrowly predicted metadata transition."""
    for checkpoint in (original, current):
        payload = {k: v for k, v in checkpoint.items() if k != "checkpoint_hash"}
        if checkpoint.get("checkpoint_hash") != compact(payload):
            raise ValueError("Invalid checkpoint checksum")
    expected = copy.deepcopy(original)
    changed = 0

    def visit(value: Any) -> None:
        nonlocal changed
        if isinstance(value, dict):
            evaluation = value.get("diagnostics", {}).get("evaluation", {})
            if "registry_sha256" in evaluation:
                if evaluation["registry_sha256"] != old_registry:
                    raise ValueError("Unexpected original registry identity")
                evaluation["registry_sha256"] = new_registry
                changed += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(expected)
    if not changed:
        raise ValueError("No registered diagnostic identity to explain")
    expected.pop("checkpoint_hash")
    expected["checkpoint_hash"] = compact(expected)
    if expected != current:
        raise ValueError("Checkpoint differs beyond the predicted registry transition")
    return {
        "original_checkpoint_sha256": original["checkpoint_hash"],
        "current_checkpoint_sha256": current["checkpoint_hash"],
        "raw_identity_equal": original == current,
        "predicted_transition_exact": True,
        "diagnostic_registry_fields": changed,
        "original_registry_sha256": old_registry,
        "current_registry_sha256": new_registry,
        "archived_strict_gate_overridden": False,
    }
