"""Causal entry-quality learning, missed-winner controls and execution integration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.runner import DecisionOverlay, run_backtest
from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from amat.domain.types import Side
from autoresearch.contracts import Candidate, Program, Proposal, apply_proposal, validate_candidate
from autoresearch.entry_labels import EntryOutcomes, entry_outcomes
from autoresearch.entry_model import (
    FEATURES,
    EntryForecast,
    ForecastBlock,
    apply_entry_policy,
    bounded_deferrals,
    build_entry_forecast,
    policy_evidence,
    timing_utility,
)
from autoresearch.experiments import Simulation
from autoresearch.labels import adjusted_window_prices
from autoresearch.registry import raw_values
from autoresearch.specs import EntryModelSpec, EvaluationSpec, FeatureSpec
from autoresearch.variants import ablations, neighbors, proposal_for
from autoresearch.worker import entry_feedback, persist_entry_model

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def base():
    return Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)


@pytest.fixture(scope="module")
def market(base):
    panel = generate_panel(base.config.strategy.to_universe(), sessions=690, seed=19)
    rng = np.random.default_rng(31)
    shape = panel.adjusted_closes.shape
    return replace(
        panel,
        source="scenario(entry-test)",
        adjusted_highs=panel.adjusted_closes * 1.015,
        adjusted_lows=panel.adjusted_closes * 0.985,
        session_volumes=rng.uniform(800_000, 1_200_000, shape),
        window_volumes=np.full(shape, 100_000.0),
    )


@pytest.fixture(scope="module")
def program(base, market):
    return Program(
        name="entry-test",
        baseline=base,
        frozen_files={},
        dependencies={},
        load_start=market.sessions[0],
        measured_start=market.sessions[503],
        train_end=market.sessions[-2],
        validation_end=market.sessions[-1],
        evaluation=EvaluationSpec(bootstrap_resamples=200),
    )


def prefix(panel, stop):
    arrays = {
        field.name: getattr(panel, field.name)[:stop]
        for field in fields(panel)
        if isinstance(getattr(panel, field.name), np.ndarray)
    }
    return replace(panel, sessions=panel.sessions[:stop], **arrays)


def test_optional_recipe_preserves_historical_canonical_hash_and_roundtrip(base):
    legacy = base.model_dump(mode="json")
    assert "entry_model" not in legacy
    expected = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert base.digest() == expected
    proposal = Proposal(
        hypothesis="Account for bad entry timing and missed winners.", entry_model=EntryModelSpec()
    )
    candidate = apply_proposal(base, proposal)
    validate_candidate(base, candidate)
    assert candidate.digest() != base.digest()
    replay = proposal_for(base, candidate, hypothesis=proposal.hypothesis, stage="manual")
    assert apply_proposal(base, replay) == candidate
    assert dict(ablations(candidate))["entry_disabled"] == base
    assert len(neighbors(candidate)) == 2
    for _, variant in ablations(candidate) + neighbors(candidate):
        validate_candidate(base, variant)
        assert variant.config == base.config


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_entry_reduction", 1),
        ("max_deferral_sessions", 0),
        ("max_feature_z", float("inf")),
        ("horizon", 5),
    ],
)
def test_unbounded_recipe_rejected(field, value):
    with pytest.raises(ValueError):
        EntryModelSpec(**{field: value})


def test_entry_model_rejects_competing_signal_and_wrong_execution_lag(base):
    candidate = apply_proposal(
        base,
        Proposal.model_validate_json(
            (ROOT / "configs/autoresearch/adaptive-ml.example.json").read_text()
        ),
    )
    with pytest.raises(ValueError, match="competing"):
        Candidate(config=base.config, signal=candidate.signal, entry_model=EntryModelSpec())
    data = base.model_dump(mode="json")
    data["entry_model"] = {}
    data["config"]["strategy"]["execution"]["signal_to_execution_lag_sessions"] = 2
    with pytest.raises(ValueError, match="next-session"):
        Candidate.model_validate(data)


def test_labels_mature_independently_and_exclude_pre_entry_lows(market):
    panel = prefix(market, 70)
    prices = np.full_like(panel.adjusted_closes, 100.0)
    lows = prices.copy()
    lows[1] = 1.0  # May have occurred before the first entry window ended.
    lows[4] = 95.0
    window = prices.copy()
    window[2] = 98.0
    # A split in raw observations must not change adjusted labels.
    factor = np.ones_like(prices)
    factor[10:] = 0.5
    panel = replace(
        panel,
        adjusted_closes=prices,
        adjusted_lows=lows,
        adjusted_highs=prices,
        raw_closes=prices * factor,
        window_vwaps=window * factor,
    )
    labels = entry_outcomes(panel, 60)
    np.testing.assert_allclose(labels.adverse_excursion[0], 0.05)
    np.testing.assert_allclose(labels.deferral_advantage[0], 100 / 98 - 1)
    np.testing.assert_allclose(labels.markout_one[0], 0.0)
    np.testing.assert_allclose(labels.net_return[0], (1 - 0.0002) / (1 + 0.0002) - 1)
    for stop in (2, 3, 5, 6, 61, 62):
        partial = entry_outcomes(prefix(panel, stop), 60)
        assert np.isfinite(partial.deferral_advantage[0]).all() == (stop >= 3)
        assert np.isfinite(partial.adverse_excursion[0]).all() == (stop >= 6)
        assert np.isfinite(partial.net_return[0]).all() == (stop >= 62)
    volume = panel.window_volumes.copy()
    volume[2] = 0
    missing = entry_outcomes(replace(panel, window_volumes=volume), 60)
    assert np.isnan(missing.deferral_advantage[0]).all()


@pytest.mark.parametrize("value", [0.0, -1.0, np.nan, np.inf])
def test_invalid_windows_are_not_labels_or_features(market, value):
    panel = prefix(market, 70)
    volume = panel.window_volumes.copy()
    volume[30, 0] = value
    panel = replace(panel, window_volumes=volume)
    assert np.isnan(adjusted_window_prices(panel)[30, 0])
    labels = entry_outcomes(panel, 20)
    assert np.isnan(labels.net_return[29, 0])
    assert np.isnan(labels.deferral_advantage[28:30, 0]).all()
    assert np.isnan(raw_values(panel, FeatureSpec(kind="opening_gap", lookback=20))[30, 0])
    assert np.isnan(
        raw_values(panel, FeatureSpec(kind="window_markout", lookback=5))[30:35, 0]
    ).all()


def test_execution_proxy_formulas_and_causal_prefix(market):
    panel = prefix(market, 70)
    for spec in FEATURES:
        np.testing.assert_array_equal(raw_values(panel, spec), raw_values(market, spec)[:70])
    window = adjusted_window_prices(panel)
    markout = raw_values(panel, FeatureSpec(kind="window_markout", lookback=5))
    np.testing.assert_allclose(
        markout[-1], np.log(panel.adjusted_closes[-5:] / window[-5:]).mean(axis=0)
    )
    illiquid = raw_values(panel, FeatureSpec(kind="illiquidity", lookback=20))
    returns = np.log(panel.adjusted_closes[1:] / panel.adjusted_closes[:-1])
    dollars = panel.session_volumes[-20:] * panel.raw_closes[-20:]
    np.testing.assert_allclose(
        illiquid[-1], np.log1p((np.abs(returns[-20:]) * 1e8 / dollars).mean(axis=0))
    )


def policy_fixture(*, bad_recent=False):
    shape = (690, 3)
    gains = np.full(shape, 0.002)
    if bad_recent:
        gains[503:] = -0.004
    zeros = np.zeros(shape)
    outcomes = EntryOutcomes(zeros, zeros, gains, zeros, zeros)
    regimes = np.broadcast_to(np.arange(3), shape).copy()
    opportunity = np.ones(shape, dtype=bool)

    def block(a, b):
        predictions = np.broadcast_to([0.0, 100.0, 40.0, 20.0], (b - a, 3, 4)).copy()
        return ForecastBlock(a, b, predictions, np.zeros((b - a, 3)), [])

    validation = tuple(block(a, a + 63) for a in (311, 374, 437))
    forecast = EntryForecast(
        np.zeros((*shape, 9)), regimes, opportunity, outcomes, ((block(503, 566), validation),)
    )
    return forecast, validation


def test_policy_scores_missed_winners_and_requires_multiple_regime_folds():
    forecast, validation = policy_fixture()
    spec = EntryModelSpec()
    permitted, report = policy_evidence(forecast, validation, spec)
    assert permitted.all()
    gains = forecast.outcomes.deferral_advantage.copy()
    gains[311:500, 1] = -0.003
    # One winning period cannot establish regime support.
    gains[311:437, 2] = -0.001
    changed = replace(forecast, outcomes=replace(forecast.outcomes, deferral_advantage=gains))
    permitted, report = policy_evidence(changed, validation, spec)
    assert permitted.tolist() == [True, False, False]
    assert report[1]["folds"][0]["missed_favorable_entry_bps"] > 0
    assert report[1]["folds"][0]["gain_bps"] == pytest.approx(-30.0)


def test_uncertainty_and_expected_carry_penalize_deferral():
    spec = EntryModelSpec()
    predictions = np.array([[[600.0, 100.0, 20.0, -20.0]]])
    assert timing_utility(predictions, spec)[0, 0] == pytest.approx(10.0)
    without_risk = spec.model_copy(update={"adverse_weight": 0.0})
    assert timing_utility(predictions, without_risk)[0, 0] == pytest.approx(0.0)
    assert bounded_deferrals(np.ones((6, 1), dtype=bool), 1).ravel().tolist() == [True, False] * 3


def test_budget_drift_missing_and_recent_missed_winners_restore_baseline(program):
    forecast, _ = policy_fixture()
    trace = apply_entry_policy(forecast, EntryModelSpec(), program)
    np.testing.assert_array_equal(trace.increase_multipliers[503:507, 0], [0.5, 1, 0.5, 1])
    assert trace.reasons[504, 0] == "deferral_budget"
    main, validation = forecast.blocks[0]
    predictions, z = main.predictions.copy(), main.feature_z.copy()
    predictions[0, 0] = np.nan
    z[0, 1] = 10
    changed = replace(
        forecast, blocks=((replace(main, predictions=predictions, feature_z=z), validation),)
    )
    changed_trace = apply_entry_policy(changed, EntryModelSpec(), program)
    assert changed_trace.reasons[503].tolist() == [
        "insufficient_model",
        "feature_drift",
        "entry_reduced",
    ]
    np.testing.assert_array_equal(changed_trace.increase_multipliers[503], [1, 1, 0.5])
    bad, _ = policy_fixture(bad_recent=True)
    bad_trace = apply_entry_policy(bad, EntryModelSpec(), program)
    assert bad_trace.reasons[530, 0] == "recent_opportunity_cost"
    assert bad_trace.increase_multipliers[530, 0] == 1
    disabled = apply_entry_policy(bad, EntryModelSpec(recent_health=False), program)
    assert np.any(disabled.increase_multipliers[530:540] < 1)


def test_trained_forecasts_and_policy_are_prefix_invariant(market, program):
    opportunities = np.ones(market.adjusted_closes.shape, dtype=bool)
    spec = EntryModelSpec()
    short = prefix(market, 610)
    earlier = build_entry_forecast(short, program, spec, opportunities[:610])
    full = build_entry_forecast(market, program, spec, opportunities)
    left, right = (
        apply_entry_policy(earlier, spec, program),
        apply_entry_policy(full, spec, program),
    )
    np.testing.assert_array_equal(left.predictions, right.predictions[:610])
    np.testing.assert_array_equal(left.increase_multipliers, right.increase_multipliers[:610])
    np.testing.assert_array_equal(left.reasons, right.reasons[:610])
    assert np.isfinite(left.predictions[503:]).all(), "Actual fitted heads must be exercised."
    for main, validation in full.blocks:
        for block in (main, *validation):
            for fit in block.fits:
                if fit["latest_label_session"]:
                    assert fit["latest_label_session"] < fit["prediction_start_session"]
                assert fit["training_stop_exclusive"] == max(
                    0, block.start - fit["label_offset_sessions"] - 1
                )
        assert all(block.stop - 1 + 2 < main.start - 1 for block in validation)
    # A future extreme cannot change the training distribution or past predictions.
    altered = market.adjusted_closes.copy()
    altered[610:] *= 10
    mutated = build_entry_forecast(
        replace(market, adjusted_closes=altered), program, spec, opportunities
    )
    third = apply_entry_policy(mutated, spec, program)
    np.testing.assert_array_equal(right.predictions[:610], third.predictions[:610])
    np.testing.assert_array_equal(
        right.increase_multipliers[:610], third.increase_multipliers[:610]
    )


def test_simulation_caches_forecasts_for_ablations_and_persists_reasons(
    base, market, program, tmp_path
):
    candidate = Candidate(config=base.config, entry_model=EntryModelSpec())
    simulation = Simulation(market, program, None)
    first = simulation.entry_model(candidate)
    for _, alternative in ablations(candidate) + neighbors(candidate):
        simulation.overlay(alternative)
    assert len(simulation.entry_forecasts) == 1
    assert np.all(simulation.overlay(candidate).risk_multipliers == 1)
    assert np.all(simulation.overlay(candidate).allow_increase)
    assert simulation.entry_model(candidate) is first
    persist_entry_model(tmp_path, simulation, candidate)
    report = json.loads((tmp_path / "entry-model.json").read_text())
    assert report["execution_authority"] == "none"
    arrays = np.load(tmp_path / "entry-model.npz", allow_pickle=False)
    np.testing.assert_array_equal(arrays["reasons"], first.reasons)
    feedback = entry_feedback(simulation, candidate)
    assert feedback["reduced_asset_sessions"] == report["reduced_asset_sessions"]
    assert len(feedback["regime_evidence"]) == 3
    assert "predictions" not in feedback and "outcomes" not in feedback
    assert entry_feedback(simulation, base) is None


def test_proposer_receives_entry_failure_evidence_without_label_arrays():
    from autoresearch.proposer import training_evidence

    feedback = {
        "status": "inactive",
        "reduced_asset_sessions": 0,
        "reason_counts": {"insufficient_regime_evidence": 50},
    }
    evidence = training_evidence(
        {
            "candidate": "a" * 64,
            "parent": "b" * 64,
            "status": "discard",
            "proposal": json.dumps({"entry_model": EntryModelSpec().model_dump(mode="json")}),
            "result": json.dumps({"entry_quality": feedback}),
        }
    )
    assert evidence["entry_quality_evidence"] == feedback
    assert evidence["entry_model"]["max_entry_reduction"] == 0.5


@pytest.mark.parametrize("lag", [1, 2, 3])
@pytest.mark.parametrize("partial", [False, True])
def test_entry_scaling_reaches_executor_and_preserves_sales(base, lag, partial):
    strategy = base.config.strategy.model_copy(
        update={
            "execution": base.config.strategy.execution.model_copy(
                update={"signal_to_execution_lag_sessions": lag}
            )
        }
    )
    panel = generate_panel(strategy.to_universe(), sessions=285, seed=61, trend_strength=0.4)
    if partial:
        panel = replace(panel, window_volumes=np.full_like(panel.window_volumes, 100.0))
    shape = panel.adjusted_closes.shape
    scores = np.zeros(shape)
    scores[257:261, 0] = 1.0
    multiplier = np.ones(shape)
    multiplier[257] = 0.5
    overlay = DecisionOverlay(np.ones(shape), np.ones(shape, dtype=bool), multiplier)
    options = {
        "signal_override": scores,
        "max_entry_window_participation": 0.01 if partial else None,
    }
    control = run_backtest(strategy, panel, strategy.to_universe(), **options)
    scaled = run_backtest(
        strategy, panel, strategy.to_universe(), decision_overlay=overlay, **options
    )
    buys = [f for r in scaled.sessions for f in r.fills if f.side == Side.BUY]
    original = [f for r in control.sessions for f in r.fills if f.side == Side.BUY]
    sells = [f for r in scaled.sessions for f in r.fills if f.side == Side.SELL]
    assert buys and sells
    if not partial:
        assert float(buys[0].quantity / original[0].quantity) == pytest.approx(0.5, abs=0.01)
    else:
        assert all(float(f.quantity) <= 1 for f in buys)
    assert buys[0].execution_session == panel.sessions[257 + lag]
    assert all(r.cash >= 0 for r in scaled.sessions)
    assert not scaled.warnings
