"""Operator selections preserve failed evidence and become complete research baselines."""

import datetime as dt
import json
from pathlib import Path

import pytest

from amat.config.v6 import load_v6_config
from autoresearch.cli import main
from autoresearch.contracts import Candidate, Program
from autoresearch.ledger import Ledger
from autoresearch.loop import finish, register
from autoresearch.promotion import active_promotion, promote
from autoresearch.specs import FeatureSpec, SignalSpec, SignalTerm
from autoresearch.workspace import digest_file, initialize, load_program, write_json

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def recorded(tmp_path):
    root = tmp_path / "history"
    root.mkdir()
    baseline = Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)
    candidate = Candidate(
        config=baseline.config,
        signal=SignalSpec(
            terms=(SignalTerm(feature=FeatureSpec(kind="trend_agreement", lookback=76)),)
        ),
    )
    program = Program(
        name="promotion-test",
        baseline=baseline,
        load_start=dt.date(2016, 1, 4),
        measured_start=dt.date(2018, 1, 2),
        train_end=dt.date(2022, 12, 30),
        validation_end=dt.date(2026, 8, 10),
        frozen_files={},
        dependencies={},
    )
    write_json(root / "program.json", program.model_dump(mode="json"))
    (root / "program.sha256").write_text(digest_file(root / "program.json"))
    write_json(
        root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
    )
    result = {
        "score": -0.11,
        "score_version": "exposure-matched-incremental/1",
        "failed_gates": ["no_incremental_exposure_matched_information"],
        "eligible": False,
    }
    objective = root / "outputs/runs" / candidate.digest() / "objective.json"
    write_json(objective, result)
    result["artifact_hashes"] = {str(objective.relative_to(root)): digest_file(objective)}
    with Ledger(root, 1000) as ledger:
        index = register(ledger, candidate.digest(), baseline.digest(), "{}")
        finish(ledger, index, "discard", result)
    return root, candidate, objective


def test_promotion_does_not_rewrite_measurements_or_discard_decision(recorded, tmp_path):
    root, candidate, _ = recorded
    original = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = promote(
        root, tmp_path / "registry", candidate.digest(), label="E73", reason="User selected E73"
    )
    assert result["automatic_decision"] == "discard"
    assert result["automatic_checks_overridden"]
    assert result["automatic_score"] == -0.11
    assert result["execution_authority"] == "none"
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == original
    assert active_promotion(tmp_path / "registry") == result


def test_evidence_tampering_and_unknown_candidate_cannot_be_promoted(recorded, tmp_path):
    root, candidate, objective = recorded
    with pytest.raises(ValueError, match="no completed"):
        promote(root, tmp_path / "registry", "a" * 64, label="unknown", reason="Requested")
    objective.write_text("{}")
    with pytest.raises(ValueError, match="evidence changed"):
        promote(root, tmp_path / "registry", candidate.digest(), label="E73", reason="Requested")
    assert active_promotion(tmp_path / "registry") is None


def test_active_selection_rejects_modified_record(recorded, tmp_path):
    root, candidate, _ = recorded
    registry = tmp_path / "registry"
    promote(root, registry, candidate.digest(), label="E73", reason="Requested")
    pointer = json.loads((registry / "active-strategy.json").read_text())
    (registry / pointer["record"]).write_text("{}")
    with pytest.raises(ValueError, match="record changed"):
        active_promotion(registry)


def test_initialize_preserves_promoted_signal_and_freezes_provenance(recorded, tmp_path):
    root, candidate, _ = recorded
    record = promote(
        root, tmp_path / "registry", candidate.digest(), label="E73", reason="Requested"
    )
    target = tmp_path / "new-session"
    initialize(
        target,
        ROOT,
        ROOT / "configs/strategy/amat_v6.toml",
        ROOT / "data/snapshots/alpaca-sip-2026-08-11",
        name="promoted-session",
        train_end=dt.date(2022, 12, 30),
        validation_end=dt.date(2026, 8, 10),
        promotion=record,
    )
    program = load_program(target)
    assert program.baseline == candidate
    assert "runtime/research-promotion.json" in program.frozen_files
    assert json.loads((target / "runtime/research-promotion.json").read_text()) == record


def test_simple_start_preserves_v13_instead_of_legacy_promotion(recorded, tmp_path, monkeypatch):
    root, candidate, _ = recorded
    record = promote(
        root, tmp_path / "registry", candidate.digest(), label="E73", reason="Requested"
    )
    captured = {}

    def initialize(root, *_args, **options):
        root.mkdir(parents=True)
        captured.update(options)

    def run_loop(root, _iterations, _built_in):
        (root / "report.html").write_text("report")
        return {
            "status": "batch_completed",
            "batch": {"requested": 1, "completed": 1, "escalated": 0},
        }

    monkeypatch.setattr("autoresearch.cli.active_promotion", lambda _directory: record)
    monkeypatch.setattr("autoresearch.cli.latest_path", lambda _source: tmp_path / "latest.json")
    monkeypatch.setattr("autoresearch.cli.initialize", initialize)
    monkeypatch.setattr("autoresearch.cli.run_frozen_start", run_loop)
    assert (
        main(
            ["start", "1", "--source", str(ROOT), "--workspace", str(tmp_path / "run"), "--no-open"]
        )
        == 0
    )
    assert captured["promotion"] is None


def test_legacy_optional_selection_migration_checks_original_digest(recorded, tmp_path):
    import hashlib

    root, candidate, _ = recorded
    directory = tmp_path / "registry"
    record = promote(root, directory, candidate.digest(), label="legacy", reason="test")
    record["candidate"]["signal"].pop("selection")
    record["candidate_id"] = hashlib.sha256(
        json.dumps(record["candidate"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    pointer = json.loads((directory / "active-strategy.json").read_text())
    path = directory / pointer["record"]
    write_json(path, record)
    pointer["sha256"] = digest_file(path)
    write_json(directory / "active-strategy.json", pointer)
    assert (
        active_promotion(directory)["runtime_compatibility"]["runtime_candidate_id"]
        == candidate.digest()
    )
    record["candidate"]["signal"]["terms"][0]["feature"]["lookback"] = 77
    write_json(path, record)
    pointer["sha256"] = digest_file(path)
    write_json(directory / "active-strategy.json", pointer)
    with pytest.raises(ValueError, match="digest mismatch"):
        active_promotion(directory)


def test_return_floor_rejection_cannot_become_an_active_selection(recorded, tmp_path):
    root, candidate, _ = recorded
    with Ledger(root, 1000) as ledger:
        row = ledger.db.execute("SELECT id,result FROM experiments").fetchone()
        result = json.loads(row[1])
        result["return_floor"] = {"passes": False}
        finish(ledger, row[0], "discard", result)
    with pytest.raises(ValueError, match="incumbent net-return floor"):
        promote(
            root,
            tmp_path / "registry",
            candidate.digest(),
            label="rejected",
            reason="Test the current research policy",
        )
    assert active_promotion(tmp_path / "registry") is None
