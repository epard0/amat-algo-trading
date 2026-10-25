"""Concrete recipe validity, phase slicing, feature cache, and bootstrap contracts."""

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import numpy as np
import pytest

from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate
from autoresearch.genetic import parameter_study as study
from autoresearch.genetic.models import FeatureName, PatternDefinition

ROOT = Path(__file__).parents[2]


@pytest.fixture
def inputs():
    pattern = PatternDefinition.model_validate_json(
        (ROOT / "docs/genetic-patterns/symbolic.example.json").read_text()
    )
    return Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    ), pattern


def test_baseline_recipe_recovers_exact_candidate_and_target_section(inputs):
    base, _ = inputs
    recipe = study._baseline_recipe(base)
    assert study._candidate(recipe, base).digest() == base.digest()
    assert study._exit_rules(recipe) is None
    changed = study._candidate({**recipe, "target_vol": 0.08}, base)
    assert changed.config.strategy.constraints.target_annualized_volatility == 0.08
    assert base.config.strategy.constraints.target_annualized_volatility == 0.2


def test_every_terminal_pattern_validates_and_gp_formula_preserved(inputs):
    _, pattern = inputs
    for name in get_args(FeatureName):
        for frame in study.TIMEFRAMES:
            result = study._pattern(f"{name}:21", pattern, frame)
            assert result.genome.output_transform == "tanh"
            assert result.timeframe == frame
    assert study._pattern("genetic_pattern", pattern, "1h").genome == pattern.genome


@pytest.mark.parametrize(
    "mode,stop,profit",
    [("none", None, None), ("stop", 0.05, None), ("profit", None, 0.1), ("both", 0.05, 0.1)],
)
def test_exit_reward_risk_modes(mode, stop, profit):
    rules = study._exit_rules(
        {"exit_mode": mode, "stop_fraction": 0.05, "reward_risk": 2, "embargo": 2.8}
    )
    if mode == "none":
        assert rules is None
    else:
        assert rules.stop_loss == stop
        assert rules.take_profit == profit
        assert rules.reentry_embargo_sessions == 3
    with pytest.raises(ValueError):
        study._exit_rules({"exit_mode": "unknown"})


def test_phase_returns_dates_and_fills_use_same_exclusive_boundary(inputs, monkeypatch):
    base, _ = inputs
    days = [dt.date(2024, 1, i) for i in range(1, 13)]
    equity = np.cumprod(np.r_[100.0, np.repeat(1.01, 11)])
    result = SimpleNamespace(
        equity_curve=equity,
        session_dates=days,
        sessions=[SimpleNamespace(session=day, fills=[1, 2, 3]) for day in days],
        warnings=(),
    )
    simulation = SimpleNamespace(panel=[1], traces={}, overlays={}, selections={})
    monkeypatch.setattr(
        study,
        "_CONTEXT",
        {
            "baseline": base,
            "simulations": {"validation": simulation},
            "phase_starts": {"validation": "2024-01-04"},
        },
    )
    monkeypatch.setattr(study, "prepare_replay", lambda *_args: None)
    monkeypatch.setattr(study, "replay_prepared", lambda *_args, **_kwargs: result)
    metrics = study._evaluate_task((study._baseline_recipe(base), "validation"))
    assert metrics["dates"] == [str(day) for day in days[4:]]
    np.testing.assert_allclose(metrics["returns"], 0.01)
    assert len(metrics["returns"]) == len(metrics["dates"]) == 8
    assert metrics["fills"] == 24
    assert metrics["score"] is not None
    # Poison earlier history; the selected phase's anchor remains intact.
    equity[0] = 999999
    assert (
        study._evaluate_task((study._baseline_recipe(base), "validation"))["returns"]
        == metrics["returns"]
    )
    stressed = study.evaluate_recipe(study._baseline_recipe(base), "validation", cost=10)
    assert stressed["cost_bps"] == 10
    result.warnings = ("accounting warning",)
    assert study._evaluate_task((study._baseline_recipe(base), "validation"))["score"] is None


def test_low_activity_is_ineligible(inputs, monkeypatch):
    base, _ = inputs
    days = [dt.date(2024, 1, i) for i in range(1, 12)]
    result = SimpleNamespace(
        equity_curve=np.ones(11) * 100,
        session_dates=days,
        sessions=[SimpleNamespace(session=day, fills=[]) for day in days],
        warnings=(),
    )
    simulation = SimpleNamespace(panel=[1], traces={}, overlays={}, selections={})
    monkeypatch.setattr(
        study,
        "_CONTEXT",
        {
            "baseline": base,
            "simulations": {"train": simulation},
            "phase_starts": {"train": "2024-01-01"},
        },
    )
    monkeypatch.setattr(study, "prepare_replay", lambda *_args: None)
    monkeypatch.setattr(study, "replay_prepared", lambda *_args, **_kwargs: result)
    assert study._evaluate_task((study._baseline_recipe(base), "train"))["score"] is None


def test_bootstrap_paired_reproducible_and_explicitly_conditional():
    returns = [0.01, -0.02, 0.03, -0.01]
    result = study.paired_return_bootstrap(returns, returns, seed=7, paths=20, block=2)
    assert result == study.paired_return_bootstrap(returns, returns, seed=7, paths=20, block=2)
    assert result["return_difference_quantiles"] == {"p05": 0.0, "p50": 0.0, "p95": 0.0}
    assert "not market-path" in result["interpretation"]
    with pytest.raises(ValueError):
        study.paired_return_bootstrap([0.1], [0.1, 0.2], seed=7)
    with pytest.raises(ValueError):
        study.paired_return_bootstrap([0.1, np.nan], [0.1, 0.2], seed=7)


def test_scope_discloses_separate_protocol_methods():
    scope = study._method_scope()
    assert "new candle/symbolic GP evolution" in scope["separate_protocol_required"]
    assert "contextual-bandit risk learning" in scope["separate_protocol_required"]
    assert "python" in study._dependencies()


def test_feature_bank_train_eligibility_and_interrupted_sidecar_recovery(
    inputs, tmp_path, monkeypatch
):
    _, pattern = inputs
    (tmp_path / "features").mkdir()
    panel = SimpleNamespace(
        symbols=("SPY", "QQQ"),
        sessions=tuple(dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(30)),
    )
    raw = np.ones((30, 2))
    raw[:20, 1] = np.nan
    monkeypatch.setattr(
        study,
        "_CONTEXT",
        {
            "output": tmp_path,
            "context_sha256": "a" * 64,
            "pattern": pattern,
            "panel": panel,
            "train_end": panel.sessions[19],
            "measured_start": panel.sessions[0],
            "views": {"1mo": panel},
        },
    )
    monkeypatch.setattr(study, "evaluate_symbolic", lambda *_args: raw)
    monkeypatch.setattr(study, "align_closed_values", lambda *_args, **_kwargs: raw)
    study._build_method("genetic_pattern")
    target = tmp_path / "features/genetic_pattern.npz"
    binding = target.with_suffix(".json")
    expected = json.loads(binding.read_text())
    assert expected["availability"]["1mo"]["training_eligible_assets"] == [True, False]
    assert expected["availability"]["1mo"]["training_finite_source_bar_counts"] == [20, 0]
    with np.load(target) as values:
        assert np.isnan(values["1mo"][:, 1]).all()  # Later observations cannot authorize the view.
        assert (values["1mo"][:, 0] == 1).all()
    original = target.read_bytes()
    binding.unlink()
    study._build_method("genetic_pattern")
    assert target.read_bytes() == original
    assert json.loads(binding.read_text()) == expected
    bad = {**expected, "sha256": "bad"}
    binding.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="hash mismatch"):
        study._build_method("genetic_pattern")


def test_native_symbolic_marker_uses_registered_contract(inputs):
    from dataclasses import replace

    from autoresearch.genetic.native_intraday import SCHEMA, TIMESTAMP_SEMANTICS
    from autoresearch.genetic.native_views import native_panels
    from autoresearch.genetic.symbolic import evaluate_symbolic

    _, pattern = inputs
    payload = {
        "schema_version": SCHEMA,
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "all",
        "symbols": ["SPY"],
        "start": "2025-11-03",
        "end": "2025-11-03",
        "collected_at": "2025-11-04T00:00:00Z",
        "timeframe": "15m",
        "provider_timeframe": "15Min",
        "alignment": "unknown",
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "native_records": [
            {
                "symbol": "SPY",
                "t": "2025-11-03T14:30:00Z",
                "o": 100.0,
                "h": 101.0,
                "l": 99.0,
                "c": 100.0,
                "v": 10.0,
            }
        ],
    }
    panel = native_panels(payload, dt.datetime(2025, 11, 4, tzinfo=dt.UTC))["15m"]
    source = pattern.model_dump(mode="json", by_alias=True)
    source["timeframe"] = "15m"
    source["genome"]["root"] = {"node": "constant", "value": 0.5}
    source["genome"]["outputTransform"] = "tanh"
    pattern = PatternDefinition.model_validate(source)
    assert evaluate_symbolic(pattern, panel).shape == panel.adjusted_closes.shape
    with pytest.raises(ValueError, match="explicitly aggregated"):
        evaluate_symbolic(
            pattern,
            replace(panel, source="alpaca:sip:all:native-15m-components:15m:contract=wrong"),
        )
