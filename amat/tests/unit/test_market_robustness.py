"""Market worlds preserve joint inputs and rerun the actual strategy machinery."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from amat.validation.montecarlo import bootstrap_panel
from autoresearch.contracts import Candidate, Program, apply_proposal
from autoresearch.experiments import Simulation
from autoresearch.inspirations import router_proposals
from autoresearch.market_scenarios import (
    SCENARIOS,
    panel_digest,
    scenario_metadata,
    scenario_moves,
    scenario_panel,
)
from autoresearch.robustness import EXECUTION_STRESSES, evaluate_robustness, historical_conditions
from autoresearch.specs import EvaluationSpec

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def base():
    return Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)


@pytest.fixture(scope="module")
def panel(base):
    return replace(
        generate_panel(base.config.strategy.to_universe(), sessions=850), source="scenario(fixture)"
    )


@pytest.fixture(scope="module")
def program(base, panel):
    return Program(
        name="scenario-test",
        load_start=panel.sessions[0],
        measured_start=panel.sessions[503],
        train_end=panel.sessions[-1],
        validation_end=panel.sessions[-1] + dt.timedelta(days=1),
        baseline=base,
        frozen_files={},
        dependencies={},
        evaluation=EvaluationSpec(robustness="extended", scenario_bootstrap_paths=1),
    )


@pytest.mark.parametrize("kind", SCENARIOS)
def test_worlds_are_reproducible_and_do_not_calibrate_on_future(panel, kind):
    world = scenario_panel(panel, first=503, kind=kind, seed=40)
    changed = panel.adjusted_closes.copy()
    changed[503:] *= 100
    future = scenario_panel(replace(panel, adjusted_closes=changed), first=503, kind=kind, seed=40)
    assert panel_digest(world) == panel_digest(future)
    np.testing.assert_array_equal(world.raw_closes, world.adjusted_closes)
    assert np.isfinite(world.adjusted_closes).all()
    assert np.all(world.adjusted_closes > 0)
    assert np.all(world.adjusted_highs[503:] >= world.window_vwaps[503:])
    assert np.all(world.adjusted_lows[503:] <= world.window_vwaps[503:])
    assert np.all(world.session_volumes[503:] >= world.window_volumes[503:])
    assert world.sessions == panel.sessions
    assert world.source.startswith("scenario(")
    assert not np.array_equal(world.adjusted_closes[503:], panel.adjusted_closes[503:])


@pytest.mark.parametrize("kind", SCENARIOS)
def test_all_scenario_inputs_ignore_future_source_data_and_preserve_prefix(panel, kind):
    fields = (
        "adjusted_closes",
        "raw_closes",
        "window_vwaps",
        "window_volumes",
        "adjusted_highs",
        "adjusted_lows",
        "session_volumes",
    )
    corrupt = {name: getattr(panel, name).copy() for name in fields}
    for values in corrupt.values():
        values[503:] = np.nan
    world = scenario_panel(panel, first=503, kind=kind, seed=40)
    future = scenario_panel(replace(panel, **corrupt), first=503, kind=kind, seed=40)
    assert panel_digest(world) == panel_digest(future)
    short = replace(
        panel,
        sessions=panel.sessions[:700],
        **{name: getattr(panel, name)[:700] for name in fields},
    )
    prefix = scenario_panel(short, first=503, kind=kind, seed=40)
    for name in fields:
        np.testing.assert_array_equal(getattr(world, name)[:700], getattr(prefix, name))
    np.testing.assert_array_equal(world.adjusted_closes[:503], panel.adjusted_closes[:503])


def test_gap_liquidity_and_flat_scenarios_have_the_declared_market_mechanics(panel):
    for name, sign in (("gap_down", -1), ("gap_up", 1)):
        moves, noise = scenario_moves(np.array([0.01]), count=150, kind=name, seed=10)
        assert moves[63, 0] - noise[63, 0] == pytest.approx(sign * 0.25)
        world = scenario_panel(panel, first=503, kind=name, seed=10)
        gaps = np.log(world.window_vwaps[566] / world.raw_closes[565])
        assert np.all(sign * gaps > 0.20)
    crisis = scenario_panel(panel, first=503, kind="liquidity_crisis", seed=10)
    assert np.all(crisis.window_volumes[566:571] == 0)
    assert np.all(crisis.window_volumes[571:586] > 0)
    assert np.max(crisis.session_volumes[566:586]) < np.min(crisis.session_volumes[565]) * 0.1
    flat = scenario_panel(panel, first=503, kind="flat", seed=10)
    np.testing.assert_array_equal(np.diff(flat.adjusted_closes[503:], axis=0), 0)
    np.testing.assert_array_equal(flat.adjusted_closes[503:], flat.window_vwaps[503:])


def test_short_windows_cannot_claim_they_exercised_future_events():
    for kind in ("crash_recovery", "correlation_break", "liquidity_crisis", "gap_down"):
        assert not scenario_metadata(kind, count=30)["coverage_complete"]
        assert scenario_metadata(kind, count=150)["coverage_complete"]
    with pytest.raises(ValueError, match="unknown scenario"):
        scenario_moves(np.array([0.01]), count=100, kind="typo", seed=1)
    with pytest.raises(ValueError, match="sigma"):
        scenario_moves(np.array([np.nan]), count=100, kind="bull", seed=1)


def test_checkpoint_is_rejected_before_any_strategy_simulation(panel, program, base, monkeypatch):
    invalid = program.model_copy(update={"train_end": panel.sessions[-2]})
    simulation = Simulation(panel, invalid, None)
    monkeypatch.setattr(simulation, "run", lambda *_: pytest.fail("checkpoint was consumed"))
    with pytest.raises(ValueError, match="checkpoint"):
        evaluate_robustness(simulation, base)


def test_multiple_seeds_produce_distinct_paired_worlds(panel, program, base, monkeypatch):
    monkeypatch.setattr("autoresearch.robustness.SCENARIOS", ("student_t_5",))
    monkeypatch.setattr("autoresearch.robustness.EXECUTION_STRESSES", ())
    repeated = program.model_copy(
        update={"evaluation": program.evaluation.model_copy(update={"scenario_synthetic_paths": 2})}
    )
    study = evaluate_robustness(Simulation(panel, repeated, None), base)
    cases = study.report["scenarios"]
    assert study.report["coverage"]["expected_cases"] == 3
    assert study.report["coverage"]["complete"]
    assert cases["student_t_5"]["panel_sha256"] != cases["student_t_5_path_1"]["panel_sha256"]
    assert cases["student_t_5_path_1"]["seed"] == cases["student_t_5"]["seed"] + 100000
    for row in cases.values():
        assert row["candidate"] == row["baseline"]
        assert row["return_difference"] == 0


def test_incomplete_scheduled_stress_fails_the_coverage_gate(panel, program, base, monkeypatch):
    monkeypatch.setattr("autoresearch.robustness.SCENARIOS", ("correlation_break",))
    monkeypatch.setattr("autoresearch.robustness.EXECUTION_STRESSES", ())
    fields = (
        "adjusted_closes",
        "raw_closes",
        "window_vwaps",
        "window_volumes",
        "adjusted_highs",
        "adjusted_lows",
        "session_volumes",
    )
    short = replace(
        panel,
        sessions=panel.sessions[:600],
        **{name: getattr(panel, name)[:600] for name in fields},
    )
    study = evaluate_robustness(Simulation(short, program, None), base)
    assert not study.report["coverage"]["complete"]
    assert not study.report["integrity_passed"]
    assert study.report["coverage"]["incomplete_scenarios"] == ["correlation_break"]


@pytest.mark.parametrize("count", [0, 9])
def test_synthetic_repetitions_are_bounded(count):
    with pytest.raises(ValueError, match="scenario_synthetic_paths"):
        EvaluationSpec(scenario_synthetic_paths=count)


def test_cli_exposes_repetitions_for_both_entry_points(tmp_path):
    from autoresearch.cli import argument_parser

    for args in (["start", "1"], ["init", str(tmp_path / "scenario-example")]):
        parsed = argument_parser().parse_args([*args, "--scenario-synthetic-paths", "3"])
        assert parsed.scenario_synthetic_paths == 3


def test_overflowing_tails_are_reported_instead_of_clipped(panel, monkeypatch):
    def extreme(sigma, *, count, **_kwargs):
        return np.full((count, len(sigma)), 1000.0), np.zeros((count, len(sigma)))

    monkeypatch.setattr("autoresearch.market_scenarios.scenario_moves", extreme)
    with pytest.raises(ValueError, match="finite positive price range"):
        scenario_panel(panel, first=503, kind="student_t_3", seed=4)


def test_bootstrap_context_matches_each_resampled_joint_price_row(panel):
    world = bootstrap_panel(panel, seed=8)
    source_returns = panel.adjusted_closes[1:] / panel.adjusted_closes[:-1]
    for index in range(1, len(world)):
        relative = world.adjusted_closes[index] / world.adjusted_closes[index - 1]
        row = int(np.argmin(np.abs(source_returns - relative).max(axis=1))) + 1
        np.testing.assert_allclose(world.session_volumes[index], panel.session_volumes[row])
        np.testing.assert_allclose(world.window_volumes[index], panel.window_volumes[row])
        np.testing.assert_allclose(
            world.adjusted_highs[index] / world.adjusted_closes[index],
            panel.adjusted_highs[row] / panel.adjusted_closes[row],
        )


def test_real_world_cannot_silently_drop_actions(panel, program):
    with pytest.raises(ValueError, match="corporate actions"):
        Simulation(replace(panel, source="real"), program, None)


def test_regime_attribution_counts_prior_session_outcomes(panel, program, base):
    simulation = Simulation(panel, program, None)
    result = historical_conditions(simulation, base)
    assert result["candidate"] == result["baseline"]
    run = simulation.run(base)
    assert sum(item["sessions"] for item in result["candidate"]) == len(run.sessions) - 1
    assert all("max_drawdown" not in row for row in result["candidate"])


@pytest.mark.slow
def test_extended_suite_pairs_every_world_and_recomputes_signals(panel, program, base):
    candidate = apply_proposal(base, router_proposals()[1])
    simulation = Simulation(panel, program, None)
    study = evaluate_robustness(simulation, candidate)
    cases = len(SCENARIOS) + len(EXECUTION_STRESSES) + 1
    assert len(study.report["scenarios"]) == cases
    assert len(study.runs) == cases * 2
    assert study.report["integrity_passed"]
    assert study.report["coverage"]["complete"]
    assert study.report["coverage"]["expected_cases"] == cases
    json.dumps(study.report, allow_nan=False)
    for name, row in study.report["scenarios"].items():
        assert row["candidate"]["first_session"] == row["baseline"]["first_session"]
        assert row["candidate"]["last_session"] == row["baseline"]["last_session"]
        assert study.runs[f"{name}/candidate"][0] == candidate
        assert study.runs[f"{name}/baseline"][0] == base
        tails = row["candidate"]["return_distribution"]
        assert tails["loss_cvar_99"] >= tails["loss_var_99"]
        for role in ("candidate", "baseline"):
            run = study.runs[f"{name}/{role}"][1]
            assert all(record.cash >= 0 and record.equity > 0 for record in run.sessions)
            for previous, current in zip(run.sessions, run.sessions[1:], strict=False):
                assert (
                    abs(current.equity - previous.equity - current.attribution.explained_change)
                    <= 0.01
                )
    assert study.report["scenarios"]["missed_10pct"]["candidate"]["missed_fills"] > 0
    bull = scenario_panel(panel, first=503, kind="bull", seed=program.evaluation.scenario_seed)
    new_scores = Simulation(bull, program, None).trace(candidate).scores
    assert not np.array_equal(simulation.trace(candidate).scores[503:], new_scores[503:])
    assert study.report["hypothetical_summary"]["count"] == len(SCENARIOS) + 1
    assert study.report["scenarios"]["liquidity_crisis"]["candidate"]["missed_fills"] > 0
