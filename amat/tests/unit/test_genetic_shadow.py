import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from amat.data.synthetic import generate_panel
from autoresearch.genetic import shadow
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.replay import attach_pattern, load_incumbent
from autoresearch.genetic.shadow_artifact import freeze_shadow_artifact

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    base = load_incumbent(ROOT / "configs/strategy/amat_v13_return_target.toml")
    panel = generate_panel(base.config.strategy.to_universe(), sessions=550)
    panel = replace(
        panel,
        source="scenario(shadow-orchestration)",
        adjusted_opens=panel.adjusted_closes * 0.999,
        adjusted_highs=panel.adjusted_closes * 1.01,
        adjusted_lows=panel.adjusted_closes * 0.99,
    )
    pattern = json.loads((ROOT / "docs/genetic-patterns/symbolic.example.json").read_text())
    pattern["genome"]["root"] = {
        "node": "feature",
        "name": "momentum",
        "lookback": 21,
        "lag": 0,
        "source": "self",
        "transform": "time_series_rank",
    }
    candidate = attach_pattern(base, parse_pattern(pattern))
    path = tmp_path / "artifact.json"
    freeze_shadow_artifact(
        candidate,
        shadow._prefix(panel, panel.sessions[519]),
        provenance={"diagnostic": "fixture"},
        output=path,
    )
    manifest = SimpleNamespace(model_dump=lambda **_: {"snapshot_id": "fixture"})
    monkeypatch.setattr(
        shadow, "load_panel", lambda *_, end, **__: (shadow._prefix(panel, end), manifest)
    )
    monkeypatch.setattr(shadow, "audit_sessions", lambda *_, **__: {"calendar": "fixture"})
    return path, panel, tmp_path / "ledger.json"


def test_snapshot_observation_is_inert_persistent_and_idempotent(fixture, tmp_path):
    artifact, panel, ledger = fixture
    output = tmp_path / "observation.json"
    record = shadow.observe_shadow(
        artifact, tmp_path, as_of=panel.sessions[530], ledger=ledger, output=output
    )
    assert record["report"]["reference_probe_parity"]
    assert record["report"]["execution_authority"] == "none"
    assert record["report"]["promotion_authority"] == "none"
    assert record["report"]["diagnostic_only"]
    assert record["report"]["evidence_level"] == "historical_snapshot_shadow_diagnostic"
    assert len(record["report"]["assets"]) == 11
    before = ledger.read_bytes()
    assert (
        shadow.observe_shadow(
            artifact, tmp_path, as_of=panel.sessions[530], ledger=ledger, output=output
        )
        == record
    )
    assert ledger.read_bytes() == before
    assert output.with_suffix(".md").exists()


def test_calibration_change_and_wrong_output_refuse_before_ledger_append(fixture, tmp_path):
    artifact, panel, ledger = fixture
    output = tmp_path / "observation.json"
    shadow.observe_shadow(
        artifact, tmp_path, as_of=panel.sessions[530], ledger=ledger, output=output
    )
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="different observation"):
        shadow.observe_shadow(
            artifact, tmp_path, as_of=panel.sessions[531], ledger=ledger, output=output
        )
    assert ledger.read_bytes() == before
    panel.adjusted_opens[100, 0] *= 1.0001
    with pytest.raises(ValueError, match="calibration panel"):
        shadow.observe_shadow(
            artifact,
            tmp_path,
            as_of=panel.sessions[531],
            ledger=ledger,
            output=tmp_path / "next.json",
        )
    assert ledger.read_bytes() == before


def test_calibration_period_is_not_forward_observation(fixture, tmp_path):
    artifact, panel, ledger = fixture
    with pytest.raises(ValueError, match="follow the calibration"):
        shadow.observe_shadow(
            artifact,
            tmp_path,
            as_of=panel.sessions[519],
            ledger=ledger,
            output=tmp_path / "obs.json",
        )
    assert not ledger.exists()


def test_evidence_verifier_rejects_missing_tampered_and_external_paths(tmp_path):
    import hashlib

    protocol = {"fixture": True}
    report = {"status": "completed", "protocol_sha256": digest_json(protocol)}
    for name, value in [("protocol.json", protocol), ("report.json", report)]:
        (tmp_path / name).write_text(json.dumps(value))
    manifest = {
        "schema_version": "p4-evidence-manifest/1",
        "files": {
            name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
            for name in ["protocol.json", "report.json"]
        },
    }
    manifest_path = tmp_path / "evidence-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    assert shadow.verify_evidence(tmp_path)["protocol"] == protocol
    (tmp_path / "report.json").write_text("{}")
    with pytest.raises(ValueError, match="evidence changed"):
        shadow.verify_evidence(tmp_path)
    manifest["files"] = {"../outside.json": "a" * 64}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="inside the study"):
        shadow.verify_evidence(tmp_path)


def test_report_path_cannot_overwrite_artifact_or_ledger(fixture, tmp_path):
    artifact, panel, ledger = fixture
    before = artifact.read_bytes()
    for output in (artifact, ledger, ledger.with_suffix(".json.lock"), tmp_path / "report.md"):
        with pytest.raises(ValueError, match="distinct JSON report"):
            shadow.observe_shadow(
                artifact, tmp_path, as_of=panel.sessions[530], ledger=ledger, output=output
            )
    assert artifact.read_bytes() == before
    assert not ledger.exists()


def test_registry_rebind_is_explicit_and_never_transfers_approval(fixture, monkeypatch, tmp_path):
    from autoresearch.market_scenarios import panel_digest

    artifact, panel, _ = fixture
    candidate = json.loads(artifact.read_text())["payload"]["candidate"]
    baseline = dict(candidate, genetic_pattern=None)
    original_registry = {
        "version": "genetic-symbolic-features/1",
        "source_sha256": {"old": "a" * 64},
    }
    historical = json.loads(json.dumps(candidate))
    historical["genetic_pattern"]["registry_sha256"] = digest_json(original_registry)
    historical_sha = digest_json(historical)
    end = panel.sessions[519]
    protocol = {
        "incumbent": baseline,
        "start": panel.sessions[0].isoformat(),
        "folds": {
            "fold": {
                "fit_end": end.isoformat(),
                "fit_panel_sha256": panel_digest(shadow._prefix(panel, end)),
            }
        },
        "registry": original_registry,
    }
    discovery_name = "observed/fold/discovery/report.json"
    selection_name = "observed/fold/portfolio/selection.json"
    for name, data in [
        (discovery_name, {"contenders": {"genetic": [candidate["genetic_pattern"]["pattern"]]}}),
        (
            selection_name,
            {
                "selections": {
                    "genetic": {
                        "selected_sha256": historical_sha,
                        "contenders": [
                            {"candidate_sha256": historical_sha, "objective": {"score": 1}}
                        ],
                    }
                }
            },
        ),
    ]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    monkeypatch.setattr(
        shadow,
        "verify_evidence",
        lambda _: {
            "protocol": protocol,
            "manifest": {"files": {discovery_name: "x", selection_name: "y"}},
            "manifest_sha256": "a" * 64,
            "report": {
                "statistics": {
                    "methods": {"genetic": {"historical_support": True, "failed_gates": []}}
                }
            },
        },
    )
    monkeypatch.setattr(shadow, "freeze_shadow_artifact", lambda *_a, provenance, **_k: provenance)
    kwargs = {"fold": "fold", "method": "genetic", "rank": 0, "output": tmp_path / "new.json"}
    with pytest.raises(ValueError, match="no matching inner"):
        shadow.freeze_from_validation(tmp_path, tmp_path, **kwargs)
    result = shadow.freeze_from_validation(tmp_path, tmp_path, allow_registry_rebind=True, **kwargs)
    assert result["registry_rebound"]
    assert result["historical_candidate_sha256"] == historical_sha
    assert not result["method_historical_support"]
    assert not result["selected_by_inner"]
    assert not result["historical_validation_transferred"]
