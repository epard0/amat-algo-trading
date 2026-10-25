"""Fixture evaluations for validation, graph, privacy and archive boundaries."""

import datetime as dt
import hashlib
import json
import sqlite3

import pytest

from autoresearch.best_strategies import record_best
from autoresearch.graph import run_graph
from autoresearch.validation_contract import (
    StatisticalProtocol,
    calendar_check,
    causal_check,
    matched_periods,
)
from autoresearch.web_evidence import admit, persist_admitted
from llmrouter.costs import CostLedger


def test_graph_orders_and_failed_prepare_cannot_run():
    events = []
    result = run_graph(
        lambda: events.append("prepare"),
        lambda: events.append("run") or {"ok": True},
        lambda _: events.append("verify"),
    )
    assert events == ["prepare", "run", "verify"]
    assert result == {"ok": True}

    def fail():
        raise ValueError("invalid protocol")

    with pytest.raises(ValueError, match="invalid protocol"):
        run_graph(fail, lambda: pytest.fail("must not execute"), lambda _: None)


def test_calendar_and_causal_violations():
    with pytest.raises(ValueError, match="sessions"):
        calendar_check(["2025-01-02"], ["2025-01-02", "2025-01-03"])
    with pytest.raises(ValueError, match="information"):
        causal_check(
            [
                {
                    "available_at": "2025-01-02T17:00:00Z",
                    "decision_at": "2025-01-02T16:00:00Z",
                    "execution_at": "2025-01-03T14:30:00Z",
                }
            ]
        )
    daily = [
        {"session": "2025-01-02", "equity": 100, "return": 0},
        {"session": "2025-01-03", "equity": 101, "return": 0.01},
    ]
    paired = matched_periods(daily, daily, [row["session"] for row in daily])
    assert all(row["return_difference"] == 0 for row in paired)


def test_protocol_is_finite_frozen_and_diagnostic(tmp_path):
    protocol = StatisticalProtocol(bootstrap_samples=100)
    protocol.freeze(tmp_path / "protocol.json")
    with pytest.raises(FileExistsError):
        protocol.freeze(tmp_path / "protocol.json")
    with pytest.raises(ValueError, match="exhausted"):
        protocol.compare([0.01] * 252, [0.0] * 252, trial=31)
    result = protocol.compare([0.0] * 252, [0.0] * 252, trial=1)
    assert result["diagnostic_pass"] is False
    assert result["promotion_authority"] == "none"


def test_only_admitted_excerpt_persists(tmp_path):
    now = dt.datetime.now(dt.UTC)
    raw = "DO_NOT_PERSIST_RAW_CANARY source says volume increased"
    row = admit(
        raw,
        url="https://example.com/source",
        excerpt="volume increased",
        claim="volume increased",
        fetched_at=now,
        now=now,
    )
    persist_admitted(tmp_path / "claims.json", [row])
    assert b"DO_NOT_PERSIST_RAW_CANARY" not in (tmp_path / "claims.json").read_bytes()
    assert row["truth_verified"] is False
    with pytest.raises(ValueError, match="excerpt"):
        admit(
            raw, url="https://example.com", excerpt="invented", claim="x", fetched_at=now, now=now
        )
    with pytest.raises(ValueError, match="projection"):
        persist_admitted(tmp_path / "bad.json", [{**row, "raw": raw}])


def test_costs_unknown_and_pending_are_preserved(tmp_path):
    ledger = CostLedger(tmp_path / "costs.sqlite3")
    identity = ledger.reserve(category="model", provider="groq", model="model")
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT status FROM attempts").fetchone()[0] == "pending"
    ledger.settle(identity, status="timeout", usage={"input_tokens": None, "usd": None})
    with pytest.raises(ValueError, match="settled"):
        ledger.settle(identity, status="ok", usage={})
    with pytest.raises(ValueError, match="metadata"):
        ledger.settle(identity, status="ok", usage={"raw": "canary"})


def test_best_return_and_score_survive_worse_candidates(tmp_path):
    root = tmp_path / "session"
    (root / "candidates").mkdir(parents=True)
    for number, annual, score, eligible in [
        (1, 0.14, 0.1, True),
        (2, 0.2, -0.1, False),
        (3, 0.05, 0.2, True),
    ]:
        candidate = {"fixture": number}
        identity = hashlib.sha256(
            json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        (root / "candidates" / f"{identity}.json").write_text(json.dumps(candidate))
        record_best(
            root,
            program={},
            candidate=identity,
            result={"train": {"annualized_return": annual}, "score": score, "eligible": eligible},
            decision="keep" if eligible else "discard",
        )
    summary = json.loads((root / "best-strategies.json").read_text())
    assert summary["recorded_candidates"] == 3
    assert summary["best_return"][1] == 0.2
    assert summary["best_eligible_score"][2] == 0.2
    with sqlite3.connect(tmp_path / "best-strategies.sqlite3") as db:
        assert (
            db.execute("SELECT COUNT(*) FROM strategies WHERE annual_return=.14").fetchone()[0] == 1
        )


def test_v13_baseline_roundtrips_without_changing_allocator():
    from pathlib import Path

    from autoresearch.baseline import load_research_config
    from autoresearch.contracts import (
        Candidate,
        Patch,
        Proposal,
        apply_proposal,
        validate_candidate,
    )

    path = Path(__file__).resolve().parents[2] / "configs/strategy/amat_v13_return_target.toml"
    baseline = Candidate(config=load_research_config(path))
    assert Candidate.model_validate_json(baseline.model_dump_json()) == baseline
    assert baseline.config.strategy.cross_sectional.enabled
    candidate = apply_proposal(
        baseline,
        Proposal(
            hypothesis="Test a smaller rebalance band",
            patches=(Patch(path="rebalance.min_weight_delta", value=0.015),),
        ),
    )
    validate_candidate(baseline, candidate)
    assert candidate.config.strategy.cross_sectional == baseline.config.strategy.cross_sectional
    assert candidate.config.strategy.constraints == baseline.config.strategy.constraints


def test_v13_fallback_supplies_100_distinct_compatible_candidates():
    from pathlib import Path

    from autoresearch.baseline import load_research_config
    from autoresearch.contracts import Candidate, apply_proposal, validate_candidate
    from autoresearch.proposer import v13_proposal

    source = Path(__file__).resolve().parents[2]
    baseline = Candidate(
        config=load_research_config(source / "configs/strategy/amat_v13_return_target.toml")
    )
    tried = {baseline.digest()}
    for _ in range(100):
        proposal = v13_proposal(baseline, tried)
        assert proposal is not None
        candidate = apply_proposal(baseline, proposal)
        validate_candidate(baseline, candidate)
        assert candidate.digest() not in tried
        assert candidate.config.strategy.cross_sectional == baseline.config.strategy.cross_sectional
        tried.add(candidate.digest())


def test_explicit_v13_reference_selection_is_verified_and_idempotent(tmp_path):
    from pathlib import Path

    from autoresearch.promotion import active_promotion, select_v13_reference

    source = Path(__file__).resolve().parents[2]
    selected = select_v13_reference(source, tmp_path)
    pointer = (tmp_path / "active-strategy.json").read_bytes()
    assert selected["candidate"]["config"]["strategy"]["version"] == "13.0.0"
    assert selected["execution_authority"] == "none"
    assert active_promotion(tmp_path) == selected
    assert select_v13_reference(source, tmp_path) == selected
    assert (tmp_path / "active-strategy.json").read_bytes() == pointer


@pytest.mark.parametrize("excerpt,claim", [("passage", "unsupported"), ("full page", "full page")])
def test_evidence_rejects_paraphrase_and_full_page(excerpt, claim):
    now = dt.datetime.now(dt.UTC)
    with pytest.raises(ValueError, match="admission"):
        admit(
            "full page" if excerpt == "full page" else "source passage here",
            url="https://example.com",
            excerpt=excerpt,
            claim=claim,
            fetched_at=now,
            now=now,
        )
