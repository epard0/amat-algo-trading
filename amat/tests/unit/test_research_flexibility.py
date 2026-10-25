"""Diverse executable hypotheses and evidence-grounded failure retention."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

from amat.config.v6 import load_v6_config
from autoresearch.contracts import Candidate, apply_proposal, validate_candidate
from autoresearch.exploration import exploratory_proposals
from autoresearch.failures import collect_failures, diagnose, export_failures, failure_memory
from autoresearch.signals import design
from autoresearch.specs import FeatureSpec, Interaction, SignalSpec, SignalTerm
from autoresearch.summaries import evidence
from autoresearch.variants import ablations
from autoresearch.workspace import digest_file, initialize, load_program, write_json

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def base():
    return Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)


def test_exploration_offers_over_100_valid_distinct_recipes(base):
    candidates = []
    for proposal in exploratory_proposals():
        try:
            candidate = apply_proposal(base, proposal)
        except ValueError as exc:
            assert "warm-up" in str(exc)
            continue
        validate_candidate(base, candidate)
        candidates.append(candidate)
    assert len({c.digest() for c in candidates}) >= 100
    assert len({t.feature.kind for c in candidates for t in c.signal.terms}) == 18
    assert any(c.signal.terms[0].feature.lookback > 126 for c in candidates)
    assert any(c.signal.policy.min_hold_sessions == 63 for c in candidates)
    assert {i.operation for c in candidates for i in c.signal.interactions} == {
        "product",
        "difference",
        "minimum",
        "maximum",
        "gate",
    }


@pytest.mark.parametrize("operation", ["minimum", "maximum", "gate"])
def test_new_interactions_are_causal_preserve_missingness_and_support_ablation(base, operation):
    signal = SignalSpec(
        terms=(
            SignalTerm(feature=FeatureSpec(kind="momentum"), weight=0),
            SignalTerm(feature=FeatureSpec(kind="volume_ratio"), weight=0),
        ),
        interactions=(Interaction(left=0, right=1, operation=operation, weight=1),),
    )
    values = np.array([[[1.0, -1.0]], [[0.0, 2.0]], [[np.nan, 1.0]]])
    transformed = np.tanh(values)
    a, b = transformed[:, :, 0], transformed[:, :, 1]
    expected = {"minimum": np.minimum(a, b), "maximum": np.maximum(a, b), "gate": a * (b + 1) / 2}[
        operation
    ]
    np.testing.assert_allclose(design(values, signal)[:, :, -1], expected, equal_nan=True)
    np.testing.assert_allclose(design(values[:2], signal), design(values, signal)[:2])
    candidate = Candidate(config=base.config, signal=signal)
    variants = dict(ablations(candidate))
    assert variants["interaction_0"].signal is None
    assert variants["signal_0"].signal is None
    assert variants["signal_1"].signal is None


def failed_row(status="discard"):
    return {
        "id": 2,
        "candidate": "a" * 64,
        "parent": "b" * 64,
        "status": status,
        "proposal": json.dumps({"hypothesis": "Test <script>untrusted</script> signal"}),
        "error": "feature history exceeds the fixed warm-up budget" if status == "crash" else None,
        "result": None
        if status == "crash"
        else json.dumps(
            {
                "score": 0.4,
                "selection_control_score": 0.6,
                "required_improvement": 0.05,
                "train": {"sharpe": 0.6, "max_drawdown": -0.1},
                "stress": {"sharpe": -0.1},
                "failed_gates": ["stress_sharpe"],
                "uncertainty": {"passes": False, "reason": "eligibility_or_objective_margin"},
                "folds": [{"year": 2022, "total_return": -0.03}],
            }
        ),
    }


def test_failed_strategy_separates_observed_decision_from_untested_mechanism():
    row = failed_row()
    record = diagnose(row)
    views = {p["standpoint"]: p for p in record["perspectives"]}
    assert len(views) == 7
    assert "0.400000" in views["objective"]["finding"]
    assert "0.600000 + 0.050000" in views["objective"]["finding"]
    assert "0.700000" in views["execution"]["finding"]
    assert views["market_conditions"]["evidence"]["negative_years"] == [2022]
    assert "skipped" in views["uncertainty"]["finding"]
    assert "remains untested" in views["signal"]["finding"]
    assert evidence(row)["failure_analysis"] == record
    assert failure_memory([row])["reason_counts"] == {"stress_sharpe": 1}


def test_runtime_failure_has_no_invented_performance():
    record = diagnose(failed_row("crash"))
    views = {p["standpoint"]: p for p in record["perspectives"]}
    assert "warm-up" in views["implementation"]["finding"]
    assert views["objective"]["evidence"]["score"] is None
    assert "not measured" in views["execution"]["finding"]
    assert "No uncertainty" in views["uncertainty"]["finding"]


def test_failure_library_preserves_attempts_provenance_and_escapes_prose(tmp_path):
    root = tmp_path / "session"
    report = {
        "program_sha256": "frozen",
        "experiments": [failed_row(), {**failed_row("crash"), "id": 3}],
        "llm_summaries": [],
    }
    records = export_failures(root, report)
    assert [r["status"] for r in records] == ["discard", "crash"]
    assert all(r["session"] == str(root) for r in records)
    assert "<script>" not in (root / "analysis/failures.html").read_text()
    assert "&lt;script&gt;" in (root / "analysis/failures.html").read_text()
    write_json(
        root / "analysis/manifest.json",
        {"exports": {"failures.json": digest_file(root / "analysis/failures.json")}},
    )
    library = collect_failures(tmp_path)
    assert library["records"] == 2
    assert library["skipped_sessions"] == []
    (root / "analysis/failures.json").write_text("[]")
    library = collect_failures(tmp_path)
    assert library["records"] == 0
    assert "hash mismatch" in library["skipped_sessions"][0]["reason"]


def test_research_brief_is_frozen_and_tampering_is_detected(tmp_path):
    root = tmp_path / "session"
    initialize(
        root,
        ROOT,
        ROOT / "configs/strategy/amat_v6.toml",
        ROOT / "data/snapshots/alpaca-sip-2026-08-11",
        name="brief-test",
        train_end=dt.date(2022, 12, 30),
        validation_end=dt.date(2026, 8, 10),
        brief="Explore simple long-horizon participation signals.",
    )
    assert "runtime/research-brief.txt" in load_program(root).frozen_files
    (root / "runtime/research-brief.txt").write_text("changed")
    with pytest.raises(ValueError, match="frozen input changed"):
        load_program(root)


@pytest.mark.parametrize("verdict", ["bad", "inconclusive"])
def test_llm_runtime_review_requires_inconclusive_performance(tmp_path, monkeypatch, verdict):
    from autoresearch.ledger import Ledger
    from autoresearch.loop import finish, register
    from autoresearch.summaries import RunSummary, summarize_experiment
    from llmrouter.chain import Chain, Router, Target
    from llmrouter.contracts import ChatResponse

    class Transport:
        async def generate(self, request, *, model, api_key, base_url):
            data = json.loads(request.user)
            assert data["decision"] == "crash"
            assert len(data["failure_analysis"]["perspectives"]) == 7
            return ChatResponse(
                provider="openai",
                model=model,
                input_tokens=100,
                output_tokens=100,
                text=RunSummary(
                    verdict=verdict,
                    headline="No valid strategy measurement",
                    explanation=(
                        "The feature history contract failed before producing "
                        "valid performance measurements."
                    ),
                    strengths=(),
                    weaknesses=("Feature window exceeded warm-up.",),
                    next_experiment="Reduce the feature history to fit the frozen warm-up budget.",
                    evidence_limits="No performance measured; economic edge remains unknown.",
                ).model_dump_json(),
            )

    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    with Ledger(tmp_path, 100000) as ledger:
        index = register(ledger, "candidate", "parent", failed_row()["proposal"])
        finish(ledger, index, "crash", error="feature history exceeds warm-up")
        router = Router(
            Chain(targets=(Target(provider="openai", model="fixture"),)),
            transports={"openai": Transport()},
        )
        outcome = summarize_experiment(tmp_path, ledger, index, router)
        assert outcome["status"] == ("complete" if verdict == "inconclusive" else "unavailable")
        assert ledger.history()[0]["status"] == "crash"


def test_cross_session_library_reads_legacy_ledgers_without_upgrading_them(tmp_path):
    from autoresearch.ledger import Ledger
    from autoresearch.loop import finish, register

    root = tmp_path / "legacy"
    root.mkdir()
    program = {"schema_version": "strategy-development/1", "old_contract": True}
    write_json(root / "program.json", program)
    (root / "program.sha256").write_text(digest_file(root / "program.json"))
    with Ledger(root, 100000) as ledger:
        index = register(ledger, "old-candidate", "old-parent", failed_row()["proposal"])
        finish(ledger, index, "discard", json.loads(failed_row()["result"]))
    before = (root / "research.sqlite3").read_bytes()
    library = collect_failures(tmp_path)
    assert library["records"] == 1
    assert library["skipped_sessions"] == []
    assert (root / "research.sqlite3").read_bytes() == before
    records = json.loads((tmp_path / "failure-library/failures.json").read_text())
    assert records[0]["program_schema"] == "strategy-development/1"
    assert records[0]["measurement_hashes_available"] is False
    (root / "program.json").write_text("{}")
    assert collect_failures(tmp_path)["skipped_sessions"]
