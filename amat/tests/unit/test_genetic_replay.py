"""Matched provenance, intervention recovery and authoritative replay accounting."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.data.synthetic import generate_panel
from autoresearch.contracts import Program
from autoresearch.experiments import Simulation
from autoresearch.genetic.data import CandleDataset
from autoresearch.genetic.replay import (
    _candidate_recipes,
    _evaluate_variant,
    attach_pattern,
    exact_replay_equal,
    load_incumbent,
    matched_studies,
)
from autoresearch.genetic.search import SearchProtocol
from autoresearch.genetic.study import ScreenConfig, run_study

ROOT = Path(__file__).parents[2]


@pytest.fixture
def studies(tmp_path):
    rows = np.arange(40, dtype=float)[:, None]
    opens = 100 + rows + np.array([[0, 12]])
    closes = opens + np.where(rows % 3 == 0, 1.0, -0.5)
    data = CandleDataset(
        tuple(f"day-{i}" for i in range(40)),
        ("AAA", "BBB"),
        {
            "open": opens,
            "close": closes,
            "high": np.maximum(opens, closes) + 2,
            "low": np.minimum(opens, closes) - 2,
        },
        opens.copy(),
        "1d",
        "corporate_action_adjusted",
        {"training_start": "2020-01-01", "training_end": "2020-02-25", "symbols": ["AAA", "BBB"]},
    )
    protocol = SearchProtocol(population_size=8, generations=1, seed=8)
    config = ScreenConfig(min_matched_sessions=2)
    ga, random = tmp_path / "ga", tmp_path / "random"
    run_study(data, protocol, config, ga)
    run_study(data, replace(protocol, method="random"), config, random)
    return ga, random, data, protocol, config


def test_matched_studies_retain_full_artifacts_and_exact_budget(studies):
    ga, random, _, protocol, _ = studies
    left, right = matched_studies(ga, random)
    assert (
        left["protocol"]["candidate_slot_budget"]
        == right["protocol"]["candidate_slot_budget"]
        == protocol.budget
    )
    assert "checkpoint.json" in left["artifacts"]
    assert left["search_counts"]["recorded_slots"] == protocol.budget
    assert left["artifact_sha256"]


@pytest.mark.parametrize("change", ["seed", "budget", "cost"])
def test_mismatched_control_rejected(studies, tmp_path, change):
    ga, _, data, protocol, config = studies
    protocol = replace(protocol, method="random")
    if change == "seed":
        protocol = replace(protocol, seed=9)
    elif change == "budget":
        protocol = replace(protocol, generations=2)
    else:
        config = replace(config, one_way_bps=3)
    other = tmp_path / "other"
    run_study(data, protocol, config, other)
    with pytest.raises(ValueError, match="mismatch"):
        matched_studies(ga, other)


@pytest.mark.parametrize("filename", ["checkpoint.json", "report.json", "champion.pattern.json"])
def test_corrupt_discovery_rejected(studies, filename):
    ga, random, *_ = studies
    path = ga / filename
    contents = json.loads(path.read_text())
    if filename == "checkpoint.json":
        contents["state"]["champion"]["fitness"] += 1
    elif filename == "report.json":
        contents["status"] = "paused"
    else:
        contents["name"] = "changed_champion"
    path.write_text(json.dumps(contents))
    with pytest.raises(ValueError):
        matched_studies(ga, random)


def test_candidate_recipes_remove_pattern_exactly(studies):
    ga, random, *_ = studies
    left, right = matched_studies(ga, random)
    base = load_incumbent(ROOT / "configs/strategy/amat_v13_return_target.toml")
    recipes = _candidate_recipes(base, left, right)
    assert recipes["remove_pattern"] == recipes["incumbent"] == base
    assert recipes["remove_pattern"].digest() == base.digest()
    assert recipes["genetic"].genetic_pattern is not None
    assert recipes["bullish_candle"].genetic_pattern.pattern.genome.max_lag == 0


def test_exact_equality_handles_nan_but_retains_all_other_fields():
    assert exact_replay_equal(
        {"missing": float("nan"), "fills": [1]}, {"missing": float("nan"), "fills": [1]}
    )
    assert not exact_replay_equal(
        {"missing": float("nan"), "fills": [1]}, {"missing": float("nan"), "fills": [2]}
    )
    assert not exact_replay_equal(float("nan"), 0)


def test_authoritative_variant_prefix_and_persisted_controls(studies, tmp_path):
    from autoresearch.genetic.models import parse_pattern

    ga, random, *_ = studies
    chosen, _ = matched_studies(ga, random)
    base = load_incumbent(ROOT / "configs/strategy/amat_v13_return_target.toml")
    market = generate_panel(base.config.strategy.to_universe(), sessions=540)
    opens = market.adjusted_closes * np.where(np.arange(540)[:, None] % 2 == 0, 0.999, 1.001)
    market = replace(
        market,
        source="scenario(replay-test)",
        adjusted_opens=opens,
        adjusted_highs=market.adjusted_closes * 1.01,
        adjusted_lows=market.adjusted_closes * 0.99,
        session_volumes=np.full_like(opens, 1e7),
    )
    cutoff = 525
    fields = {
        name: getattr(market, name)[:cutoff].copy()
        for name in (
            "adjusted_closes",
            "raw_closes",
            "window_vwaps",
            "window_volumes",
            "adjusted_opens",
            "adjusted_highs",
            "adjusted_lows",
            "session_volumes",
        )
    }
    train = replace(market, sessions=market.sessions[:cutoff], **fields)
    program = Program(
        schema_version="strategy-development/8",
        name="replay-test",
        load_start=market.sessions[0],
        measured_start=market.sessions[503],
        train_end=train.sessions[-1],
        validation_end=market.sessions[-1],
        baseline=base,
        frozen_files={},
        dependencies={},
    )
    candidate = attach_pattern(base, parse_pattern(chosen["champion"]["pattern"]))
    report = _evaluate_variant(
        "genetic",
        candidate,
        Simulation(train, program, None),
        Simulation(market, program, None),
        tmp_path,
        contract_hash="a" * 64,
    )
    assert report["status"] == "evaluated"
    assert "insufficient_year_folds" in report["training_objective"]["failed_gates"]
    assert not report["training_objective"]["eligible"]
    assert report["development_cost_6bps"]
    control_path = tmp_path / "runs/genetic/exposure_control/results.json"
    assert json.loads(control_path.read_text())["candidate_sha256"] == base.digest()
    recipe = (tmp_path / "runs/genetic/exposure_control/config.toml").read_text()
    assert "scalar" in recipe


@pytest.mark.parametrize("status,exit_code", [("completed", 0), ("completed_with_failures", 1)])
def test_replay_cli_returns_failure_status(tmp_path, monkeypatch, status, exit_code):
    from autoresearch.genetic import cli, replay

    monkeypatch.setattr(replay, "load_incumbent", lambda _path: object())
    monkeypatch.setattr(replay, "run_replay", lambda *_args, **_kwargs: {"status": status})
    result = cli.main(
        [
            "replay",
            "--incumbent",
            "base.toml",
            "--snapshot",
            "snapshot",
            "--genetic-study",
            "ga",
            "--random-study",
            "random",
            "--start",
            "2016-01-04",
            "--train-end",
            "2020-12-31",
            "--evaluation-end",
            "2022-12-30",
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert result == exit_code


@pytest.fixture
def replay_preflight(studies, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import exchange_calendars as xcals

    from autoresearch.genetic import replay

    ga, random, *_ = studies
    left, right = matched_studies(ga, random)
    base = load_incumbent(ROOT / "configs/strategy/amat_v13_return_target.toml")
    market = generate_panel(base.config.strategy.to_universe(), sessions=540)
    calendar = xcals.get_calendar("XNYS", start="2016-01-04", end="2019-01-01")
    market = replace(market, sessions=tuple(day.date() for day in calendar.sessions[:540]))
    cutoff = 525
    fields = {
        name: getattr(market, name)[:cutoff].copy()
        for name in (
            "adjusted_closes",
            "raw_closes",
            "window_vwaps",
            "window_volumes",
            "adjusted_highs",
            "adjusted_lows",
            "session_volumes",
        )
    }
    train = replace(market, sessions=market.sessions[:cutoff], **fields)
    for record in (left, right):
        record["protocol"]["dataset"] = {
            "symbols": [str(symbol) for symbol in base.config.strategy.universe.symbols],
            "training_start": train.sessions[0].isoformat(),
            "training_end": train.sessions[-1].isoformat(),
        }
    manifest = SimpleNamespace(partition_sha256={}, model_dump=lambda **_: {})
    monkeypatch.setattr(replay, "matched_studies", lambda *_: (left, right))
    monkeypatch.setattr(
        replay,
        "load_daily",
        lambda *_args, **_kwargs: SimpleNamespace(digest=left["protocol"]["dataset_sha256"]),
    )
    monkeypatch.setattr(
        replay,
        "load_panel",
        lambda *_args, **kwargs: (
            train if kwargs["end"] == train.sessions[-1] else market,
            manifest,
        ),
    )
    ledger = tmp_path / "actions.toml"
    ledger.write_text("# frozen action resolution\n")
    monkeypatch.setattr(replay, "default_ledger_path", lambda _: ledger)
    monkeypatch.setattr(replay, "dependencies", lambda: {})
    args = {
        "incumbent": base,
        "snapshot": tmp_path / "snapshot",
        "ga_study": ga,
        "random_study": random,
        "start": train.sessions[0],
        "train_end": train.sessions[-1],
        "evaluation_end": market.sessions[-1],
        "output": tmp_path / "replay",
    }
    return SimpleNamespace(
        module=replay, train=train, market=market, manifest=manifest, args=args, ledger=ledger
    )


def test_replay_rejects_interior_missing_evaluation_session(replay_preflight, monkeypatch):
    setup = replay_preflight
    drop = 530
    market = setup.market
    keep = np.arange(len(market.sessions)) != drop
    fields = {
        name: getattr(market, name)[keep].copy()
        for name in (
            "adjusted_closes",
            "raw_closes",
            "window_vwaps",
            "window_volumes",
            "adjusted_highs",
            "adjusted_lows",
            "session_volumes",
        )
    }
    gapped = replace(
        market, sessions=tuple(day for i, day in enumerate(market.sessions) if i != drop), **fields
    )
    monkeypatch.setattr(
        setup.module,
        "load_panel",
        lambda *_args, **kwargs: (
            setup.train if kwargs["end"] == setup.train.sessions[-1] else gapped,
            setup.manifest,
        ),
    )
    with pytest.raises(ValueError, match="exchange sessions"):
        setup.module.run_replay(**setup.args)
    assert not setup.args["output"].exists()


@pytest.mark.parametrize("change", ["source", "exceptions"])
def test_replay_refuses_publication_after_input_mutation(replay_preflight, monkeypatch, change):
    setup = replay_preflight
    changed = False

    class FakeSimulation:
        def __init__(self, *_args):
            self.actions = {}

        def run(self, _candidate):
            return {"accounting": "constant fixture"}

    def evaluate(*_args, **_kwargs):
        nonlocal changed
        changed = True
        if change == "exceptions":
            setup.ledger.write_text("# modified action resolution\n")
        return {"status": "evaluated"}

    monkeypatch.setattr(setup.module, "Simulation", FakeSimulation)
    monkeypatch.setattr(setup.module, "_evaluate_variant", evaluate)
    monkeypatch.setattr(
        setup.module,
        "_code_hashes",
        lambda: {"source": "changed" if changed and change == "source" else "original"},
    )
    with pytest.raises(ValueError, match="changed during replay"):
        setup.module.run_replay(**setup.args)
    assert (setup.args["output"] / "protocol.json").exists()
    assert not (setup.args["output"] / "report.json").exists()
