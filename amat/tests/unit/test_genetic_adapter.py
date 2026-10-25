"""Opt-in candle intervention, compatibility, causality and native replay parity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from amat.data.synthetic import generate_panel
from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate, Program, apply_proposal, validate_candidate
from autoresearch.experiments import ENTRY_PARTICIPATION_CAP, Simulation
from autoresearch.genetic.adapter import GeneticAdapterSpec, apply_genetic_pattern
from autoresearch.genetic.models import canonical_genome_digest, parse_pattern
from autoresearch.signals import build_signal
from autoresearch.specs import FeatureSpec, RouterSpec, SignalSpec, SignalTerm
from autoresearch.variants import ablations, proposal_for, replace_signal
from autoresearch.worker import complexity

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def spec():
    payload = json.loads((ROOT / "docs/genetic-patterns/candle.example.json").read_text())
    payload["genome"]["rules"] = [
        {"left": {"field": "open", "lag": 0}, "op": "lt", "right": {"field": "close", "lag": 0}}
    ]
    pattern = parse_pattern(payload)
    return GeneticAdapterSpec(pattern=pattern, genome_sha256=canonical_genome_digest(pattern))


@pytest.fixture(scope="module")
def base():
    return Candidate(
        config=load_research_config(ROOT / "configs/strategy/amat_v13_return_target.toml")
    )


@pytest.fixture(scope="module")
def panel(base):
    market = generate_panel(base.config.strategy.to_universe(), sessions=650)
    opens = market.adjusted_closes.copy()
    opens[::2] *= 0.999
    opens[1::2] *= 1.001
    return replace(
        market,
        source="scenario(genetic-test)",
        adjusted_opens=opens,
        adjusted_highs=market.adjusted_closes * 1.01,
        adjusted_lows=market.adjusted_closes * 0.99,
        session_volumes=np.full_like(opens, 1e7),
    )


@pytest.fixture(scope="module")
def program(base, panel):
    return Program(
        schema_version="strategy-development/8",
        name="genetic-adapter-test",
        baseline=base,
        load_start=panel.sessions[0],
        measured_start=panel.sessions[503],
        train_end=panel.sessions[-2],
        validation_end=panel.sessions[-1],
        frozen_files={},
        dependencies={},
    )


def attach(base, spec):
    return Candidate.model_validate({**base.model_dump(), "genetic_pattern": spec})


def test_hash_binding_and_unsupported_contracts(spec):
    with pytest.raises(ValidationError, match="hash mismatch"):
        GeneticAdapterSpec(pattern=spec.pattern, genome_sha256="a" * 64)
    for key, value in (("timeframe", "1h"), ("priceBasis", "spot_raw")):
        payload = spec.pattern.model_dump(by_alias=True)
        payload[key] = value
        pattern = parse_pattern(payload)
        with pytest.raises(ValidationError, match="daily"):
            GeneticAdapterSpec(pattern=pattern, genome_sha256=canonical_genome_digest(pattern))
    payload = spec.pattern.model_dump(by_alias=True)
    payload["binding"]["role"] = "entry_filter"
    pattern = parse_pattern(payload)
    with pytest.raises(ValidationError, match="feature binding"):
        GeneticAdapterSpec(pattern=pattern, genome_sha256=canonical_genome_digest(pattern))


def test_legacy_candidate_digest_and_proposal_round_trip(base, spec):
    legacy = base.model_dump(mode="json")
    assert "genetic_pattern" not in legacy
    canonical = json.dumps(legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert base.digest() == hashlib.sha256(canonical.encode()).hexdigest()
    candidate = attach(base, spec)
    assert candidate.digest() != base.digest()
    proposal = proposal_for(
        base, candidate, hypothesis="Test incremental candle contribution.", stage="manual"
    )
    assert apply_proposal(base, proposal) == candidate
    validate_candidate(base, candidate)
    assert replace_signal(candidate, None).genetic_pattern == spec
    assert complexity(candidate) - complexity(base) == 1
    disabled = dict(ablations(candidate))["genetic_pattern_disabled"]
    assert disabled == base
    assert disabled.digest() == base.digest()


def test_centered_bounded_addition_preserves_inactive_and_missing(panel, spec):
    scores = np.full_like(panel.adjusted_closes, 0.5)
    scores[4] = 0.0
    scores[5] = 0.1
    scores[6] = 0.9
    opens = panel.adjusted_opens.copy()
    opens[7] = np.nan
    changed, matches = apply_genetic_pattern(replace(panel, adjusted_opens=opens), scores, spec)
    np.testing.assert_equal(changed[0], 0.75)
    np.testing.assert_equal(changed[1], 0.25)
    np.testing.assert_equal(changed[4], 0)
    np.testing.assert_equal(changed[5], 0)
    np.testing.assert_equal(changed[6], 1)
    np.testing.assert_equal(changed[7], scores[7])
    assert np.isnan(matches[7]).all()
    assert np.max(np.abs(changed - scores)) <= spec.pattern.binding.max_effect


def test_signal_prefix_and_future_invariance(base, panel, program, spec):
    candidate = attach(base, spec)
    trace = build_signal(panel, candidate, program)
    stop = 600
    fields = (
        "adjusted_opens",
        "adjusted_highs",
        "adjusted_lows",
        "adjusted_closes",
        "raw_closes",
        "window_vwaps",
        "window_volumes",
        "session_volumes",
    )
    prefix = replace(
        panel,
        sessions=panel.sessions[:stop],
        **{name: getattr(panel, name)[:stop] for name in fields},
    )
    shorter = build_signal(prefix, candidate, program)
    np.testing.assert_equal(trace.scores[:stop], shorter.scores)
    changes = {}
    for name in fields:
        array = getattr(panel, name).copy()
        array[stop:] *= 3
        changes[name] = array
    corrupted = build_signal(replace(panel, **changes), candidate, program)
    np.testing.assert_equal(trace.scores[:stop], corrupted.scores[:stop])
    assert np.isfinite(trace.scores).all()
    assert ((trace.scores >= 0) & (trace.scores <= 1)).all()


def test_program_version_opt_in_applies_to_candidate(base, panel, program, spec):
    old = Program.model_validate(
        {**program.model_dump(), "schema_version": "strategy-development/7"}
    )
    with pytest.raises(ValueError, match="strategy-development/8"):
        build_signal(panel, attach(base, spec), old)
    with pytest.raises(ValidationError, match="strategy-development/8"):
        Program.model_validate({**old.model_dump(), "baseline": attach(base, spec)})


def test_router_still_works_but_combination_rejected(spec):
    v6 = Candidate(config=load_research_config(ROOT / "configs/strategy/amat_v6.toml"))
    terms = tuple(
        SignalTerm(feature=FeatureSpec(kind=name))
        for name in ("momentum", "rsi", "efficiency", "volatility_shock")
    )
    router = Candidate(
        config=v6.config, signal=SignalSpec(terms=terms, router=RouterSpec(stress_term=3))
    )
    with pytest.raises(ValidationError, match="routers"):
        attach(router, spec)


def test_weighted_signal_extension_preserves_policy_zeros(panel, program, spec):
    v6 = Candidate(config=load_research_config(ROOT / "configs/strategy/amat_v6.toml"))
    # Use the same market universe as v6 because group lookup is contract-bound.
    market = generate_panel(v6.config.strategy.to_universe(), sessions=len(panel))
    market = replace(
        market,
        adjusted_opens=market.adjusted_closes * 0.999,
        adjusted_highs=market.adjusted_closes * 1.01,
        adjusted_lows=market.adjusted_closes * 0.99,
        session_volumes=np.full_like(market.adjusted_closes, 1e7),
    )
    weighted = Candidate(
        config=v6.config,
        signal=SignalSpec(terms=(SignalTerm(feature=FeatureSpec(kind="momentum", lookback=21)),)),
    )
    native = build_signal(market, weighted, program)
    extension = build_signal(market, attach(weighted, spec), program)
    np.testing.assert_equal(extension.scores[native.scores == 0], 0)
    assert np.max(np.abs(extension.scores - native.scores)) <= 0.25 + np.finfo(float).eps
    np.testing.assert_equal(extension.raw_scores, native.raw_scores)
    assert extension.fits == native.fits


def test_v13_native_cross_sectional_missing_and_removal_replay_parity(
    base, panel, program, spec, monkeypatch
):
    assert base.config.strategy.cross_sectional.enabled
    missing = replace(panel, adjusted_opens=np.full_like(panel.adjusted_closes, np.nan))
    simulation = Simulation(missing, program, None)
    candidate = attach(base, spec)
    # Exercise authoritative accounting with existing native cross-sectional inputs.
    expected = simulation.run(base)
    actual = simulation.run(candidate)
    removed = simulation.run(dict(ablations(candidate))["genetic_pattern_disabled"])
    for observed in (actual, removed):
        for left, right in zip(observed.sessions, expected.sessions, strict=True):
            np.testing.assert_equal(
                [left.forecast_volatility_pre, left.forecast_volatility_post],
                [right.forecast_volatility_pre, right.forecast_volatility_post],
            )
            assert replace(left, forecast_volatility_pre=0, forecast_volatility_post=0) == replace(
                right, forecast_volatility_pre=0, forecast_volatility_post=0
            )
    diagnostic = simulation.diagnostic_trace(candidate)
    assert diagnostic.predictors.shape[-1] == 1
    assert np.isnan(diagnostic.predictors).all()
    assert diagnostic.features[-1]["id"] == spec.genome_sha256
    from autoresearch import experiments

    original = experiments.run_backtest
    seen = []

    def recording(*args, **kwargs):
        seen.append(kwargs["max_entry_window_participation"])
        return original(*args, **kwargs)

    monkeypatch.setattr(experiments, "run_backtest", recording)
    simulation.run(base, cost=6)
    assert seen == [ENTRY_PARTICIPATION_CAP]
