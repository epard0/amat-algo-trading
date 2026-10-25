"""Frozen report-only artifacts bind formulas, implementation and calibration."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.data.synthetic import generate_panel
from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate
from autoresearch.genetic import shadow_artifact as artifacts
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.replay import attach_pattern

ROOT = Path(__file__).parents[2]


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(artifacts, "_source_hashes", lambda: {"test.py": "a" * 64})
    monkeypatch.setattr(artifacts, "_runtime_versions", lambda: {"python": "fixed"})
    baseline = Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    )
    panel = replace(
        generate_panel(baseline.config.strategy.to_universe(), sessions=300), source="shadow-test"
    )
    panel = replace(panel, adjusted_opens=panel.adjusted_closes.copy())
    return baseline, panel


def candidate(base, kind):
    payload = json.loads((ROOT / f"docs/genetic-patterns/{kind}.example.json").read_text())
    return attach_pattern(base, parse_pattern(payload))


def freeze(base, panel, tmp_path, kind="candle"):
    return artifacts.freeze_shadow_artifact(
        candidate(base, kind),
        panel,
        provenance={"eligible": True, "execution_authority": "trade"},
        output=tmp_path / "artifact.json",
    )


@pytest.mark.parametrize("kind", ["candle", "symbolic"])
def test_frozen_roundtrip_matches_native_scores_and_never_grants_authority(context, tmp_path, kind):
    base, panel = context
    artifact = freeze(base, panel, tmp_path, kind)
    assert artifacts.load_shadow_artifact(tmp_path / "artifact.json") == artifact
    payload = artifact["payload"]
    assert payload["diagnostic_only"] is True
    assert payload["execution_authority"] == payload["promotion_authority"] == "none"
    assert payload["provenance"]["eligible"] is True  # Inert provenance cannot confer authority.
    assert payload["reference_window_sessions"] == min(
        252, len(panel) - payload["required_history"]
    )
    assert (payload["registry"] is not None) == (kind == "symbolic")
    artifacts.verify_calibration(artifact, panel)
    values, original, changed = artifacts.evaluate_shadow(candidate(base, kind), panel)
    assert payload["probes"]["values"] == artifacts._matrix(values[-5:])
    np.testing.assert_allclose(changed[original == 0], 0)
    assert np.max(np.abs(changed - original)) <= 0.25 + 1e-12
    with pytest.raises(FileExistsError):
        freeze(base, panel, tmp_path, kind)


@pytest.mark.parametrize("key", ["candidate", "sources", "provenance", "probes"])
def test_checksum_detects_payload_changes(context, tmp_path, key):
    base, panel = context
    artifact = freeze(base, panel, tmp_path)
    artifact["payload"][key] = {}
    with pytest.raises(ValueError, match="checksum"):
        artifacts.validate_shadow_artifact(artifact)


@pytest.mark.parametrize(
    "key,value",
    [
        ("execution_authority", "paper"),
        ("promotion_authority", "automatic"),
        ("diagnostic_only", False),
        ("required_history", 999),
        ("universe", ["SPY"]),
        ("registry", {"version": "fake"}),
        ("sources", {}),
        ("runtime_versions", {}),
        ("monitor_policy", {}),
    ],
)
def test_rehashed_invalid_contract_is_rejected(context, tmp_path, key, value):
    base, panel = context
    artifact = freeze(base, panel, tmp_path)
    artifact["payload"][key] = value
    artifact["payload_sha256"] = digest_json(artifact["payload"])
    with pytest.raises(ValueError):
        artifacts.validate_shadow_artifact(artifact)


def test_dependency_and_calibration_parity_changes_fail_closed(context, tmp_path, monkeypatch):
    base, panel = context
    artifact = freeze(base, panel, tmp_path)
    modified = panel.adjusted_closes.copy()
    modified[-1, 0] += 1
    with pytest.raises(ValueError, match="panel mismatch"):
        artifacts.verify_calibration(artifact, replace(panel, adjusted_closes=modified))
    forged = copy.deepcopy(artifact)
    forged["payload"]["probes"]["modified_scores"][-1][0] = 0.111
    forged["payload_sha256"] = digest_json(forged["payload"])
    with pytest.raises(ValueError, match="numerical parity"):
        artifacts.verify_calibration(forged, panel)
    monkeypatch.setattr(artifacts, "_source_hashes", lambda: {"test.py": "b" * 64})
    with pytest.raises(ValueError, match="source or runtime"):
        artifacts.validate_shadow_artifact(artifact)


def test_missing_fields_and_short_history_rejected(context, tmp_path):
    base, panel = context
    artifact = freeze(base, panel, tmp_path)
    del artifact["payload"]["calibration"]
    artifact["payload_sha256"] = digest_json(artifact["payload"])
    with pytest.raises(ValueError, match="fields"):
        artifacts.validate_shadow_artifact(artifact)
    with pytest.raises(ValueError, match="requires a genetic"):
        artifacts.freeze_shadow_artifact(base, panel, provenance={}, output=tmp_path / "base.json")
    short = generate_panel(base.config.strategy.to_universe(), sessions=100)
    with pytest.raises(ValueError, match="warm-up"):
        artifacts.freeze_shadow_artifact(
            candidate(base, "candle"), short, provenance={}, output=tmp_path / "short.json"
        )


def test_rehashed_reference_statistics_require_calibration_parity(context, tmp_path):
    base, panel = context
    artifact = freeze(base, panel, tmp_path)
    symbol = artifact["payload"]["universe"][0]
    artifact["payload"]["reference_stats"][symbol]["std"] += 0.001
    artifact["payload_sha256"] = digest_json(artifact["payload"])
    with pytest.raises(ValueError, match="reference statistics mismatch"):
        artifacts.verify_calibration(artifact, panel)


def test_rehashed_formula_change_rejected_by_operational_digest(context, tmp_path):
    base, panel = context
    artifact = freeze(base, panel, tmp_path)
    spec = artifact["payload"]["candidate"]["genetic_pattern"]
    spec["pattern"]["binding"]["max_effect"] = 0.2
    artifact["payload"]["candidate_sha256"] = digest_json(artifact["payload"]["candidate"])
    artifact["payload_sha256"] = digest_json(artifact["payload"])
    with pytest.raises(ValueError, match="operational hash mismatch"):
        artifacts.validate_shadow_artifact(artifact)
