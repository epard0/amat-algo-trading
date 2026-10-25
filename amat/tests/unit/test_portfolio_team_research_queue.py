import json

import pytest
from test_portfolio_team import setup as evidence_fixture

from portfolio_team.automation import run_cycle
from portfolio_team.cli import main
from portfolio_team.coordinator import run_review
from portfolio_team.evidence import make_bundle
from portfolio_team.providers import FakeProvider
from portfolio_team.research_queue import (
    ExperimentQueue,
    approve_task,
    build_queue,
    dispatch_spec,
    execute_approved_task,
    run_registered_task,
)
from portfolio_team.store import Store


def test_queue_is_typed_deterministic_and_non_executable(tmp_path):
    root, catalog = evidence_fixture.__wrapped__(tmp_path)
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        # Fake review is deterministic and produces a persisted completed decision.
        from portfolio_team.coordinator import run_review
        from portfolio_team.providers import FakeProvider

        decision = run_review(store, bundle, FakeProvider())
        queue = build_queue(bundle, decision)
    assert isinstance(queue, ExperimentQueue)
    assert queue.tasks
    assert all(task.status == "proposed" for task in queue.tasks)
    assert all(task.execution_authority == "none" for task in queue.tasks)
    assert all("command" not in task.objective.lower() for task in queue.tasks)
    assert queue == build_queue(bundle, decision)


def test_queue_does_not_turn_request_text_into_commands(tmp_path):
    root, catalog = evidence_fixture.__wrapped__(tmp_path)
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        from portfolio_team.coordinator import run_review
        from portfolio_team.providers import FakeProvider

        decision = run_review(store, bundle, FakeProvider())
    queue = build_queue(bundle, decision)
    for task in queue.tasks:
        assert task.parameters_json == "{}"
        assert task.operation in {
            "matched_backtest",
            "cost_stress",
            "ablation",
            "walkforward",
            "session_pattern",
            "stat_arb",
        }


def test_dispatch_requires_explicit_approval_and_is_non_executable(tmp_path):
    root, catalog = evidence_fixture.__wrapped__(tmp_path)
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        from portfolio_team.coordinator import run_review
        from portfolio_team.providers import FakeProvider

        decision = run_review(store, bundle, FakeProvider())
    queue = build_queue(bundle, decision)
    task = queue.tasks[0]
    approval = approve_task(queue, task.id, "operator@example")
    assert approval.queue_sha256 == queue.digest()
    spec = dispatch_spec(task)
    assert spec["execute"] is False
    assert isinstance(spec["argv_template"], tuple)
    assert task.id in approval.canonical()
    result = execute_approved_task(queue, task.id, approval)
    assert result.status == "blocked"
    assert result.reason == "adapter_not_registered"
    assert result.execution_authority == "none"


@pytest.fixture
def planned(tmp_path):
    root, catalog = evidence_fixture.__wrapped__(tmp_path)
    (root / "pyproject.toml").touch()
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "data/governance/pmteam") as store:
        store.import_bundle(bundle, blobs)
        decision = run_review(store, bundle, FakeProvider())
    assert main(["--root", str(root), "plan", decision.review_id]) == 0
    target = root / "outputs/governance" / decision.review_id
    queue = ExperimentQueue.model_validate_json((target / "research_queue.v2.json").read_text())
    return root, target, queue


@pytest.mark.parametrize("field", ["review_id", "task_id", "queue_sha256"])
def test_worker_refuses_approval_for_different_identity(planned, field):
    root, _, queue = planned
    task = queue.tasks[0]
    approval = approve_task(queue, task.id, "operator")
    approval = approval.model_copy(update={field: "0" * 64 if field != "task_id" else "0" * 16})
    with pytest.raises(ValueError, match="approval does not match"):
        execute_approved_task(queue, task.id, approval)
    with pytest.raises(ValueError, match="approval does not match"):
        run_registered_task(root, queue, task.id, approval)


def test_run_gate_cannot_launch_candidate_only_study(planned, monkeypatch):
    import subprocess

    # A regression must never put the host's real environment in pytest locals.
    monkeypatch.setattr("os.environ", {"PATH": "/usr/bin"})
    root, _, queue = planned
    task = queue.tasks[0].model_copy(
        update={
            "operation": "matched_backtest",
            "candidate_config": "candidate.toml",
            "control_config": "control.toml",
            "snapshot": "snapshot",
        }
    )
    queue = queue.model_copy(update={"tasks": (task,)})
    approval = approve_task(queue, task.id, "operator")

    def forbidden(*_args, **_kwargs):
        pytest.fail("review coordinator must never launch a study process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    result = run_registered_task(root, queue, task.id, approval)
    assert result.status == "blocked"
    assert result.reason == "adapter_not_registered"
    assert dispatch_spec(task)["argv_template"] == ()


@pytest.mark.parametrize("command", ["dispatch-task", "execute-task"])
def test_cli_refuses_copied_approval(planned, command):
    root, target, queue = planned
    first, second = queue.tasks[:2]
    approval = approve_task(queue, first.id, "operator")
    path = target / "approvals" / f"{second.id}.json"
    path.parent.mkdir()
    path.write_text(approval.canonical())
    with pytest.raises(SystemExit) as error:
        main(["--root", str(root), command, queue.source_review_id, second.id])
    assert error.value.code == 1


def test_repeated_approval_and_blocked_execution_are_idempotent(planned):
    root, target, queue = planned
    task = queue.tasks[0]
    common = ["--root", str(root)]
    approve = [*common, "approve-task", queue.source_review_id, task.id, "--by", "operator"]
    assert main(approve) == 0
    path = target / "approvals" / f"{task.id}.json"
    original = path.read_bytes()
    assert main(approve) == 0
    assert path.read_bytes() == original
    execute = [*common, "execute-task", queue.source_review_id, task.id]
    assert main(execute) == 1
    assert main([*execute, "--run"]) == 1
    result = json.loads((target / "executions" / f"{task.id}.json").read_text())
    assert result["status"] == "blocked"
    assert result["return_code"] is None


def test_cycle_exports_queue_and_reuses_it_without_more_reviews(tmp_path, monkeypatch):
    root, catalog = evidence_fixture.__wrapped__(tmp_path)
    monkeypatch.setattr("portfolio_team.automation.load_catalog", lambda _: catalog)
    with Store(root / "registry") as store:
        first = run_cycle(store, root, root / "catalog.toml", FakeProvider())
        path = (
            root / "outputs/governance" / first["rows"][0]["review_id"] / "research_queue.v2.json"
        )
        original = path.read_bytes()
        attempts = store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        second = run_cycle(store, root, root / "catalog.toml", FakeProvider())
        assert second["rows"][0]["status"] == "cached"
        assert second["dispatched_reviews"] == 0
        assert path.read_bytes() == original
        assert store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == attempts
        queue = ExperimentQueue.model_validate_json(original)
        assert queue.tasks
