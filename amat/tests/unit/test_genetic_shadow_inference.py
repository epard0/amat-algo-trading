"""Frozen inference retains research parity and fails closed without authority."""

import copy
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.data.synthetic import generate_panel
from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.replay import attach_pattern
from autoresearch.genetic.shadow_artifact import evaluate_shadow, freeze_shadow_artifact
from autoresearch.genetic.shadow_inference import (
    ShadowMonitorPolicy,
    _prefix,
    calibrate_values,
    infer_shadow,
    monitor_values,
)

ROOT = Path(__file__).parents[2]


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "autoresearch.genetic.shadow_artifact._source_hashes", lambda: {"test": "fixed"}
    )
    base = Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    )
    panel = generate_panel(base.config.strategy.to_universe(), sessions=340)
    closes = panel.adjusted_closes
    rows = np.arange(len(panel))[:, None]
    panel = replace(
        panel,
        adjusted_opens=closes * (1 + 0.005 * np.sin(rows)),
        adjusted_highs=closes * 1.02,
        adjusted_lows=closes * 0.98,
        session_volumes=np.ones_like(closes) * 1000,
    )
    pattern = json.loads((ROOT / "docs/genetic-patterns/symbolic.example.json").read_text())
    pattern["genome"]["root"] = {
        "node": "feature",
        "name": "body_atr",
        "lookback": 5,
        "lag": 0,
        "transform": "time_series_rank",
        "source": "self",
    }
    candidate = attach_pattern(base, parse_pattern(pattern))
    artifact = freeze_shadow_artifact(
        candidate, _prefix(panel, 300), provenance={"test": True}, output=tmp_path / "artifact.json"
    )
    return artifact, panel, candidate


def test_inference_matches_research_and_has_no_authority(frozen):
    artifact, panel, candidate = frozen
    report = infer_shadow(artifact, panel)
    values, baseline, changed = evaluate_shadow(candidate, panel)
    assert report["reference_probe_parity"]
    assert report["forward_sessions"] == 40
    assert report["diagnostic_only"] and report["mode"] == "report_only"
    assert report["promotion_authority"] == report["execution_authority"] == "none"
    assert report["latency_ns"] > 0
    assert "orders" not in report and "positions" not in report
    for i, row in enumerate(report["assets"]):
        assert row["pattern_value"] == values[-1, i]
        assert row["candidate_score"] == changed[-1, i]
        assert row["baseline_score"] == baseline[-1, i]
        assert 0 <= row["candidate_score"] <= 1
        assert (
            abs(row["bounded_contribution"]) <= candidate.genetic_pattern.pattern.binding.max_effect
        )
    json.dumps(report, allow_nan=False)


def test_same_prefix_unaffected_by_future_input_changes(frozen):
    artifact, panel, _ = frozen
    original = infer_shadow(artifact, _prefix(panel, 320))
    opens = panel.adjusted_opens.copy()
    opens[320:] *= 10
    modified = infer_shadow(artifact, _prefix(replace(panel, adjusted_opens=opens), 320))
    original.pop("latency_ns")
    modified.pop("latency_ns")
    assert original == modified


def test_missing_latest_feature_suspends_every_contribution(frozen):
    artifact, panel, _ = frozen
    opens = panel.adjusted_opens.copy()
    opens[-1, 0] = np.nan
    report = infer_shadow(artifact, replace(panel, adjusted_opens=opens))
    assert report["recommended_monitor_state"] == "suspended"
    assert report["assets"][0]["pattern_value"] is None
    assert any(item["code"] == "pattern_unavailable" for item in report["monitor"]["alerts"])
    assert all(row["bounded_contribution"] == 0 for row in report["assets"])
    json.dumps(report, allow_nan=False)


def test_missing_required_ohlc_fails_closed(frozen):
    artifact, panel, _ = frozen
    report = infer_shadow(artifact, replace(panel, adjusted_opens=None))
    assert report["recommended_monitor_state"] == "suspended"
    assert all(row["bounded_contribution"] == 0 for row in report["assets"])
    assert all(row["pattern_value"] is None for row in report["assets"])


def test_mutated_artifact_refused_even_without_loading_from_disk(frozen):
    artifact, panel, _ = frozen
    changed = copy.deepcopy(artifact)
    changed["payload"]["monitor_policy"]["max_abs_mean_z"] = 100
    with pytest.raises(ValueError, match="checksum"):
        infer_shadow(changed, panel)


def test_changed_calibration_inputs_suspend_even_if_probes_match(frozen):
    artifact, panel, _ = frozen
    opens = panel.adjusted_opens.copy()
    opens[20] *= 1.001
    report = infer_shadow(artifact, replace(panel, adjusted_opens=opens))
    assert not report["reference_probe_parity"]
    assert report["recommended_monitor_state"] == "suspended"
    assert all(row["bounded_contribution"] == 0 for row in report["assets"])


def test_requires_exact_universe_and_origin(frozen):
    artifact, panel, _ = frozen
    with pytest.raises(ValueError, match="universe"):
        infer_shadow(artifact, panel.slice_symbols(panel.symbols[::-1]))
    shifted = replace(panel, sessions=tuple(date + dt.timedelta(days=1) for date in panel.sessions))
    with pytest.raises(ValueError, match="calibration start"):
        infer_shadow(artifact, shifted)


def test_monitor_constant_and_missing_calibration_are_finite():
    reference = calibrate_values(np.array([[0.25, np.nan], [0.25, np.nan]]), ["A", "B"])
    result = monitor_values(
        np.array([[0.5, np.nan], [0.5, np.nan]]), ["A", "B"], reference, ShadowMonitorPolicy()
    )
    assert result["per_asset"]["A"]["absolute_mean_z"] is None
    assert {"symbol": "A", "code": "pattern_mean_drift"} in result["alerts"]
    assert {"symbol": "B", "code": "calibration_unavailable"} in result["alerts"]
    json.dumps(result, allow_nan=False)
    unchanged = monitor_values(np.array([[0.25]]), ["A"], reference, ShadowMonitorPolicy())
    assert not unchanged["alerts"]


@pytest.mark.parametrize(
    "options",
    [
        {"min_available_fraction": -1},
        {"max_abs_mean_z": float("nan")},
        {"max_prevalence_shift": 2},
        {"max_score_change": float("inf")},
    ],
)
def test_invalid_monitor_policy_refused(options):
    with pytest.raises(ValueError):
        ShadowMonitorPolicy(**options)
