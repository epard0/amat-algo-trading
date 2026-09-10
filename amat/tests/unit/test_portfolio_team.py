"""Offline committee acceptance: evidence integrity, honest decisions and recovery."""

from __future__ import annotations

import ast
import datetime as dt
import json
from pathlib import Path

import pytest

from amat.provenance import CodeRevision, RunManifest
from portfolio_team.cli import main
from portfolio_team.contracts import Catalog, ComparisonSpec, Policy, RunSpec
from portfolio_team.coordinator import run_review
from portfolio_team.evidence import EvidenceError, import_run, make_bundle, sha256
from portfolio_team.providers import FakeProvider
from portfolio_team.reports import export_review, render
from portfolio_team.store import Store


def make_run(root, alias, *, first="2020-01-02", initial=100000.0, total=0.10):
    folder = root / "outputs/runs" / alias
    folder.mkdir(parents=True)
    config = f'[strategy]\nname="{alias}"\n[execution]\nstart=09:30:00\n'.encode()
    config_path = root / f"{alias}.toml"
    config_path.write_bytes(config)
    manifest = RunManifest(
        run_id=alias,
        strategy_name=alias,
        strategy_version="1",
        config_sha256=sha256(config),
        code_revision=CodeRevision(detected=True, commit="abc", dirty=True),
        python_version="3.12",
        execution_model_version="daily",
        cost_model_version="2bps",
        started_at=dt.datetime(2026, 9, 10, tzinfo=dt.UTC),
    )
    sample = {"first_session": first, "last_session": "2020-12-31", "sessions": 252}
    performance = {
        **sample,
        "initial_equity": initial,
        "final_equity": initial * (1 + total),
        "total_return": total,
        "annualized_return": total,
        "annualized_volatility": 0.05,
        "sharpe": 1.0,
        "max_drawdown": -0.05,
        "hit_rate": 0.55,
        "average_gross_exposure": 0.6,
        "total_fills": 100,
        "total_missed_fills": 2,
        "total_costs": 20.0,
        "one_way_bps": 2.0,
    }
    combined = {
        "performance": performance,
        "sample": sample,
        "engine": "v1-frozen",
        "config_sha256": manifest.config_sha256,
        **{
            key: performance[key]
            for key in (
                "initial_equity",
                "final_equity",
                "total_return",
                "max_drawdown",
                "total_fills",
                "total_costs",
                "one_way_bps",
            )
        },
        "total_missed": 2,
    }
    payload = {
        "run_id": alias,
        "manifest_sha256": manifest.digest(),
        "results": {
            "studies": [{"engine": "v1-frozen", "combined": combined}],
        },
    }
    (folder / "manifest.json").write_text(manifest.to_json())
    (folder / "results.json").write_text(json.dumps(payload))
    spec = RunSpec(
        id=alias,
        path=f"outputs/runs/{alias}",
        config_path=f"{alias}.toml",
        study_index=0,
        engine="v1-frozen",
    )
    return spec, payload


@pytest.fixture
def setup(tmp_path):
    left, _ = make_run(tmp_path, "candidate")
    right, _ = make_run(tmp_path, "control")
    catalog = Catalog(
        schema_version="portfolio-team-catalog/1",
        default_evaluation_use="previously_inspected_history",
        runs=(left, right),
        comparisons=(
            ComparisonSpec(
                id="test", candidate="candidate", control="control", purpose="replacement"
            ),
        ),
    )
    return tmp_path, catalog


def alter(root, spec, mutate):
    path = root / spec.path / "results.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def test_manifest_fact_provenance_and_config_time(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    run = bundle.candidate
    assert run.config_status == "verified"
    assert "09:30:00" in run.config_json
    for fact in run.facts:
        value = json.loads(blobs[fact.artifact_sha256])
        for segment in fact.pointer.strip("/").split("/"):
            value = value[int(segment)] if isinstance(value, list) else value[segment]
        assert fact.value == value
    assert any(
        check.id == "candidate.revision" and check.status == "fail" for check in bundle.checks
    )
    assert any(
        check.id == "untouched_evaluation" and check.status == "missing" for check in bundle.checks
    )


@pytest.mark.parametrize(
    "failure", ["digest", "run_id", "config", "contradiction", "nan", "boolean", "equity", "count"]
)
def test_bad_evidence_is_rejected(setup, failure):
    root, catalog = setup

    def mutate(data):
        combined = data["results"]["studies"][0]["combined"]
        if failure == "digest":
            data["manifest_sha256"] = "0" * 64
        elif failure == "run_id":
            data["run_id"] = "wrong"
        elif failure == "config":
            combined["config_sha256"] = "0" * 64
        elif failure == "contradiction":
            combined["total_costs"] = 999
        elif failure == "nan":
            combined["performance"]["sharpe"] = float("nan")
        elif failure == "boolean":
            combined["performance"]["hit_rate"] = True
        elif failure == "equity":
            combined["final_equity"] = combined["performance"]["final_equity"] = 200000
        else:
            combined["total_fills"] = combined["performance"]["total_fills"] = 1.5

    alter(root, catalog.runs[0], mutate)
    with pytest.raises(EvidenceError):
        make_bundle(catalog, "test", root)


def test_missing_or_modified_current_config_is_not_historical_evidence(setup):
    root, catalog = setup
    (root / "candidate.toml").write_text("changed=true")
    run, _ = import_run(root, catalog.runs[0])
    assert run.config_status == "mismatch" and run.config_json is None
    missing = catalog.runs[0].model_copy(update={"config_path": "missing.toml"})
    run, _ = import_run(root, missing)
    assert run.config_status == "missing"


def test_paths_and_duplicate_json_keys(setup):
    root, catalog = setup
    escaped = catalog.runs[0].model_copy(update={"path": "../escape"})
    with pytest.raises(EvidenceError, match="escapes"):
        import_run(root, escaped)
    path = root / catalog.runs[0].path / "results.json"
    path.write_text('{"run_id":"a","run_id":"b"}')
    with pytest.raises(EvidenceError, match="duplicate"):
        import_run(root, catalog.runs[0])


def test_full_meeting_idempotency_replay_and_source_corruption(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        first = run_review(store, bundle, FakeProvider())
        second = run_review(store, bundle, FakeProvider())
        assert first == second
        assert first.execution_authority == "none"
        assert first.effective_disposition == "request_evidence"
        assert len(first.assessments) == 4
        assert store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 4
        target = export_review(store, first, root / "reports")
        before = (target / "report.md").read_bytes()
        assert export_review(store, second, root / "reports") == target
        assert (target / "report.md").read_bytes() == before
        assert "OFFLINE SIMULATION" in before.decode()
        store.db.execute(
            "UPDATE artifacts SET content=? WHERE hash=?",
            (b"bad", bundle.candidate.results_file_sha256),
        )
        store.db.commit()
        with pytest.raises(EvidenceError, match="corrupted"):
            export_review(store, first, root / "reports")


def test_mismatched_period_suppresses_deltas(setup):
    root, catalog = setup

    def mutate(data):
        combined = data["results"]["studies"][0]["combined"]
        combined["sample"]["first_session"] = "2020-02-03"
        combined["performance"]["first_session"] = "2020-02-03"

    alter(root, catalog.runs[0], mutate)
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        result = run_review(store, bundle, FakeProvider())
        report = render(bundle, result)
        assert "not comparable" in report
        assert result.effective_disposition == "request_evidence"


class ScriptedFailure(FakeProvider):
    id = "failure-test/1"
    chief_called = False

    def assess(self, role, bundle, previous=()):
        if role == "chief_pm":
            self.chief_called = True
        if role == "research":
            raise ValueError("secret-provider-key-do-not-store")
        return super().assess(role, bundle, previous)


def test_failed_reviewer_cannot_produce_a_decision_and_errors_are_redacted(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    provider = ScriptedFailure()
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        with pytest.raises(EvidenceError, match="budget"):
            run_review(store, bundle, provider)
        assert not provider.chief_called
        assert store.db.execute("SELECT decision FROM reviews").fetchone()[0] is None
        events = " ".join(row[0] for row in store.db.execute("SELECT payload FROM events"))
        assert "secret-provider" not in events
        assert (
            store.db.execute("SELECT COUNT(*) FROM attempts WHERE role='research'").fetchone()[0]
            == 2
        )


class UnknownReference(FakeProvider):
    id = "unknown-reference/1"

    def assess(self, role, bundle, previous=()):
        result = super().assess(role, bundle, previous)
        if role == "research":
            finding = result.findings[0].model_copy(update={"references": ("fabricated",)})
            return result.model_copy(update={"findings": (finding,)})
        return result


def test_unknown_citations_fail_closed(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        with pytest.raises(EvidenceError):
            run_review(store, bundle, UnknownReference())
        assert store.db.execute("SELECT status FROM reviews").fetchone()[0] == "failed"


class Shortlist(FakeProvider):
    id = "shortlist-test/1"

    def assess(self, role, bundle, previous=()):
        if role != "chief_pm":
            assert previous == ()
        result = super().assess(role, bundle, previous)
        return result.model_copy(update={"disposition": "shortlist_for_forward_shadow"})


def test_chief_cannot_grant_authority_or_shortlist_from_aggregate_studies(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        result = run_review(store, bundle, Shortlist())
        assert result.proposed_disposition == "shortlist_for_forward_shadow"
        assert result.effective_disposition == "request_evidence"
        assert result.execution_authority == "none"
    with pytest.raises(ValueError):
        Policy(execution_authority="paper")


def test_restart_preserves_successful_first_passes(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        with pytest.raises(EvidenceError):
            run_review(store, bundle, ScriptedFailure())
        review_id = store.db.execute("SELECT id FROM reviews").fetchone()[0]
        # Simulate a crash with one unfinished research attempt and completed peers.
        store.db.execute("DELETE FROM attempts WHERE role='research' AND attempt=2")
        store.db.execute("UPDATE attempts SET status='running' WHERE role='research'")
        store.db.commit()
    repaired = FakeProvider()
    repaired.id = ScriptedFailure.id
    with Store(root / "registry") as store:
        decision = run_review(store, store.resolve("test"), repaired)
        assert decision.review_id == review_id
        assert (
            store.db.execute("SELECT COUNT(*) FROM attempts WHERE role='portfolio'").fetchone()[0]
            == 1
        )


def test_registry_writer_lock_and_export_no_overwrite(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        with pytest.raises(EvidenceError, match="lock"):
            Store(root / "registry")
        store.import_bundle(bundle, blobs)
        decision = run_review(store, bundle, FakeProvider())
        target = export_review(store, decision, root / "reports")
        (target / "report.md").write_text("user edits")
        with pytest.raises(EvidenceError, match="overwrite"):
            export_review(store, decision, root / "reports")
        assert (target / "report.md").read_text() == "user edits"


def test_package_has_no_broker_or_process_execution_imports():
    package = Path(__file__).resolve().parents[2] / "src/portfolio_team"
    forbidden = {"executor", "subprocess", "requests", "httpx", "alpaca"}
    for source in package.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.Import):
                assert not {item.name.split(".")[0] for item in node.names} & forbidden
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden


def test_cli_requires_explicit_openai_model_and_credential(setup, monkeypatch):
    root, _ = setup
    (root / "pyproject.toml").touch()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["--root", str(root), "review", "test", "--provider", "openai"])
    assert error.value.code == 1
