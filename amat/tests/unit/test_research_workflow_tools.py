"""Research repair, cross-session memory, paired execution and chart provenance."""

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest
from test_autoresearch import make_workspace

from autoresearch.contracts import Feature, Proposal, apply_proposal
from autoresearch.followup import run_matched_task
from autoresearch.ledger import BudgetPauseError, Ledger
from autoresearch.loop import finish, register, run_loop
from autoresearch.memory import MEMORY_PATH, _records, known_candidates, related_experiments
from autoresearch.proposer import context, model_proposal
from autoresearch.search import choose_experiment
from autoresearch.tools import inspect_tool
from autoresearch.workspace import digest_file, load_program, write_json
from llmrouter.chain import Chain, RepairableResponseError, Router, Target
from llmrouter.contracts import ChatRequest, ChatResponse
from portfolio_team.cli import main as team_main
from portfolio_team.coordinator import run_review
from portfolio_team.evidence import load_catalog, make_bundle
from portfolio_team.providers import FakeProvider
from portfolio_team.research_queue import TaskApproval, build_queue
from portfolio_team.store import Store


@pytest.mark.parametrize("repaired", [True, False])
def test_one_repair_is_budgeted_and_does_not_expose_invalid_inputs(tmp_path, monkeypatch, repaired):
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-key")
    requests = []

    class Transport:
        async def generate(self, request, *, model, api_key, base_url):
            requests.append(request)
            return ChatResponse(
                provider="gemini",
                model=model,
                input_tokens=20,
                output_tokens=10,
                text="valid" if repaired and len(requests) > 1 else "invalid secret",
            )

    def validate(text):
        if text != "valid":
            raise RepairableResponseError("proposal_duplicate", "Choose a new recipe.")

    router = Router(
        Chain(targets=(Target(provider="gemini", model="fixture"),)),
        transports={"gemini": Transport()},
    )
    request = ChatRequest(
        system="Return a proposal",
        user="{}",
        schema_json="{}",
        max_output_tokens=256,
        timeout_seconds=1,
    )
    with Ledger(tmp_path, 10000) as ledger:
        response = asyncio.run(router.complete(request, ledger, validate=validate, repair=True))
        assert bool(response) == repaired
        assert len(requests) == 2
        assert ledger.db.execute("SELECT SUM(charged) FROM calls").fetchone()[0] == 60
        assert "Choose a new recipe" in requests[1].system
        assert "invalid secret" not in requests[1].canonical()


def test_repair_cannot_bypass_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-key")

    class Transport:
        async def generate(self, request, *, model, api_key, base_url):
            return ChatResponse(
                provider="gemini", model=model, input_tokens=None, output_tokens=None, text="bad"
            )

    def invalid(_text):
        raise RepairableResponseError("proposal_schema", "Follow the schema.")

    router = Router(
        Chain(targets=(Target(provider="gemini", model="fixture"),)),
        transports={"gemini": Transport()},
    )
    request = ChatRequest(
        system="", user="", schema_json="{}", max_output_tokens=256, timeout_seconds=1
    )
    with Ledger(tmp_path, 3500) as ledger:
        with pytest.raises(BudgetPauseError):
            asyncio.run(router.complete(request, ledger, validate=invalid, repair=True))
        assert ledger.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1


def seed_memory(root):
    program = load_program(root)
    proposal = Proposal(
        hypothesis="A momentum confirmation may filter weak trends.",
        features=(Feature(kind="momentum", lookback=21, threshold=0),),
    )
    candidate = apply_proposal(program.baseline, proposal)
    write_json(
        root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
    )
    relative = f"outputs/runs/{candidate.digest()}/objective.json"
    result = {
        "score": -0.1,
        "score_version": "exposure-matched-incremental/1",
        "train": {},
        "stress": {},
        "failed_gates": ["no_incremental_exposure_matched_information"],
    }
    write_json(root / relative, result)
    with Ledger(root, 10000) as ledger:
        index = register(
            ledger, candidate.digest(), program.baseline.digest(), proposal.canonical()
        )
        finish(
            ledger,
            index,
            "discard",
            {**result, "artifact_hashes": {relative: digest_file(root / relative)}},
        )
        ledger.event("experiment_completed", experiment=index)
    return candidate, relative


def test_memory_freezes_compatible_results_and_does_not_reread_source(tmp_path):
    source = make_workspace(tmp_path / "prior")
    candidate, relative = seed_memory(source)
    target = make_workspace(tmp_path / "new")
    program = load_program(target)
    rows = related_experiments(target, program, candidate)
    assert len(rows) == 1
    assert rows[0]["candidate"] == candidate.digest()
    retrieved = inspect_tool(target, "related", None, query="momentum")
    assert retrieved["related_experiments"][0]["candidate"] == candidate.digest()
    assert known_candidates(target, program) == {candidate.digest()}
    choice = choose_experiment(target, program, program.baseline, [])
    assert apply_proposal(*choice).digest() != candidate.digest()
    payload = context(program, candidate, [], related=rows)
    assert payload["related_experiments"]
    original = (target / MEMORY_PATH).read_bytes()
    (source / relative).write_text("changed after snapshot")
    assert related_experiments(target, program, candidate) == rows
    assert (target / MEMORY_PATH).read_bytes() == original
    (target / MEMORY_PATH).write_text("{}")
    with pytest.raises(ValueError, match="hash_mismatch"):
        related_experiments(target, program, candidate)


def test_inspection_before_the_first_backtest(tmp_path):
    root = make_workspace(tmp_path / "fresh")
    assert not (root / "research.sqlite3").exists()
    assert inspect_tool(root, "related", None, query="momentum") == {"related_experiments": []}
    with pytest.raises(ValueError, match="no completed candidate measurement"):
        inspect_tool(root, "charts", None)
    assert not (root / "research.sqlite3").exists()


@pytest.mark.parametrize(
    "problem",
    [
        "checkpoint",
        "artifact",
        "future_time",
        "different_period",
        "different_gate",
        "different_baseline",
    ],
)
def test_memory_excludes_contaminated_or_invalid_evidence(tmp_path, problem):
    source = make_workspace(tmp_path / "prior")
    _, relative = seed_memory(source)
    if problem == "checkpoint":
        (source / "checkpoints").mkdir()
    elif problem == "artifact":
        (source / relative).write_text("tampered")
    elif problem == "future_time":
        with Ledger(source, 10000) as ledger, ledger.db:
            ledger.db.execute("UPDATE events SET recorded_at='2099-01-01T00:00:00+00:00'")
    target = make_workspace(tmp_path / "new")
    program = load_program(target)
    if problem == "different_period":
        program = program.model_copy(update={"train_end": dt.date(2021, 12, 30)})
    if problem == "different_gate":
        program = program.model_copy(update={"max_drawdown": 0.1})
    if problem == "different_baseline":
        program = program.model_copy(
            update={
                "baseline": apply_proposal(
                    program.baseline,
                    Proposal(
                        hypothesis="A different baseline attenuation rule.",
                        features=(Feature(kind="momentum", lookback=63, threshold=0),),
                    ),
                )
            }
        )
    with pytest.raises(ValueError):
        _records(source, program, target, dt.datetime.now(dt.UTC))


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    root = make_workspace(tmp_path_factory.mktemp("workflow") / "research")
    result = run_loop(root, iterations=1)
    return root, result


def test_charts_match_recorded_tables_and_reject_tampering(measured):
    root, result = measured
    row = result["experiments"][-1]
    output = inspect_tool(root, "charts", row["candidate"])
    data = json.loads(Path(output["data"]).read_text())
    assert len(data["series"]) == 4
    for series in data["series"].values():
        assert series["rows"][0]["equity_index_100"] == 100
        original = json.loads((root / series["source"]).read_text())
        assert series["rows"][-1]["equity_index_100"] == pytest.approx(
            100 * original[-1]["equity"] / original[0]["equity"]
        )
    source = root / data["series"]["Candidate"]["source"]
    saved = source.read_bytes()
    try:
        source.write_text("changed")
        with pytest.raises(ValueError, match="artifact changed"):
            inspect_tool(root, "charts", row["candidate"])
    finally:
        source.write_bytes(saved)


def test_approved_followup_runs_both_sides_registers_and_resumes(measured, monkeypatch):
    root, result = measured
    catalog = load_catalog(root / "catalog.toml")
    bundle, blobs = make_bundle(catalog, catalog.comparisons[0].id, root)
    with Store(root / "data/governance/pmteam") as store:
        store.import_bundle(bundle, blobs)
        decision = run_review(store, bundle, FakeProvider())
    queue = build_queue(bundle, decision)
    task = next(t for t in queue.tasks if t.operation == "matched_backtest")
    assert team_main(["--root", str(root), "plan", decision.review_id]) == 0
    assert (
        team_main(
            [
                "--root",
                str(root),
                "approve-task",
                decision.review_id,
                task.id,
                "--by",
                "test-operator",
            ]
        )
        == 0
    )
    approval = TaskApproval.model_validate_json(
        (
            root / "outputs/governance" / decision.review_id / "approvals" / f"{task.id}.json"
        ).read_text()
    )
    from autoresearch.followup import execute as real_execute

    attempted_roles = []

    def interrupted_worker(workspace, program, candidate, target):
        attempted_roles.append(target.name)
        if attempted_roles == ["candidate", "control"]:
            raise ValueError("injected interruption after candidate completed")
        return real_execute(workspace, program, candidate, target)

    monkeypatch.setattr("autoresearch.followup.execute", interrupted_worker)
    with pytest.raises(SystemExit):
        team_main(
            [
                "--root",
                str(root),
                "execute-task",
                decision.review_id,
                task.id,
                "--run",
            ]
        )
    assert (
        team_main(["--root", str(root), "execute-task", decision.review_id, task.id, "--run"]) == 0
    )
    receipt = run_matched_task(root, queue, task.id, approval)
    assert attempted_roles == ["candidate", "control", "control"]
    assert receipt.status == "completed"
    new_catalog = load_catalog(root / receipt.catalog_path)
    new_bundle, _ = make_bundle(new_catalog, new_catalog.comparisons[0].id, root)
    assert new_bundle.candidate.first_session == new_bundle.control.first_session
    assert new_bundle.candidate.sessions == new_bundle.control.sessions
    with Store(root / "data/governance/pmteam") as store:
        assert store.bundle(receipt.evidence_sha256) == new_bundle
    with Ledger(root, 10000) as ledger:
        assert ledger.get("incumbent") == result["incumbent"]
    monkeypatch.setattr(
        "autoresearch.followup.execute", lambda *_a, **_kw: pytest.fail("repeated worker")
    )
    assert run_matched_task(root, queue, task.id, approval) == receipt
    with pytest.raises(ValueError, match="approval"):
        run_matched_task(root, queue, task.id, approval.model_copy(update={"task_id": "0" * 16}))


def test_model_proposal_records_local_repair(measured, tmp_path, monkeypatch):
    root, _ = measured
    program = load_program(root)
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-key")
    attempts = []
    proposal = Proposal(
        hypothesis="Test a distinct momentum rule.",
        features=(Feature(kind="momentum", lookback=63, threshold=0),),
    )

    class Transport:
        async def generate(self, request, *, model, api_key, base_url):
            attempts.append(request)
            return ChatResponse(
                provider="gemini",
                model=model,
                input_tokens=10,
                output_tokens=10,
                text="{}" if len(attempts) == 1 else proposal.canonical(),
            )

    router = Router(
        Chain(targets=(Target(provider="gemini", model="fixture"),)),
        transports={"gemini": Transport()},
    )
    with Ledger(tmp_path, 100000) as ledger:
        selected = asyncio.run(
            model_proposal(
                router, ledger, program.model_copy(update={"frozen_files": {}}), program.baseline
            )
        )
        assert selected == proposal
        assert attempts[0].reasoning_effort == "low"
        assert len(attempts) == 2
        errors = [
            json.loads(r[0])["error_code"]
            for r in ledger.db.execute("SELECT response FROM calls ORDER BY id")
        ]
        assert errors == ["proposal_schema", None]
