"""Budget pauses must be non-destructive, inspectable, and safe to resume."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest
from test_portfolio_team import setup as evidence_fixture
from test_portfolio_team_models import Transport

from portfolio_team.automation import run_cycle
from portfolio_team.coordinator import run_review
from portfolio_team.evaluation import EvalCase, EvalCatalog, evaluate
from portfolio_team.evidence import BudgetDeferredError, EvidenceError, make_bundle
from portfolio_team.model_runtime import ModelSettings, RemoteProvider
from portfolio_team.openai_runtime import OpenAIProvider, OpenAISettings, ReviewTeam
from portfolio_team.preflight import preflight_review
from portfolio_team.store import Store


@pytest.fixture
def setup(tmp_path):
    return evidence_fixture.__wrapped__(tmp_path)


def clock(monkeypatch, day):
    instant = dt.datetime(2026, 9, day, 12, tzinfo=dt.UTC)
    monkeypatch.setattr("portfolio_team.store.budget_now", lambda: instant)


def seed_usage(store, bundle):
    seed = RemoteProvider(ModelSettings(model="seed"), Transport(bundle, missing_usage=True))
    run_review(store, bundle, seed, single_reviewer=True)


def test_preflight_read_only_and_daily_deferral_resumes_without_lost_attempt(setup, monkeypatch):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    clock(monkeypatch, 10)
    transport = Transport(bundle)
    provider = OpenAIProvider(
        OpenAISettings(model="gpt-6-astra", max_output_reservation_per_day=12000), transport
    )
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        seed_usage(store, bundle)
        before = store.db.total_changes
        check = preflight_review(store, bundle, provider, single_reviewer=True)
        assert store.db.total_changes == before
        assert not check["ready"]
        assert check["next_role"] == "chief_pm"
        assert check["attempts_remaining"] == 2
        assert check["budget"]["blocked_dimensions"] == ["daily_output"]
        assert check["budget"]["retry_after_utc"] == "2026-09-11T00:00:00+00:00"
        for _ in range(3):
            with pytest.raises(BudgetDeferredError):
                run_review(store, bundle, provider, single_reviewer=True)
        assert transport.calls == 0
        assert store.next_attempt(check["review_id"], "chief_pm") == 1
        row = store.db.execute("SELECT * FROM reviews WHERE id=?", (check["review_id"],)).fetchone()
        assert row["status"] == "deferred_budget"
        assert row["decision"] is None
        assert json.loads(row["error"])["dimensions"]["daily_output"]["charged"] == 4096
        clock(monkeypatch, 11)
        assert preflight_review(store, bundle, provider, single_reviewer=True)["ready"]
        completed = run_review(store, bundle, provider, single_reviewer=True)
        assert completed.review_id == check["review_id"]
        assert transport.calls == 1
        assert store.model_calls(completed.review_id)[0]["attempt"] == 1
        assert preflight_review(store, bundle, provider, single_reviewer=True)["completed"]


def test_cycle_automatically_revisits_budget_pause_without_repeat_api_calls(setup, monkeypatch):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    monkeypatch.setattr("portfolio_team.automation.load_catalog", lambda _: catalog)
    clock(monkeypatch, 10)
    transport = Transport(bundle)
    provider = OpenAIProvider(
        OpenAISettings(model="gpt-6-astra", max_output_reservation_per_day=12000), transport
    )
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        seed_usage(store, bundle)
        for _ in range(2):
            deferred = run_cycle(store, root, root / "unused.toml", provider)
            assert deferred["rows"][0]["status"] == "deferred_budget"
            assert deferred["dispatched_reviews"] == 0
            assert deferred["incomplete"]
        assert transport.calls == 0
        clock(monkeypatch, 11)
        complete = run_cycle(store, root, root / "unused.toml", provider)
        assert complete["rows"][0]["status"] == "completed"
        assert transport.calls == 4
        assert (
            run_cycle(store, root, root / "unused.toml", provider)["rows"][0]["status"] == "cached"
        )
        assert transport.calls == 4


def test_chief_budget_pause_preserves_completed_specialists(setup, monkeypatch):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    clock(monkeypatch, 10)
    worker_transport = Transport(bundle, missing_usage=True)
    chief_transport = Transport(bundle)
    workers = RemoteProvider(
        ModelSettings(model="gemini-test", max_output_reservation_per_day=20000), worker_transport
    )
    team = ReviewTeam(workers, OpenAIProvider(OpenAISettings(model="gpt-6-astra"), chief_transport))
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        with pytest.raises(BudgetDeferredError):
            run_review(store, bundle, team)
        assert worker_transport.calls == 3
        assert chief_transport.calls == 0
        assert preflight_review(store, bundle, team)["next_role"] == "chief_pm"
        clock(monkeypatch, 11)
        decision = run_review(store, bundle, team)
        assert worker_transport.calls == 3
        assert chief_transport.calls == 1
        assert len(decision.assessments) == 4


def test_reservation_failure_rolls_back_attempt_request_and_event(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        store.start("test-atomic", bundle.digest())
        artifacts = store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        events = store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        store.db.execute(
            "CREATE TRIGGER fail_reservation BEFORE INSERT ON model_calls "
            "BEGIN SELECT RAISE(ABORT, 'test reservation failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.begin_model_attempt(
                "test-atomic",
                "chief_pm",
                2,
                request_json='{"atomic":true}',
                input_tokens=10,
                output_tokens=10,
                limits=(100, 100, 100, 100),
            )
        assert store.next_attempt("test-atomic", "chief_pm") == 1
        assert not store.model_calls("test-atomic")
        assert store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == artifacts
        assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events


@pytest.mark.parametrize("limits", [(100, 5, 100, 100), (100, 100, 100, 5)])
def test_midnight_does_not_fix_per_review_or_oversized_request_limits(tmp_path, limits):
    with Store(tmp_path / "registry") as store:
        budget = store.model_budget("new", input_tokens=1, output_tokens=10, limits=limits)
        assert not budget["allowed"]
        assert budget["retry_after_utc"] is None


def test_real_error_is_not_hidden_by_simultaneous_budget_deferral(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)

    class MixedProvider(RemoteProvider):
        def prepare(self, role, bundle, previous=()):
            request = super().prepare(role, bundle, previous)
            if role == "research":
                request = request.model_copy(
                    update={
                        "settings": request.settings.model_copy(update={"max_output_tokens": 8192})
                    }
                )
            return request

    provider = MixedProvider(
        ModelSettings(model="test", concurrent_requests=3, max_output_reservation_per_day=4096),
        Transport(bundle, failure="auth"),
    )
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        with pytest.raises(EvidenceError, match="provider_http_401"):
            run_review(store, bundle, provider)
        assert store.db.execute("SELECT status FROM reviews").fetchone()[0] == "failed"


def test_evaluation_distinguishes_budget_pause_from_failure(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    cases = EvalCatalog(
        schema_version="pmteam-reference-eval/1",
        cases=(EvalCase(id="test", comparison="test", expected_issue_ids=("daily_series",)),),
    )
    transport = Transport(bundle)
    provider = OpenAIProvider(
        OpenAISettings(model="gpt-6-astra", max_output_reservation_per_day=1), transport
    )
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        target = evaluate(store, cases, provider, root / "evaluations")
        rows = json.loads((target / "results.json").read_text())["rows"]
        assert all(row["status"] == "deferred_budget" for row in rows[1:])
        assert all(row["covered"] is None for row in rows[1:])
        assert "deferred_budget" in (target / "report.md").read_text()
        assert store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
        assert transport.calls == 0
