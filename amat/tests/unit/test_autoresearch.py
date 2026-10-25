"""Causality, protected inputs, failover and a real strategy-development cycle."""

from __future__ import annotations

import ast
import asyncio
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from autoresearch.contracts import (
    Candidate,
    Feature,
    Patch,
    Proposal,
    apply_proposal,
    validate_candidate,
)
from autoresearch.features import feature_overlay, feature_values
from autoresearch.ledger import BudgetPauseError, Ledger
from autoresearch.loop import run_loop, worker_environment
from autoresearch.proposer import MAX_CONTEXT_BYTES, context, search_proposal
from autoresearch.workspace import initialize, load_program
from llmrouter.chain import Chain, Router, Target
from llmrouter.contracts import ChatRequest, ChatResponse

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/strategy/amat_v6.toml"
SNAPSHOT = ROOT / "data/snapshots/alpaca-sip-2026-08-11"


@pytest.fixture
def baseline():
    return Candidate(config=load_v6_config(CONFIG).config)


@pytest.mark.parametrize(
    "path,value",
    [
        ("costs.baseline_one_way_bps", 0),
        ("execution.signal_to_execution_lag_sessions", 0),
        ("warmup.min_completed_sessions", 5),
        ("constraints.target_annualized_volatility", 1),
        ("universe.clusters", []),
        ("trend.horizons_sessions", [5, 400]),
        ("trend.deadband_abs", 0.9),
    ],
)
def test_proposals_cannot_rewrite_experiment_contract(baseline, path, value):
    proposal = Proposal(
        hypothesis="Attempt a prohibited modification.", patches=(Patch(path=path, value=value),)
    )
    with pytest.raises(ValueError):
        apply_proposal(baseline, proposal)


def test_registered_candidate_is_revalidated_at_worker_boundary(baseline):
    payload = baseline.model_dump(mode="json")
    payload["config"]["strategy"]["constraints"]["target_annualized_volatility"] = 0.2
    with pytest.raises(ValueError, match="protected"):
        validate_candidate(baseline, Candidate.model_validate(payload))


@pytest.fixture
def panel(baseline):
    panel = generate_panel(baseline.config.strategy.to_universe(), sessions=400)
    volumes = np.arange(400, dtype=float)[:, None] + np.ones(panel.adjusted_closes.shape)
    return replace(
        panel,
        adjusted_highs=panel.adjusted_closes * 1.01,
        adjusted_lows=panel.adjusted_closes * 0.99,
        session_volumes=volumes,
    )


@pytest.mark.parametrize(
    "kind", ["momentum", "volatility", "drawdown", "volume_ratio", "efficiency"]
)
@pytest.mark.parametrize("rank", [False, True])
def test_feature_recipes_are_prefix_invariant(panel, kind, rank):
    feature = Feature(kind=kind, lookback=21, smoothing=5, cross_sectional_rank=rank, threshold=0)
    expected = feature_values(panel, feature)
    cutoff = 200
    changes = {}
    for field in (
        "adjusted_closes",
        "raw_closes",
        "window_vwaps",
        "window_volumes",
        "adjusted_highs",
        "adjusted_lows",
        "session_volumes",
    ):
        values = getattr(panel, field).copy()
        values[cutoff:] *= 100
        changes[field] = values
    changed = feature_values(replace(panel, **changes), feature)
    np.testing.assert_allclose(changed[:cutoff], expected[:cutoff], equal_nan=True)
    prefix = replace(
        panel,
        sessions=panel.sessions[:cutoff],
        **{field: getattr(panel, field)[:cutoff] for field in changes},
    )
    np.testing.assert_allclose(feature_values(prefix, feature), expected[:cutoff], equal_nan=True)


def test_features_change_allocations_without_increasing_risk(panel):
    feature = Feature(
        kind="volatility", lookback=21, threshold=0, direction="below", attenuation=0.5
    )
    overlay = feature_overlay(panel, (feature,))
    assert np.all(overlay.risk_multipliers == 0.5)
    assert overlay.allow_increase.all()
    assert np.all(feature_overlay(panel, ()).risk_multipliers == 1)


def test_worker_environment_has_no_host_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "fixture-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    environment = worker_environment(tmp_path)
    assert not any("SECRET" in key or "KEY" in key for key in environment)
    assert environment["AMAT_EXECUTION_MODE"] == "backtest"
    assert environment["PYTHONPATH"] == str(tmp_path / "runtime")


def make_workspace(path, **options):
    if not SNAPSHOT.exists():
        pytest.skip("real snapshot not present")
    initialize(
        path,
        ROOT,
        CONFIG,
        SNAPSHOT,
        name="test-features",
        train_end=dt.date(2022, 12, 30),
        validation_end=dt.date(2026, 8, 10),
        max_experiments=options.pop("max_experiments", 5),
        **options,
    )
    return path


def test_init_refuses_executor_mounted_tree():
    with pytest.raises(ValueError, match="outside"):
        initialize(ROOT / "outputs/unsafe-research", ROOT, CONFIG, SNAPSHOT)


@pytest.mark.parametrize("tamper", ["source", "program", "extra", "data"])
def test_frozen_inputs_are_verified_before_dispatch(tmp_path, tamper):
    root = make_workspace(tmp_path / "research")
    if tamper == "source":
        (root / "runtime/amat/backtest/runner.py").write_text("raise RuntimeError('modified')")
    elif tamper == "program":
        (root / "program.json").write_text("{}")
    elif tamper == "extra":
        (root / "runtime/amat/extra.py").touch()
    else:
        (root / "snapshot/manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        load_program(root)


@pytest.mark.slow
def test_real_loop_resumes_and_exports_matched_reviewable_evidence(tmp_path):
    from portfolio_team.automation import run_cycle
    from portfolio_team.providers import FakeProvider
    from portfolio_team.store import Store

    root = make_workspace(tmp_path / "research")
    before = CONFIG.read_bytes()
    first = run_loop(root, iterations=1)
    assert first["status"] == "batch_completed"
    assert first["experiments"][0]["status"] == "baseline"
    assert len(first["experiments"]) == 2
    trial = first["experiments"][-1]
    result = json.loads(trial["result"])
    assert result["train"]["last_session"] == "2022-12-30"
    baseline_result = json.loads(first["experiments"][0]["result"])
    assert baseline_result["score"] == 0
    assert result["score_version"] == "exposure-matched-incremental/1"
    parts = result["score_components"]
    assert result["score"] == pytest.approx(
        result["control_summary"]["exposure_match"]["sharpe_difference"]
        - parts["incremental_complexity_penalty"]
    )
    assert result["absolute_performance_score"] is not None
    assert parts["reference_candidate"] == first["experiments"][0]["candidate"]
    assert "Matched Sharpe gain" in (root / "report.html").read_text()
    assert (
        result["train"]["first_session"]
        == json.loads(first["experiments"][0]["result"])["train"]["first_session"]
    )
    run = root / "outputs/runs" / trial["candidate"] / "primary"
    assert (
        json.loads((run / "daily.json").read_text())[0]["session"]
        == result["train"]["first_session"]
    )
    assert CONFIG.read_bytes() == before
    second = run_loop(root, iterations=1)
    assert len(second["experiments"]) == 3
    assert second["experiments"][0] == first["experiments"][0]
    assert second["experiments"][-1]["candidate"] != trial["candidate"]
    with Store(root / "data/governance/pmteam") as store:
        reviewed = run_cycle(store, root, root / "catalog.toml", FakeProvider(), max_reviews=5)
    assert not reviewed["incomplete"]
    assert len(reviewed["rows"]) == 2
    with Ledger(root, 100000) as ledger:
        program = load_program(root)
        body = json.dumps(context(program, program.baseline, ledger.history()))
    assert "validation.json" not in body
    assert "validation_end" not in body
    assert "2026-08-10" not in body
    assert "artifact_hashes" not in body
    assert "outputs/runs/" not in body
    assert "momentum" in body
    assert len(body.encode()) <= MAX_CONTEXT_BYTES
    assert not (root / "checkpoint.json").exists()


def test_ledger_crash_recovery_lock_and_token_reservations(tmp_path):
    target = Target(provider="openai", model="fixture")
    request = ChatRequest(
        system="test", user="test", schema_json="{}", max_output_tokens=256, timeout_seconds=1
    )
    with Ledger(tmp_path, 4000) as ledger:
        with pytest.raises(ValueError, match="another"):
            Ledger(tmp_path, 4000)
        with ledger.db:
            ledger.db.execute(
                "INSERT INTO experiments(candidate,parent,proposal,status) "
                "VALUES('a','b','{}','running')"
            )
        call = ledger.reserve(target, request)
        with pytest.raises(BudgetPauseError):
            ledger.reserve(target, request)
        ledger.settle(
            call,
            ChatResponse(
                provider="openai", model="fixture", input_tokens=10, output_tokens=10, text="{}"
            ),
        )
        ledger.reserve(target, request)
    with Ledger(tmp_path, 4000) as ledger:
        assert ledger.history()[0]["status"] == "crash"
        assert ledger.history()[0]["error"] == "interrupted"
        assert ledger.db.execute("SELECT charged FROM calls WHERE id=1").fetchone()[0] == 20


@pytest.mark.parametrize("failure", ["quota", "auth", "timeout", "server", "refusal", "malformed"])
def test_router_fails_over_and_accounts_for_each_attempt(tmp_path, monkeypatch, failure):
    from pydantic import BaseModel

    class Output(BaseModel):
        value: int

    class Transport:
        def __init__(self, provider):
            self.provider = provider

        async def generate(self, request, *, model, api_key, base_url):
            return ChatResponse(
                provider=self.provider,
                model=model,
                input_tokens=10,
                output_tokens=5,
                text='{"value":7}' if self.provider == "gemini" else "not-json",
                error_class="ok"
                if failure == "malformed" or self.provider == "gemini"
                else failure,
            )

    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    monkeypatch.setenv("GEMINI_API_KEY", "fixture")
    router = Router(
        Chain(
            targets=(
                Target(provider="openai", model="first"),
                Target(provider="gemini", model="second"),
            )
        ),
        transports={"openai": Transport("openai"), "gemini": Transport("gemini")},
    )
    request = ChatRequest(
        system="test", user="test", schema_json="{}", max_output_tokens=256, timeout_seconds=1
    )
    with Ledger(tmp_path, 10000) as ledger:
        response = asyncio.run(
            router.complete(request, ledger, validate=Output.model_validate_json)
        )
        assert response.provider == "gemini"
        assert ledger.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 2
        assert ledger.db.execute("SELECT SUM(charged) FROM calls").fetchone()[0] == 30


def test_new_package_does_not_enter_strategy_or_executor_import_graph():
    for package in (ROOT / "src/amat", ROOT.parent / "executor/src/executor"):
        for source in package.rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Import):
                    assert all(item.name.split(".")[0] != "autoresearch" for item in node.names)
                if isinstance(node, ast.ImportFrom):
                    assert (node.module or "").split(".")[0] != "autoresearch"


def test_search_avoids_repeated_candidates_and_supports_ablation(baseline):
    proposal = search_proposal(baseline, {baseline.digest()})
    candidate = apply_proposal(baseline, proposal)
    assert candidate.features
    next_proposal = search_proposal(baseline, {baseline.digest(), candidate.digest()})
    assert apply_proposal(baseline, next_proposal).digest() != candidate.digest()
    assert (
        apply_proposal(candidate, Proposal(hypothesis="Remove all feature gates for an ablation."))
        == baseline
    )


def test_keep_discard_and_restart_recover_the_retained_parent(tmp_path, monkeypatch):
    from autoresearch.workspace import write_json

    root = make_workspace(tmp_path / "research")

    def scripted_measure(root, _program, _ledger, candidate):
        write_json(
            root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
        )
        score = (
            0.0
            if not candidate.features
            else (1.0 if candidate.features[0].kind == "momentum" else 0.5)
        )
        return {"score": score, "eligible": True, "train": {}, "stress": {}}

    monkeypatch.setattr("autoresearch.loop.measure", scripted_measure)
    monkeypatch.setattr("autoresearch.loop.selection_uncertainty", lambda *_args: {"passes": True})
    first = run_loop(root, iterations=1)
    assert first["experiments"][-1]["status"] == "keep"
    retained = first["incumbent"]
    # Simulate losing the metadata update after a durable keep record.
    with Ledger(root, 100000) as ledger:
        ledger.put("incumbent", "wrong")
    second = run_loop(
        root,
        proposal=Proposal(
            hypothesis="Test a weaker feature against the recovered retained parent.",
            features=(Feature(kind="volatility", lookback=21, threshold=0.25, direction="below"),),
        ),
    )
    assert second["experiments"][-1]["parent"] == retained
    assert second["experiments"][-1]["status"] == "discard"
    assert second["incumbent"] == retained
    bad = Proposal(
        hypothesis="Try changing a protected risk parameter.",
        patches=(Patch(path="costs.baseline_one_way_bps", value=0),),
    )
    third = run_loop(root, proposal=bad)
    assert third["status"] == "batch_incomplete"
    assert third["experiments"][-1]["status"] == "crash"
    assert third["incumbent"] == retained


def test_actual_subprocess_timeout_is_bounded(tmp_path, monkeypatch):
    import subprocess

    from autoresearch.loop import execute

    root = make_workspace(tmp_path / "research")
    program = load_program(root)
    seen = {}

    def timeout(command, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("autoresearch.loop.subprocess.run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        execute(root, program, program.baseline, root / "output")
    assert seen["timeout"] == program.timeout_seconds
    assert seen["env"] == worker_environment(root)
    assert seen["check"] is False


def test_validation_includes_first_return_and_excludes_training_activity(baseline, panel):
    from amat.backtest.runner import run_backtest
    from autoresearch.worker import validation_slice

    result = run_backtest(baseline.config.strategy, panel, baseline.config.strategy.to_universe())
    cut = result.sessions[50].session
    validation = validation_slice(result, cut)
    assert validation.sessions[0].session == cut
    assert validation.initial_equity == result.sessions[50].equity
    assert not validation.sessions[0].fills
    assert validation.sessions[0].attribution.explained_change == 0
    assert validation.sessions[1] == result.sessions[51]
    assert validation.total_fills == sum(len(row.fills) for row in result.sessions[51:])


def test_no_credentials_means_no_model_reservation(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    router = Router(Chain(targets=(Target(provider="openai", model="fixture"),)))
    request = ChatRequest(
        system="test", user="test", schema_json="{}", max_output_tokens=256, timeout_seconds=1
    )
    with Ledger(tmp_path, 10000) as ledger:
        assert asyncio.run(router.complete(request, ledger)) is None
        assert ledger.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_provider_cooldown_survives_a_new_router():
    chain = Chain(targets=(Target(provider="openai", model="fixture"),))
    first = Router(chain)
    first.health["openai"].record("rate_limit", retry_after_seconds=900)
    second = Router(chain)
    second.restore_cooldowns(first.cooldowns())
    assert second.health["openai"].should_skip()


def test_default_search_exercises_parameters_before_stall_limit(baseline):
    tried = {baseline.digest()}
    proposals = []
    for _ in range(12):
        proposal = search_proposal(baseline, tried)
        assert proposal is not None
        proposals.append(proposal)
        tried.add(apply_proposal(baseline, proposal).digest())
    assert any(proposal.patches for proposal in proposals)
    assert {feature.kind for proposal in proposals for feature in proposal.features} == {
        "momentum",
        "volatility",
        "drawdown",
        "volume_ratio",
        "efficiency",
    }


@pytest.mark.parametrize("gemini", [False, True])
def test_provider_schema_preserves_nested_feature_contract(gemini):
    from llmrouter.chain import wire_schema

    schema = wire_schema(Proposal.model_json_schema(), gemini=gemini)
    feature = schema["$defs"]["Feature"]
    assert "momentum" in feature["properties"]["kind"]["enum"]
    assert "default" not in feature["properties"]["smoothing"]
    assert feature["properties"]["lookback"]["maximum"] == 126
    if gemini:
        assert "additionalProperties" not in feature
        assert "maxItems" not in schema["properties"]["features"]
    else:
        assert set(feature["required"]) == set(feature["properties"])
        assert feature["additionalProperties"] is False
        assert schema["properties"]["features"]["maxItems"] == 4


def test_cached_artifact_changes_fail_closed(tmp_path):
    from autoresearch.loop import cached_measurement
    from autoresearch.workspace import digest_file

    artifact = tmp_path / "daily.json"
    artifact.write_text("original evidence")
    result = {"artifact_hashes": {artifact.name: digest_file(artifact)}, "score": 1.0}
    with Ledger(tmp_path, 10000) as ledger:
        ledger.db.execute("INSERT INTO cache VALUES(?,?)", ("candidate", json.dumps(result)))
        assert cached_measurement(tmp_path, ledger, "candidate") == result
        artifact.write_text("changed evidence")
        with pytest.raises(ValueError, match="artifact changed"):
            cached_measurement(tmp_path, ledger, "candidate")


def test_year_folds_include_the_first_session_return(tmp_path, baseline, panel):
    from amat.backtest.runner import run_backtest
    from autoresearch.objective import score_run

    root = make_workspace(tmp_path / "research")
    result = run_backtest(baseline.config.strategy, panel, baseline.config.strategy.to_universe())
    scored = score_run(result, result, load_program(root), 0)
    for fold in scored["folds"]:
        indices = [i for i, row in enumerate(result.sessions) if row.session.year == fold["year"]]
        start = max(0, indices[0] - 1)
        expected = float(result.sessions[indices[-1]].equity / result.sessions[start].equity - 1)
        assert fold["total_return"] == pytest.approx(expected)
        assert fold["initial_equity"] == float(result.sessions[start].equity)


def test_external_proposal_cannot_bypass_stall_budget_with_screen_label(tmp_path, monkeypatch):
    from autoresearch.workspace import write_json

    root = make_workspace(tmp_path / "research")

    def measurement(root, _program, _ledger, candidate):
        write_json(
            root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
        )
        return {"score": 0.0, "eligible": False, "train": {}, "stress": {}}

    monkeypatch.setattr("autoresearch.loop.measure", measurement)
    proposal = Proposal(
        hypothesis="Attempt to exempt an external proposal from the stall budget.",
        stage="screen",
        features=(Feature(kind="momentum", lookback=21, threshold=0),),
    )
    result = run_loop(root, proposal=proposal)
    assert json.loads(result["experiments"][-1]["proposal"])["stage"] == "manual"
    with Ledger(root, 100000) as ledger:
        assert ledger.get("stall") == 1


def test_exploratory_parent_cannot_replace_global_incumbent_by_beating_only_parent(
    tmp_path, monkeypatch
):
    from autoresearch.workspace import write_json

    root = make_workspace(tmp_path / "research")

    def measure(root, _program, _ledger, candidate):
        write_json(
            root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
        )
        score = (
            1.0
            if not candidate.features
            else (0.2 if candidate.features[0].kind == "momentum" else 0.5)
        )
        return {"score": score, "eligible": True, "train": {}, "stress": {}}

    monkeypatch.setattr("autoresearch.loop.measure", measure)
    monkeypatch.setattr("autoresearch.loop.selection_uncertainty", lambda *_args: {"passes": True})
    first = run_loop(
        root,
        proposal=Proposal(
            hypothesis="Explore a weaker momentum recipe.",
            features=(Feature(kind="momentum", lookback=21, threshold=0),),
        ),
    )
    exploratory = first["experiments"][-1]["candidate"]
    assert exploratory in first["frontier"]
    second = run_loop(
        root,
        proposal=Proposal(
            hypothesis="Improve the weak exploratory recipe.",
            parent_candidate=exploratory,
            features=(Feature(kind="volatility", lookback=21, threshold=0.25),),
        ),
    )
    trial = second["experiments"][-1]
    assert trial["status"] == "crash"
    assert "current retained best" in trial["error"]
    assert trial["result"] is None
    assert second["incumbent"] == first["incumbent"]


def test_batch_counts_five_distinct_successes_replaces_failures_and_escalates(
    tmp_path, monkeypatch
):
    from autoresearch.search import seed_proposals
    from autoresearch.workspace import write_json

    root = make_workspace(tmp_path / "research", max_experiments=20, stall_limit=20)
    program = load_program(root)
    seeds = seed_proposals()
    proposals = iter(
        [
            Proposal(hypothesis="Repeat baseline without changing its executable recipe."),
            seeds[0],
            *seeds[1:6],
        ]
    )
    monkeypatch.setattr(
        "autoresearch.loop.choose_experiment",
        lambda *_args: (program.baseline, next(proposals)),
    )
    scores = {
        apply_proposal(program.baseline, recipe).digest(): value
        for recipe, value in zip(seeds[1:6], [1.0, 0.5, 2.0, 1.5, 3.0], strict=True)
    }

    def measurement(root, _program, _ledger, candidate):
        if candidate.features:
            raise ValueError("fixture failed measurement")
        write_json(
            root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
        )
        return {
            "score": scores.get(candidate.digest(), 0.0),
            "eligible": True,
            "train": {},
            "stress": {},
            "failed_gates": [],
        }

    monkeypatch.setattr("autoresearch.loop.measure", measurement)
    monkeypatch.setattr("autoresearch.loop.selection_uncertainty", lambda *_args: {"passes": True})
    result = run_loop(root, iterations=5)
    assert result["status"] == "batch_completed"
    assert result["batch"] == {
        "requested": 5,
        "completed": 5,
        "experiment_ids": [4, 5, 6, 7, 8],
        "failed_attempts": 2,
        "escalated": 3,
    }
    completed = [row for row in result["experiments"] if row["status"] in ("keep", "discard")]
    assert len({row["candidate"] for row in completed}) == 5
    assert [row["status"] for row in completed] == ["keep", "discard", "keep", "discard", "keep"]
    assert result["incumbent"] == completed[-1]["candidate"]
    assert json.loads(completed[3]["result"])["selection_control"] == completed[2]["candidate"]
    assert "5 of 5 strategies backtested" in (root / "report.html").read_text()


def test_partial_batch_is_not_reported_as_complete(tmp_path, monkeypatch):
    root = make_workspace(tmp_path / "research")
    from autoresearch.workspace import write_json

    def measurement(root, _program, _ledger, candidate):
        write_json(
            root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
        )
        return {"score": 0.0, "eligible": True, "train": {}, "stress": {}}

    monkeypatch.setattr("autoresearch.loop.measure", measurement)
    monkeypatch.setattr("autoresearch.loop.choose_experiment", lambda *_args: None)
    result = run_loop(root, iterations=5)
    assert result["status"] == "search_exhausted"
    assert result["batch"]["completed"] == 0
    assert result["batch"]["requested"] == 5
    assert "0 of 5 strategies backtested" in (root / "report.html").read_text()


def test_unavailable_model_falls_back_to_disclosed_distinct_search(tmp_path, monkeypatch):
    from autoresearch.workspace import write_json

    root = make_workspace(tmp_path / "research")

    def measurement(root, _program, _ledger, candidate):
        write_json(
            root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
        )
        return {"score": 0.0, "eligible": False, "train": {}, "stress": {}}

    monkeypatch.setattr("autoresearch.loop.measure", measurement)
    monkeypatch.setattr("autoresearch.loop.next_proposal", lambda *_args, **_kwargs: None)
    messages = []
    result = run_loop(
        root,
        iterations=1,
        chain=Chain(targets=(Target(provider="gemini", model="fixture"),)),
        fallback_search=True,
        on_progress=messages.append,
    )
    assert result["batch"]["completed"] == 1
    assert result["status"] == "batch_completed"
    assert json.loads(result["experiments"][-1]["result"])["proposal_source"] == "built-in"
    assert any("continuing with built-in" in message for message in messages)


def test_repeated_duplicate_proposals_stop_at_bounded_attempt_limit(tmp_path, monkeypatch):
    from autoresearch.workspace import write_json

    root = make_workspace(tmp_path / "research")
    program = load_program(root)

    def measurement(root, _program, _ledger, candidate):
        write_json(
            root / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
        )
        return {"score": 0.0, "eligible": True, "train": {}, "stress": {}}

    monkeypatch.setattr("autoresearch.loop.measure", measurement)
    monkeypatch.setattr(
        "autoresearch.loop.choose_experiment",
        lambda *_args: (
            program.baseline,
            Proposal(hypothesis="Repeat the baseline without a real strategy change."),
        ),
    )
    result = run_loop(root, iterations=1)
    assert result["status"] == "batch_incomplete"
    assert result["batch"]["completed"] == 0
    assert result["batch"]["failed_attempts"] == 3
    assert len(result["experiments"]) == 4


def test_baseline_failure_leaves_durable_failed_status_and_report(tmp_path, monkeypatch):
    root = make_workspace(tmp_path / "research")

    def fail(*_args):
        raise ValueError("fixture baseline failure")

    monkeypatch.setattr("autoresearch.loop.measure", fail)
    with pytest.raises(ValueError, match="fixture baseline"):
        run_loop(root, iterations=5)
    report = json.loads((root / "report.json").read_text())
    assert report["status"] == "baseline_failed"
    assert report["batch"]["completed"] == 0
    assert report["experiments"][0]["status"] == "crash"
    assert "fixture baseline failure" in (root / "report.html").read_text()
