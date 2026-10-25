"""The search objective and the exposure-matched eligibility evidence must agree."""

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from pathlib import Path

import pytest

from amat.config.v6 import load_v6_config
from autoresearch.contracts import Candidate, Program
from autoresearch.objective import apply_incremental_score, score_description


@pytest.fixture
def program():
    config = Path(__file__).resolve().parents[2] / "configs/strategy/amat_v6.toml"
    return Program(
        name="incremental-test",
        baseline=Candidate(config=load_v6_config(config).config),
        load_start=dt.date(2016, 1, 1),
        measured_start=dt.date(2018, 1, 2),
        train_end=dt.date(2022, 12, 30),
        validation_end=dt.date(2023, 12, 29),
        frozen_files={},
        dependencies={},
    )


def controls(delta):
    return {
        "exposure_match": {
            "matched": True,
            "sharpe_difference": delta,
            "exposure_error": 0.001,
            "candidate": {"sharpe": 0.7 + delta, "average_gross_exposure": 0.501},
            "control": {"sharpe": 0.7, "average_gross_exposure": 0.5},
            "scaled": "candidate",
            "scalar": 0.8,
            "annualized_return_difference": 0.001,
        }
    }


def objective(legacy=9):
    return {"score": legacy, "failed_gates": [], "eligible": True}


def test_high_absolute_score_cannot_hide_negative_incremental_value(program):
    loser, winner = objective(9), objective(-9)
    apply_incremental_score(loser, controls(-0.08), program, 2, 0)
    apply_incremental_score(winner, controls(0.08), program, 2, 0)
    assert loser["score"] == pytest.approx(-0.10)
    assert winner["score"] == pytest.approx(0.06)
    assert loser["absolute_performance_score"] == 9
    assert winner["score"] > program.min_improvement
    assert loser["score_components"]["reference_candidate"] == program.baseline.digest()


def test_baseline_zero_and_added_complexity_without_simplicity_bonus(program):
    result = objective()
    apply_incremental_score(result, {"baseline": True}, program, 3, 3)
    assert result["score"] == 0
    for candidate_size, expected in [(2, 0.1), (3, 0.1), (5, 0.08)]:
        result = objective()
        apply_incremental_score(result, controls(0.1), program, candidate_size, 3)
        assert result["score"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "unmatched",
        "nan",
        "infinite",
        "exposure",
        "delta",
        "error",
        "zero",
        "null",
    ],
)
def test_invalid_match_has_no_score_and_cannot_fall_back_to_absolute(program, damage):
    report = controls(0.1)
    match = report["exposure_match"]
    if damage == "missing":
        report = {}
    elif damage == "unmatched":
        match["matched"] = False
    elif damage == "nan":
        match["candidate"]["sharpe"] = float("nan")
    elif damage == "infinite":
        match["sharpe_difference"] = float("inf")
    elif damage == "exposure":
        match["candidate"]["average_gross_exposure"] = 0.8
    elif damage == "delta":
        match["sharpe_difference"] = 1
    elif damage == "error":
        match["exposure_error"] = -1
    elif damage == "zero":
        match["candidate"]["average_gross_exposure"] = 0
    elif damage == "null":
        report["exposure_match"] = None
    result = objective()
    apply_incremental_score(result, report, program, 0, 0)
    assert result["score"] is None
    assert result["eligible"] is False
    assert "incremental_score_unavailable" in result["failed_gates"]
    assert result["absolute_performance_score"] == 9


def test_scoring_does_not_remove_risk_gates_or_mutate_control_evidence(program):
    result, report = objective(), controls(0.2)
    result.update(failed_gates=["drawdown"], eligible=False)
    before = deepcopy(report)
    apply_incremental_score(result, report, program, 1, 0)
    assert result["score"] == pytest.approx(0.19)
    assert not result["eligible"]
    assert result["failed_gates"] == ["drawdown"]
    assert report == before


def test_old_program_canonical_payload_and_objective_remain_unchanged(program):
    payload = program.model_dump(mode="json")
    payload["schema_version"] = "strategy-development/5"
    old = Program.model_validate(payload)
    assert old.model_dump(mode="json") == payload
    assert old.digest() == Program.model_validate_json(old.model_dump_json()).digest()
    result = objective()
    before = deepcopy(result)
    apply_incremental_score(result, controls(-0.1), old, 4, 0)
    assert result == before
    assert "Yearly" in score_description(old)
    assert "Exposure-matched" in score_description(program)


def test_historical_comparison_preserves_source_and_handles_unrankable_evidence(tmp_path, program):
    import json
    import runpy

    from autoresearch.contracts import Feature
    from autoresearch.workspace import digest_file, write_json

    compare = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/compare_research_scores.py")
    )["compare"]
    root, output = tmp_path / "history", tmp_path / "comparison"
    root.mkdir()
    old = program.model_copy(update={"schema_version": "strategy-development/5"})
    write_json(root / "program.json", old.model_dump(mode="json"))
    (root / "program.sha256").write_text(digest_file(root / "program.json"))
    (root / "research.sqlite3").write_bytes(b"original ledger bytes")
    rows = []
    for index, lookback in enumerate((0, 21, 63), 1):
        candidate = Candidate(
            config=old.baseline.config,
            features=(Feature(kind="momentum", lookback=lookback, threshold=0),)
            if lookback
            else (),
        )
        write_json(
            root / "candidates" / (candidate.digest() + ".json"), candidate.model_dump(mode="json")
        )
        report = {"baseline": True} if not lookback else controls(0.1)
        if lookback == 63:
            report["exposure_match"]["matched"] = False
        result = {
            **objective(),
            "control_summary": report,
            "train": {"sharpe": 0.8, "annualized_return": 0.05},
        }
        relative = f"outputs/{index}.json"
        write_json(root / relative, result)
        result["artifact_hashes"] = {relative: digest_file(root / relative)}
        rows.append(
            {
                "id": index,
                "candidate": candidate.digest(),
                "status": "baseline" if not lookback else "discard",
                "result": json.dumps(result),
            }
        )
    write_json(root / "report.json", {"program_sha256": old.digest(), "experiments": rows})
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    compare(root, output)
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after
    summary = json.loads((output / "comparison.json").read_text())
    assert summary["candidates"] == 2
    assert summary["unavailable_scores"] == 1
    assert summary["new_top"]["new_score"] == pytest.approx(0.09)
    assert "unavailable" in (output / "report.md").read_text()
    with pytest.raises(ValueError, match="outside"):
        compare(root, root / "forbidden")
    (root / "outputs/2.json").write_text("tampered")
    with pytest.raises(ValueError, match="evidence changed"):
        compare(root, tmp_path / "tampered-comparison")


def test_failure_and_model_evidence_identify_the_score_formula(program):
    import json

    from autoresearch.failures import diagnose
    from autoresearch.proposer import training_evidence
    from autoresearch.summaries import evidence

    result = objective()
    apply_incremental_score(result, controls(-0.08), program, 2, 0)
    result.update(selection_control_score=0, required_improvement=0.05)
    row = {
        "id": 2,
        "candidate": "candidate",
        "parent": "baseline",
        "status": "discard",
        "proposal": json.dumps({"hypothesis": "Test incremental evidence"}),
        "result": json.dumps(result),
    }
    review = diagnose(row)
    objective_evidence = next(
        p["evidence"] for p in review["perspectives"] if p["standpoint"] == "objective"
    )
    for payload in (objective_evidence, training_evidence(row), evidence(row)):
        assert payload["score_version"] == "exposure-matched-incremental/1"
        assert payload["score_components"]["matched_sharpe_difference"] == -0.08
        assert payload["absolute_performance_score"] == 9
