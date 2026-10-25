"""Asset eligibility, leakage, model integration and constrained execution evidence."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path

import numpy as np
import pytest

from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from amat.domain.types import Side
from autoresearch.contracts import Candidate, Program, Proposal, apply_proposal, validate_candidate
from autoresearch.experiments import Simulation
from autoresearch.registry import raw_values
from autoresearch.selection import select_assets
from autoresearch.signals import SignalTrace
from autoresearch.specs import EvaluationSpec, FeatureSpec, SelectionSpec
from autoresearch.variants import ablations, neighbors

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def base():
    return Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)


@pytest.fixture(scope="module")
def candidate(base):
    proposal = Proposal.model_validate_json(
        (ROOT / "configs/autoresearch/adaptive-ml.example.json").read_text()
    )
    return apply_proposal(base, proposal)


@pytest.fixture(scope="module")
def market(base):
    panel = generate_panel(base.config.strategy.to_universe(), sessions=800)
    shape = panel.adjusted_closes.shape
    return replace(
        panel,
        source="scenario(adaptive-test)",
        adjusted_highs=panel.adjusted_closes * 1.01,
        adjusted_lows=panel.adjusted_closes * 0.985,
        session_volumes=np.full(shape, 1_000_000.0),
        window_volumes=np.full(shape, 100_000.0),
    )


@pytest.fixture(scope="module")
def program(base, market):
    return Program(
        name="adaptive-test",
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


def select(panel, scores, **options):
    return select_assets(
        panel,
        scores,
        SelectionSpec(**options),
        clusters=tuple("cluster" + str(i // 3) for i in range(len(panel.symbols))),
        allow_increase=np.ones(scores.shape, dtype=bool),
    )


def test_close_location_formula_flat_and_invalid_candles(market):
    panel = prefix(market, 15)
    prices = np.full_like(panel.adjusted_closes, 102.0)
    panel = replace(
        panel, adjusted_closes=prices, adjusted_highs=prices + 1, adjusted_lows=prices - 3
    )
    spec = FeatureSpec(kind="close_location", lookback=5)
    actual = raw_values(panel, spec)
    assert np.isnan(actual[:4]).all()
    np.testing.assert_allclose(actual[4:], 0.5)
    flat = replace(panel, adjusted_highs=prices, adjusted_lows=prices)
    np.testing.assert_allclose(raw_values(flat, spec)[4:], 0)
    highs = panel.adjusted_highs.copy()
    highs[7, 0] = 101
    invalid = raw_values(replace(panel, adjusted_highs=highs), spec)
    assert np.isnan(invalid[7:12, 0]).all()
    assert np.isfinite(invalid[12, 0])


def test_liquidity_is_raw_dollars_and_excludes_current_from_relative_baseline(market):
    panel = prefix(market, 30)
    volume = panel.session_volumes.copy()
    volume[-1] *= 2
    panel = replace(panel, session_volumes=volume)
    trace = select(panel, np.full_like(panel.adjusted_closes, 0.7))
    np.testing.assert_allclose(trace.relative_volume[-1], 2)
    expected = np.minimum(
        (volume[-21:-1] * panel.raw_closes[-21:-1]).mean(axis=0),
        volume[-1] * panel.raw_closes[-1],
    )
    np.testing.assert_allclose(trace.dollar_volume[-1], expected)
    assert not trace.eligible[:20].any()


@pytest.mark.parametrize("value", [0.0, -1.0, np.nan, np.inf])
def test_invalid_liquidity_cannot_enter_even_with_zero_floors(market, value):
    panel = prefix(market, 30)
    volume = panel.window_volumes.copy()
    volume[-1, 0] = value
    trace = select(
        replace(panel, window_volumes=volume),
        np.full_like(panel.adjusted_closes, 0.7),
        min_dollar_volume=0,
        min_window_dollar_volume=0,
        min_relative_volume=0,
    )
    assert not trace.eligible[-1, 0]
    assert trace.reasons[-1, 0] == "missing_liquidity"
    assert trace.eligible[-1, 1:].all()
    json.dumps(trace.report(panel, 20), allow_nan=False)


def test_ranking_cluster_limit_retention_and_eligibility_override(market):
    panel = prefix(market, 30)
    scores = np.zeros_like(panel.adjusted_closes)
    scores[20:, :4] = [0.8, 0.79, 0.78, 0.7]
    scores[21:, 1] = 0.805
    trace = select(panel, scores, top_k=2, max_per_cluster=1, retention_bonus=0.02)
    assert set(np.flatnonzero(trace.selected[20])) == {0, 3}
    assert set(np.flatnonzero(trace.selected[21])) == {0, 3}
    assert trace.reasons[21, 1] == "cluster_limit"
    volume = panel.session_volumes.copy()
    volume[22, 0] = 10  # retention never bypasses liquidity
    changed = select(replace(panel, session_volumes=volume), scores, top_k=2, max_per_cluster=1)
    assert not changed.selected[22, 0]
    assert changed.selected[22, 1]
    tied = select(panel, np.full_like(scores, 0.7), top_k=1, max_per_cluster=30)
    assert panel.symbols[np.flatnonzero(tied.selected[-1])[0]] == min(panel.symbols)


def test_rejected_best_asset_does_not_take_a_slot(market):
    scores = np.full_like(market.adjusted_closes, 0.7)
    scores[:, 0] = 0.99
    allowed = np.ones(scores.shape, dtype=bool)
    allowed[:, 0] = False
    trace = select_assets(
        market,
        scores,
        SelectionSpec(top_k=1),
        clusters=tuple(str(s) for s in market.symbols),
        allow_increase=allowed,
    )
    assert not trace.selected[:, 0].any()
    assert trace.selected[20:].sum(axis=1).min() == 1
    assert trace.reasons[-1, 0] == "existing_entry_gate"


def test_model_selection_and_fits_are_prefix_invariant(market, program, candidate):
    first = Simulation(market, program, None)
    original = first.selection(candidate)
    changed = {}
    for field in fields(market):
        array = getattr(market, field.name)
        if isinstance(array, np.ndarray):
            array = array.copy()
            array[700:] *= np.linspace(3, 8, 100)[:, None]
            changed[field.name] = array
    future = Simulation(replace(market, **changed), program, None)
    truncated = Simulation(prefix(market, 700), program, None)
    for field in fields(original):
        a = getattr(original, field.name)[:700]
        b = getattr(future.selection(candidate), field.name)[:700]
        c = getattr(truncated.selection(candidate), field.name)
        if a.dtype.kind == "U":
            np.testing.assert_array_equal(a, b)
            np.testing.assert_array_equal(a, c)
        else:
            np.testing.assert_allclose(a, b, equal_nan=True)
            np.testing.assert_allclose(a, c, equal_nan=True)
    short_fits = truncated.trace(candidate).fits
    assert first.trace(candidate).fits[: len(short_fits)] == short_fits
    for fit in first.trace(candidate).fits:
        assert fit["latest_label_exit"] < fit["prediction_start"] - 1
    assert np.isfinite(original.scores).all()
    assert not original.selected[:503].any()


def test_selection_controls_and_research_contract(base, candidate):
    validate_candidate(base, candidate)
    controls = dict(ablations(candidate))
    assert controls["selection_disabled"].signal.selection is None
    assert controls["selection_disabled"].signal.terms == candidate.signal.terms
    assert controls["selection_no_retention"].signal.selection.retention_bonus == 0
    assert any(name.startswith("nearby_selection_") for name, _ in neighbors(candidate))
    for _, control in (*ablations(candidate), *neighbors(candidate)):
        validate_candidate(base, control)
    with pytest.raises(ValueError, match="minimum relative volume"):
        SelectionSpec(min_relative_volume=3, max_relative_volume=2)


def test_next_session_fills_capacity_and_exit_after_liquidity_loss(market, program, candidate):
    panel = prefix(market, 560)
    volumes = panel.session_volumes.copy()
    volumes[525:, 0] = 0
    windows = panel.window_volumes.copy()
    windows[:] = 100  # every buy is limited to one share by the frozen 1% cap
    panel = replace(panel, session_volumes=volumes, window_volumes=windows)
    payload = candidate.model_dump(mode="json")
    payload["signal"]["selection"].update(min_window_dollar_volume=0, top_k=1, mode="portfolio")
    candidate = Candidate.model_validate(payload)
    simulation = Simulation(panel, program, None)
    scores = np.zeros_like(panel.adjusted_closes)
    scores[503:, 0] = 0.8
    simulation.traces[candidate.digest()] = SignalTrace(
        scores,
        scores,
        np.empty((*scores.shape, 0)),
        scores,
        [],
        [],
    )
    result = simulation.run(candidate)
    fills = [(row.session, fill) for row in result.sessions for fill in row.fills]
    buys = [(session, fill) for session, fill in fills if fill.side == Side.BUY]
    sells = [(session, fill) for session, fill in fills if fill.side == Side.SELL]
    assert buys and sells
    assert min(session for session, _ in buys) > panel.sessions[503]
    assert max(session for session, _ in buys) <= panel.sessions[525]
    assert all(float(fill.quantity) <= 1 for _, fill in buys)
    assert any(session > panel.sessions[525] for session, _ in sells)
    assert any(
        str(miss.reason) == "participation_cap" for row in result.sessions for miss in row.missed
    )
    assert not result.warnings


def test_entry_selection_preserves_holdings_until_the_signal_exits(market, program, candidate):
    panel = prefix(market, 560)
    volumes = panel.session_volumes.copy()
    volumes[525:, 0] = 0
    panel = replace(panel, session_volumes=volumes, window_volumes=np.full_like(volumes, 100))
    payload = candidate.model_dump(mode="json")
    payload["signal"]["selection"].update(min_window_dollar_volume=0, top_k=1, mode="entries")
    candidate = Candidate.model_validate(payload)
    simulation = Simulation(panel, program, None)
    scores = np.zeros_like(panel.adjusted_closes)
    scores[503:540, 0] = 0.8
    simulation.traces[candidate.digest()] = SignalTrace(
        scores,
        scores,
        np.empty((*scores.shape, 0)),
        scores,
        [],
        [],
    )
    result = simulation.run(candidate)
    buys = [row.session for row in result.sessions for fill in row.fills if fill.side == Side.BUY]
    sells = [row.session for row in result.sessions for fill in row.fills if fill.side == Side.SELL]
    assert buys and sells
    assert max(buys) <= panel.sessions[525]
    assert min(sells) == panel.sessions[541]
    assert not simulation.selection(candidate).selected[525:].any()
    assert not result.warnings
