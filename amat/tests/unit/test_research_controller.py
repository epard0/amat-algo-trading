"""Controller dispatch, evidence access, bounds, and audit provenance."""

import json
from pathlib import Path

import pytest

from autoresearch.cli import argument_parser
from autoresearch.controller import ControllerDecision, choose_action, run_controller
from autoresearch.controller_context import read_research_ledger
from autoresearch.ledger import Ledger
from autoresearch.workspace import digest_file, write_json

ROOT = Path(__file__).resolve().parents[2]


def options(tmp_path, *extra):
    return argument_parser().parse_args(
        [
            "control",
            "3",
            "--source",
            str(ROOT),
            "--workspace",
            str(tmp_path / "research"),
            "--no-open",
            *extra,
        ]
    )


def decision(action="run", iterations=1, **extra):
    return ControllerDecision(
        action=action,
        iterations=iterations,
        brief="Ablate the proposed signal and preserve the frozen comparison controls."
        if action == "run"
        else "",
        rationale="Measured evidence supports a bounded discriminating experiment.",
        stopping_condition="Stop at the experiment limit or repeated invalid candidates.",
        **extra,
    )


def measured(root, amount):
    root.mkdir(exist_ok=True)
    with Ledger(root, 100000) as ledger:
        ledger.put("status", "batch_completed")
        for _ in range(amount):
            index = len(ledger.history()) + 1
            with ledger.db:
                ledger.db.execute(
                    "INSERT INTO experiments VALUES(?,?,?,?,?,?,NULL)",
                    (
                        index,
                        f"candidate-{index}",
                        "parent",
                        json.dumps({"hypothesis": "A mechanism"}),
                        "keep",
                        json.dumps({"score": index, "train": {"sharpe": 1}}),
                    ),
                )
    write_json(root / "report.json", {"status": "batch_completed", "batch": {"completed": amount}})


def test_controller_reads_guides_and_continues_one_session(tmp_path, monkeypatch):
    args = options(tmp_path)
    decisions = iter(
        [
            decision(),
            decision("inspect", 0, inspect_ids=(1,)),
            decision(iterations=2, evidence_ids=(1,)),
            decision("stop", 0, evidence_ids=(3,)),
        ]
    )
    contexts, starts, continuations = [], [], []

    def choose(_router, _audit, context, remaining):
        contexts.append(json.loads(json.dumps(context)))
        assert remaining >= 0
        return next(decisions)

    def start(args, *, initial_iterations):
        starts.append((args.workspace, args.iterations, initial_iterations, args.brief))
        measured(args.workspace, initial_iterations)
        return 0

    def continuation(root, amount, built_in, brief):
        continuations.append((root, amount, built_in, brief))
        measured(root, amount)
        return json.loads((root / "report.json").read_text())

    monkeypatch.setattr("autoresearch.controller.choose_action", choose)
    result = run_controller(
        args, start=start, continue_batch=continuation, session_directory=tmp_path
    )
    assert result["status"] == "completed_limits"
    assert len(starts) == len(continuations) == 1
    assert starts[0][1:3] == (3, 1)
    assert continuations[0][1] == 2
    assert starts[0][0] == continuations[0][0] == args.workspace
    assert contexts[0]["baseline_configuration"]
    assert contexts[-1]["ledger"]["experiments"][-1]["experiment"] == 3
    assert len(result["batches"]) == 2
    assert Path(result["audit_directory"]) != args.workspace
    assert (Path(result["audit_directory"]) / "decision-4.json").is_file()


def test_read_only_inspection_never_recovers_or_mutates_research(tmp_path):
    with Ledger(tmp_path, 10000) as ledger:
        ledger.put("status", "running")
        ledger.phase("backtesting", 1)
    before = digest_file(tmp_path / "research.sqlite3")
    snapshot = read_research_ledger(tmp_path)
    assert snapshot["state"]["status"] == "running"
    assert snapshot["state"]["progress"]["phase"] == "backtesting"
    assert digest_file(tmp_path / "research.sqlite3") == before
    with pytest.raises(ValueError, match="unknown"):
        read_research_ledger(tmp_path, (999,))
    assert digest_file(tmp_path / "research.sqlite3") == before


def test_plan_only_and_missing_controller_never_launch(tmp_path, monkeypatch):
    args = options(tmp_path, "--plan-only")
    monkeypatch.setattr("autoresearch.controller.choose_action", lambda *_: decision())
    result = run_controller(
        args,
        start=lambda *_a, **_kw: pytest.fail("launched"),
        continue_batch=lambda *_: pytest.fail("continued"),
        session_directory=tmp_path,
    )
    assert result["status"] == "planned"
    assert not args.workspace.exists()
    args.workspace = tmp_path / "unavailable"
    monkeypatch.setattr("autoresearch.controller.choose_action", lambda *_: None)
    result = run_controller(
        args,
        start=lambda *_a, **_kw: pytest.fail("launched"),
        continue_batch=lambda *_: pytest.fail("continued"),
        session_directory=tmp_path,
    )
    assert result["status"] == "paused_controller_unavailable"


def test_launch_failure_is_audited_and_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("autoresearch.controller.choose_action", lambda *_: decision())
    attempts = []

    def fail(*_args, **_kwargs):
        attempts.append(1)
        raise ValueError("fixture launch failure")

    result = run_controller(
        options(tmp_path), start=fail, continue_batch=fail, session_directory=tmp_path
    )
    assert result["status"] == "failed" and attempts == [1]
    assert "fixture launch failure" in result["reason"]


@pytest.mark.parametrize("bad", [decision(iterations=3), decision(evidence_ids=(999,))])
def test_controller_decisions_must_fit_allowance_and_reference_real_evidence(tmp_path, bad):
    class Router:
        async def complete(self, request, audit, *, validate):
            assert request.task_class == "complex"
            assert request.persistence == "metadata_only"
            with pytest.raises(ValueError):
                validate(bad.canonical())

    with Ledger(tmp_path, 10000) as ledger:
        assert choose_action(Router(), ledger, {"ledger": {"experiment_index": []}}, 2) is None


def test_intraday_context_never_contains_validation(tmp_path):
    with Ledger(tmp_path, 10000):
        pass
    write_json(tmp_path / "program.json", {"schema": "intraday-research/1"})
    write_json(
        tmp_path / "report.json",
        {
            "status": "completed",
            "incumbent": "c",
            "experiments": [
                {
                    "id": 0,
                    "status": "baseline",
                    "recipe": {},
                    "result": {"train": {"sharpe": 1}},
                    "validation": {"SECRET_VALIDATION": 99},
                }
            ],
        },
    )
    (tmp_path / "report.sha256").write_text(digest_file(tmp_path / "report.json"))
    result = read_research_ledger(tmp_path, (0,))
    assert result["experiment_index"] == [{"id": 0, "status": "baseline"}]
    assert "SECRET_VALIDATION" not in json.dumps(result)
    assert result["experiments"][0]["train"] == {"sharpe": 1}


def test_intraday_controller_dispatches_followups_without_validation_context(tmp_path, monkeypatch):
    args = options(tmp_path, "--intraday")
    choices = iter([decision(), decision(iterations=2), decision("stop", 0)])
    seen = []

    def choose(_router, _audit, context, _remaining):
        assert context["parameters"]["mode"] == "intraday"
        assert "recipe_schema" in context["research_capabilities"]
        assert "SECRET_VALIDATION" not in json.dumps(context)
        return next(choices)

    def batch(root, amount):
        root.mkdir(exist_ok=True)
        with Ledger(root, 10000):
            pass
        report = root / "report.json"
        rows = (
            json.loads(report.read_text())["experiments"]
            if report.exists()
            else [
                {
                    "id": 0,
                    "status": "baseline",
                    "recipe": {},
                    "result": {"train": {}},
                }
            ]
        )
        for _ in range(amount):
            rows.append(
                {
                    "id": len(rows),
                    "status": "keep",
                    "recipe": {},
                    "result": {"train": {}},
                    "validation": {"SECRET_VALIDATION": 123},
                }
            )
        write_json(root / "program.json", {"schema": "intraday-research/1"})
        write_json(
            report,
            {
                "status": "completed" if len(rows) == 4 else "batch_completed",
                "experiments": rows,
                "incumbent": "fixture",
            },
        )
        (root / "report.sha256").write_text(digest_file(report))
        return json.loads(report.read_text())

    def start(args, *, initial_iterations):
        seen.append(args.brief)
        batch(args.workspace, initial_iterations)
        return 0

    def continuation(root, amount, _built_in, brief):
        seen.append(brief)
        return batch(root, amount)

    monkeypatch.setattr("autoresearch.controller.choose_action", choose)
    result = run_controller(
        args, start=start, continue_batch=continuation, session_directory=tmp_path
    )
    assert result["status"] == "completed_limits"
    assert [b["completed"] for b in result["batches"]] == [1, 2]
    assert len(seen) == 2 and all("Ablate" in brief for brief in seen)


def test_frozen_daily_dispatch_passes_controller_guidance_as_data(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from autoresearch.cli import run_frozen_start

    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    write_json(tmp_path / "report.json", {"status": "batch_completed"})
    monkeypatch.setattr("autoresearch.cli.subprocess.run", run)
    brief = "Compare the new hypothesis; $(this is model text, never a shell command)."
    result = run_frozen_start(tmp_path, 2, False, brief)
    assert result["status"] == "batch_completed"
    assert seen[0][0][-1] == brief
    assert seen[0][1]["env"]["PYTHONPATH"] == str(tmp_path / "runtime")
    assert "shell" not in seen[0][1]


def test_controller_resume_reconciles_interrupted_launch_and_never_replays_it(
    tmp_path, monkeypatch
):
    args = options(tmp_path)
    monkeypatch.setattr("autoresearch.controller.choose_action", lambda *_: decision())

    def interrupted(args, *, initial_iterations):
        measured(args.workspace, initial_iterations)
        raise ValueError("interrupted after worker completed")

    result = run_controller(
        args, start=interrupted, continue_batch=lambda *_: None, session_directory=tmp_path
    )
    assert result["status"] == "failed"
    choices = iter([decision(iterations=2), decision("stop", 0)])
    monkeypatch.setattr("autoresearch.controller.choose_action", lambda *_: next(choices))
    args.resume = True
    calls = []

    def continuation(root, amount, _built_in, _brief):
        calls.append(amount)
        measured(root, amount)
        return {}

    result = run_controller(
        args,
        start=lambda *_a, **_kw: pytest.fail("replayed initial launch"),
        continue_batch=continuation,
        session_directory=tmp_path,
    )
    assert result["status"] == "completed_limits"
    assert calls == [2]
    assert result["batches"][0]["status"] == "recovered_interrupted_launch"
    assert result["batches"][0]["completed"] == 1
    assert len(read_research_ledger(args.workspace)["experiment_index"]) == 3
