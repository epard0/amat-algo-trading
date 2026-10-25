"""Symbolic discovery evidence enters a distinct, matched portfolio replay contract."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.data.synthetic import generate_panel
from autoresearch.contracts import Program
from autoresearch.experiments import Simulation
from autoresearch.genetic.adapter import GeneticAdapterSpec, GeneticSymbolicAdapterSpec
from autoresearch.genetic.data import CandleDataset
from autoresearch.genetic.replay import (
    _candidate_recipes,
    _evaluate_variant,
    _pattern_diagnostics,
    attach_pattern,
    exact_replay_equal,
    load_incumbent,
    matched_studies,
)
from autoresearch.genetic.symbolic_search import SymbolicSearchProtocol, SymbolicTerminal
from autoresearch.genetic.symbolic_study import SymbolicScreenConfig, run_symbolic_study

ROOT = Path(__file__).parents[2]
FIELDS = (
    "adjusted_closes",
    "raw_closes",
    "window_vwaps",
    "window_volumes",
    "adjusted_opens",
    "adjusted_highs",
    "adjusted_lows",
    "session_volumes",
)


@pytest.fixture(scope="module")
def base():
    return load_incumbent(ROOT / "configs/strategy/amat_v13_return_target.toml")


@pytest.fixture(scope="module")
def market(base):
    panel = generate_panel(base.config.strategy.to_universe(), sessions=540)
    return replace(
        panel,
        source="scenario(symbolic-replay-test)",
        adjusted_opens=panel.adjusted_closes * 0.999,
        adjusted_highs=panel.adjusted_closes * 1.01,
        adjusted_lows=panel.adjusted_closes * 0.99,
    )


@pytest.fixture
def studies(tmp_path, market):
    train = replace(
        market,
        sessions=market.sessions[:525],
        **{name: getattr(market, name)[:525].copy() for name in FIELDS},
    )
    data = CandleDataset(
        tuple(day.isoformat() for day in train.sessions),
        tuple(str(symbol) for symbol in train.symbols),
        dict(zip(("open", "high", "low", "close"), train.candle_inputs(), strict=True)),
        train.adjusted_opens.copy(),
        "1d",
        "corporate_action_adjusted",
        {
            "training_start": train.sessions[0].isoformat(),
            "training_end": train.sessions[-1].isoformat(),
            "symbols": list(train.symbols),
        },
    )
    # One real varying feature guarantees eligible candidates, independent of
    # the search engine's operator choices or chance constant-only populations.
    protocol = SymbolicSearchProtocol(
        population_size=3,
        generations=1,
        elite_count=1,
        max_depth=0,
        max_nodes=1,
        constants=(),
        feature_terminals=(SymbolicTerminal("momentum", lookback=5),),
    )
    ga, random = tmp_path / "ga", tmp_path / "random"
    for directory, settings in ((ga, protocol), (random, replace(protocol, method="random"))):
        run_symbolic_study(
            data, train, settings, SymbolicScreenConfig(min_effect_sessions=2), directory
        )
    return ga, random, train


def test_matched_symbolic_studies_dispatch_and_attach_distinct_adapter(studies, base):
    ga, random, _ = studies
    left, right = matched_studies(ga, random)
    assert left["protocol"]["schema_version"] == "training-symbolic-screen/1"
    assert left["protocol"]["symbolic_context"] == right["protocol"]["symbolic_context"]
    recipes = _candidate_recipes(base, left, right)
    assert isinstance(recipes["genetic"].genetic_pattern, GeneticSymbolicAdapterSpec)
    assert isinstance(recipes["random"].genetic_pattern, GeneticSymbolicAdapterSpec)
    assert isinstance(recipes["bullish_candle"].genetic_pattern, GeneticAdapterSpec)
    assert recipes["remove_pattern"].digest() == base.digest()


def test_changed_symbolic_context_rejected(studies):
    ga, random, _ = studies
    path = random / "protocol.json"
    frozen = json.loads(path.read_text())
    frozen["symbolic_context"]["registry_sha256"] = "0" * 64
    path.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match=r"provenance|mismatch"):
        matched_studies(ga, random)


def test_symbolic_portfolio_replay_uses_continuous_diagnostics_and_prefix_parity(
    studies, base, market, tmp_path
):
    ga, random, train = studies
    chosen, control = matched_studies(ga, random)
    candidate = _candidate_recipes(base, chosen, control)["genetic"]
    program = Program(
        schema_version="strategy-development/9",
        name="symbolic-replay-test",
        load_start=train.sessions[0],
        measured_start=train.sessions[503],
        train_end=train.sessions[-1],
        validation_end=market.sessions[-1],
        baseline=base,
        frozen_files={},
        dependencies={},
    )
    training, evaluation = Simulation(train, program, None), Simulation(market, program, None)
    report = _evaluate_variant(
        "symbolic", candidate, training, evaluation, tmp_path, contract_hash="c" * 64
    )
    assert report["status"] == "evaluated"
    assert not report["training_objective"]["eligible"]  # Fixture is too short for year-fold gates.
    diagnostics = _pattern_diagnostics(evaluation, candidate)
    for row in diagnostics["per_asset"].values():
        assert "matched_bars" not in row
        assert (
            row["positive_bars"] + row["negative_bars"] + row["zero_bars"] == row["observed_bars"]
        )
        assert -1 <= row["mean_value"] <= 1
    recipe = json.loads((tmp_path / "runs/symbolic/training/results.json").read_text())
    assert recipe["candidate_sha256"] == candidate.digest()
    assert report["development_cost_6bps"]


def test_missing_symbolic_output_has_exact_native_accounting(base, market):
    from autoresearch.genetic.models import parse_pattern

    payload = json.loads((ROOT / "docs/genetic-patterns/symbolic.example.json").read_text())
    payload["genome"]["root"] = {
        "node": "binary",
        "op": "safe_div",
        "left": {"node": "constant", "value": 1.0},
        "right": {"node": "constant", "value": 0.0},
    }
    candidate = attach_pattern(base, parse_pattern(payload))
    program = Program(
        schema_version="strategy-development/9",
        name="symbolic-neutral-test",
        load_start=market.sessions[0],
        measured_start=market.sessions[503],
        train_end=market.sessions[-2],
        validation_end=market.sessions[-1],
        baseline=base,
        frozen_files={},
        dependencies={},
    )
    simulation = Simulation(market, program, None)
    assert exact_replay_equal(simulation.run(base), simulation.run(candidate))
    trace = simulation.diagnostic_trace(candidate)
    assert trace.features[-1]["metadata"]["units"] == "bounded_numerical"
    assert np.isnan(trace.predictors).all()
