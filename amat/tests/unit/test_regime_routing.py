"""Executable arithmetic, causality, failure handling and real router evidence."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from autoresearch.contracts import Candidate, Program, validate_candidate
from autoresearch.inspirations import router_proposals
from autoresearch.loop import run_loop
from autoresearch.registry import raw_values
from autoresearch.routing import route_scores
from autoresearch.search import combinations, policy_variants
from autoresearch.signals import build_signal
from autoresearch.specs import FeatureSpec, RouterSpec, SignalSpec, SignalTerm
from autoresearch.variants import ablations, neighbors
from autoresearch.workspace import initialize

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def base():
    return Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)


@pytest.fixture(scope="module")
def market(base):
    return generate_panel(base.config.strategy.to_universe(), sessions=900)


@pytest.fixture(scope="module")
def program(base, market):
    return Program(
        name="router-test",
        load_start=market.sessions[0],
        measured_start=market.sessions[503],
        train_end=market.sessions[-2],
        validation_end=market.sessions[-1],
        baseline=base,
        frozen_files={},
        dependencies={},
    )


def test_breadth_uses_frozen_universe_and_quarantines_low_coverage(market):
    columns = len(market.symbols)
    signs = np.where(np.arange(columns) < columns // 2, 1, -1)
    closes = 100 * np.exp(np.arange(len(market))[:, None] * signs * 0.001)
    spec = FeatureSpec(kind="breadth", lookback=5)
    values = raw_values(replace(market, adjusted_closes=closes), spec)
    np.testing.assert_allclose(values[5:], (columns // 2) / columns)
    closes[10, : columns // 3] = np.nan
    with np.errstate(invalid="ignore"):
        missing = raw_values(replace(market, adjusted_closes=closes), spec)
    assert np.isnan(missing[10]).all()
    assert np.isnan(missing[15]).all()
    assert np.isfinite(missing[16]).all()


def test_volatility_features_match_independent_calculation(market):
    rng = np.random.default_rng(8)
    returns = rng.normal(0, 0.01, market.adjusted_closes.shape)
    closes = 100 * np.exp(np.cumsum(returns, axis=0))
    panel = replace(market, adjusted_closes=closes)
    shock = raw_values(
        panel, FeatureSpec(kind="volatility_shock", lookback=20, secondary_lookback=5)
    )
    expected = np.log(returns[96:101].std(axis=0, ddof=1) / returns[81:101].std(axis=0, ddof=1))
    np.testing.assert_allclose(shock[100], expected, atol=1e-12)
    instability = raw_values(
        panel, FeatureSpec(kind="volatility_of_volatility", lookback=20, secondary_lookback=5)
    )
    logs = np.stack([np.log(returns[i - 19 : i + 1].std(axis=0, ddof=1)) for i in range(96, 101)])
    np.testing.assert_allclose(instability[100], logs.std(axis=0, ddof=1), atol=1e-12)
    flat = replace(market, adjusted_closes=np.full_like(closes, 100.0))
    for kind in ("volatility_shock", "volatility_of_volatility"):
        values = raw_values(flat, FeatureSpec(kind=kind))
        assert np.isfinite(values[-1]).all()
        np.testing.assert_allclose(values[-1], 0, atol=1e-12)


def arithmetic_signal(**changes):
    return SignalSpec(
        terms=(
            SignalTerm(feature=FeatureSpec(kind="momentum")),
            SignalTerm(feature=FeatureSpec(kind="rsi"), weight=-1),
            SignalTerm(feature=FeatureSpec(kind="efficiency"), weight=0),
            SignalTerm(feature=FeatureSpec(kind="volatility_shock"), weight=0),
        ),
        router=RouterSpec.model_validate(
            {
                "stress_term": 3,
                "state_floor": 0,
                "state_ceiling": 1,
                "smoothing_sessions": 1,
                "stress_threshold": 0,
                "stress_scale": 1,
                **changes,
            }
        ),
    )


def test_router_blend_stress_and_negative_trend_guard():
    values = np.zeros((60, 2, 4))
    values[:, :, 0] = np.arctanh(0.6)  # trend score 0.8
    values[:, :, 1] = np.arctanh(-0.2)  # range score 0.6
    values[:, :, 2] = 0.25
    values[:, :, 3] = 1  # stress gate 0.5
    trace = route_scores(values, arithmetic_signal(), 0, 50)
    np.testing.assert_allclose(trace.raw, 0.5 * (0.25 * 0.8 + 0.75 * 0.6))
    values[:, 0, 0] = np.arctanh(-0.6)
    guarded = route_scores(values, arithmetic_signal(), 0, 50)
    np.testing.assert_allclose(guarded.raw[:, 0], 0.5 * 0.25 * 0.2)
    values[55, 1, 2] = np.nan
    missing = route_scores(values, arithmetic_signal(), 0, 50)
    assert not missing.available[55, 1]
    assert np.isnan(missing.raw[55, 1])


def test_training_mean_is_per_asset_frozen_and_rejects_insufficient_history():
    values = np.zeros((100, 2, 4))
    values[:50, 0, 2], values[:50, 1, 2] = 0.2, 0.7
    values[50:, :, 2] = 0.99
    signal = arithmetic_signal(mode="training_mean")
    trace = route_scores(values, signal, 0, 50)
    np.testing.assert_allclose(trace.components[50:, 0, 2], 0.2)
    np.testing.assert_allclose(trace.components[50:, 1, 2], 0.7)
    truncated = route_scores(values[:70], signal, 0, 50)
    np.testing.assert_allclose(trace.raw[:70], truncated.raw)
    insufficient = route_scores(values, signal, 30, 50)
    assert not insufficient.available.any()


@pytest.mark.parametrize(
    "update",
    [
        {"learner": "ridge"},
        {"router": {"state_term": 8}},
        {"router": {"trend_terms": [0, 0]}},
        {"router": {"trend_terms": [2]}},
        {"router": {"state_floor": 0.5, "state_ceiling": 0.5}},
        {"interactions": [{"left": 0, "right": 1}]},
    ],
)
def test_invalid_router_contracts_fail_closed(update):
    payload = arithmetic_signal().model_dump(mode="json")
    for key, value in update.items():
        if key == "router":
            payload[key].update(value)
        else:
            payload[key] = value
    with pytest.raises(ValueError):
        SignalSpec.model_validate(payload)


@pytest.mark.parametrize("mode", ["adaptive", "static", "training_mean"])
def test_routed_signal_prefix_invariance_and_controls(base, market, program, mode):
    payload = router_proposals()[0].signal.model_dump(mode="json")
    payload["router"]["mode"] = mode
    candidate = Candidate(config=base.config, signal=SignalSpec.model_validate(payload))
    original = build_signal(market, candidate, program)
    changed = market.adjusted_closes.copy()
    changed[800:] *= np.linspace(2, 20, len(market) - 800)[:, None]
    future = build_signal(replace(market, adjusted_closes=changed), candidate, program)
    np.testing.assert_allclose(original.scores[:800], future.scores[:800])
    np.testing.assert_allclose(
        original.routing_scores[:800], future.routing_scores[:800], equal_nan=True
    )
    assert not original.scores[:503].any()
    assert np.isfinite(original.scores).all()
    assert ((original.scores >= 0) & (original.scores <= 1)).all()
    assert original.scores[503:].any()
    for _, variant in ablations(candidate) + neighbors(candidate):
        validate_candidate(base, variant)
    assert all(
        item.signal.router and item.signal.learner == "weighted"
        for item in policy_variants(candidate)
    )
    assert combinations(candidate, [base, candidate]) == []


@pytest.mark.slow
def test_real_router_loop_persists_components_static_controls_and_resume(tmp_path):
    root = tmp_path / "research"
    initialize(
        root,
        ROOT,
        ROOT / "configs/strategy/amat_v6.toml",
        ROOT / "data/snapshots/alpaca-sip-2026-08-11",
        name="router-integration",
        train_end=dt.date(2020, 12, 31),
        validation_end=dt.date(2021, 12, 31),
        max_experiments=3,
        timeout_seconds=600,
        evaluation={"bootstrap_resamples": 200},
    )
    proposal = router_proposals()[0]
    report = run_loop(root, iterations=1, proposal=proposal)
    trial = report["experiments"][-1]
    assert trial["status"] in ("keep", "discard"), trial
    result = json.loads(trial["result"])
    controls = result["control_summary"]
    assert "router_equal_blend" in controls["measurements"]
    assert "router_training_mean" in controls["router_static_uncertainty"]
    if not controls["router_beats_static"]:
        assert "router_has_no_incremental_static_blend_value" in result["failed_gates"]
    target = root / "outputs/runs" / trial["candidate"]
    with np.load(target / "signals.npz", allow_pickle=False) as arrays:
        assert arrays["routing_scores"].shape[-1] == 4
        assert list(arrays["routing_columns"]) == [
            "trend_sleeve",
            "range_sleeve",
            "trend_weight",
            "stress_multiplier",
        ]
    assert len(json.loads((target / "diagnostics.json").read_text())["features"]) == 8
    repeated = run_loop(root, iterations=1, proposal=proposal)
    assert repeated["experiments"][-1]["candidate"] == trial["candidate"]
    assert json.loads(repeated["experiments"][-1]["result"])["train"] == result["train"]
