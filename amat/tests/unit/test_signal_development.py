"""Signal formation, point-in-time fitting, diagnostic controls and search contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from autoresearch.contracts import Candidate, Program, apply_proposal
from autoresearch.diagnostics import correlation, feature_diagnostics, paired_uncertainty, rankdata
from autoresearch.labels import forward_returns, purged_stop
from autoresearch.registry import REGISTRY, feature_values, raw_values
from autoresearch.search import (
    choose_experiment,
    combinations,
    policy_variants,
    refinements,
    seed_proposals,
)
from autoresearch.signals import build_signal, fit_feature_transforms, policy_scores
from autoresearch.specs import (
    EvaluationSpec,
    FeatureSpec,
    Interaction,
    SignalSpec,
    SignalTerm,
    TradingPolicy,
    required_history,
)
from autoresearch.variants import ablations, neighbors

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def base():
    return Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)


@pytest.fixture(scope="module")
def market(base):
    panel = generate_panel(base.config.strategy.to_universe(), sessions=1100)
    rng = np.random.default_rng(99)
    return replace(
        panel,
        adjusted_highs=panel.adjusted_closes * 1.01,
        adjusted_lows=panel.adjusted_closes * 0.99,
        session_volumes=rng.uniform(10000, 100000, panel.adjusted_closes.shape),
    )


@pytest.fixture(scope="module")
def program(base, market):
    return Program(
        name="signal-test",
        load_start=market.sessions[0],
        measured_start=market.sessions[503],
        train_end=market.sessions[-2],
        validation_end=market.sessions[-1],
        baseline=base,
        frozen_files={},
        dependencies={},
        evaluation=EvaluationSpec(bootstrap_resamples=200),
    )


def prefix(panel, end):
    fields = (
        "adjusted_closes",
        "raw_closes",
        "window_vwaps",
        "window_volumes",
        "adjusted_highs",
        "adjusted_lows",
        "session_volumes",
    )
    return replace(
        panel, sessions=panel.sessions[:end], **{key: getattr(panel, key)[:end] for key in fields}
    )


def corrupt_future(panel, start):
    changes = {}
    for key in (
        "adjusted_closes",
        "raw_closes",
        "window_vwaps",
        "window_volumes",
        "adjusted_highs",
        "adjusted_lows",
        "session_volumes",
    ):
        values = getattr(panel, key).copy()
        values[start:] *= np.linspace(2, 20, len(panel) - start)[:, None]
        changes[key] = values
    return replace(panel, **changes)


@pytest.mark.parametrize("kind", list(REGISTRY))
def test_every_registered_formula_is_prefix_invariant(market, kind):
    spec = FeatureSpec(kind=kind, template=(1, 0, 1) if kind == "path_similarity" else None)
    before = raw_values(market, spec)
    after = raw_values(corrupt_future(market, 800), spec)
    truncated = raw_values(prefix(market, 800), spec)
    np.testing.assert_allclose(before[:800], after[:800], equal_nan=True)
    np.testing.assert_allclose(before[:800], truncated, equal_nan=True)
    assert np.isfinite(before[required_history(spec) :]).any()
    assert not np.isfinite(before[: required_history(spec)]).any()


@pytest.mark.parametrize(
    "transform",
    [
        "identity",
        "time_series_rank",
        "rolling_zscore",
        "cross_sectional_rank",
        "group_rank",
        "volatility_scaled",
        "train_zscore",
    ],
)
def test_transform_prefix_invariance_and_group_fallback(market, transform):
    spec = FeatureSpec(kind="normalized_momentum", transform=transform, smoothing=3)
    groups = tuple((i,) for i in range(len(market.symbols)))
    original = feature_values(market, spec, groups=groups)
    truncated = feature_values(prefix(market, 800), spec, groups=groups)
    np.testing.assert_allclose(original[:800], truncated, equal_nan=True)
    if transform == "group_rank":
        temporal = FeatureSpec.model_validate(
            {**spec.model_dump(), "transform": "time_series_rank"}
        )
        np.testing.assert_allclose(original, feature_values(market, temporal), equal_nan=True)


def test_feature_contract_rejects_unavailable_benchmark_and_excess_history(base):
    for feature in (
        FeatureSpec(kind="relative_strength", benchmark="NOTREAL"),
        FeatureSpec(kind="trend_acceleration", lookback=126, secondary_lookback=126, smoothing=21),
    ):
        with pytest.raises(ValueError):
            Candidate(config=base.config, signal=SignalSpec(terms=(SignalTerm(feature=feature),)))
    with pytest.raises(ValueError):
        SignalSpec(
            terms=(SignalTerm(feature=FeatureSpec(kind="momentum")),),
            interactions=(Interaction(left=0, right=1),),
        )


def test_forward_labels_use_executable_windows_and_mature_before_fit(market):
    panel = prefix(market, 12)
    prices = np.tile(np.arange(100.0, 112.0)[:, None], (1, len(panel.symbols)))
    panel = replace(panel, window_vwaps=prices, raw_closes=prices, adjusted_closes=2 * prices)
    labels = forward_returns(panel, 5)
    assert labels[0, 0] == pytest.approx(106 / 101 * (1 - 0.0002) / (1 + 0.0002) - 1)
    assert np.isnan(labels[6:]).all()
    volumes = panel.window_volumes.copy()
    volumes[6, 0] = 0
    broken = forward_returns(replace(panel, window_volumes=volumes), 5)
    assert np.isnan(broken[0, 0])
    assert np.isfinite(broken[0, 1])
    stop = purged_stop(100, 20)
    assert stop == 78
    assert stop - 1 + 20 + 1 < 100 - 1


def test_hysteresis_confirmation_minimum_hold_and_missing_exit():
    raw = np.array([0.7, 0.8, 0.1, 0.1, 0.1, 0.8, 0.8, 0.8])[:, None]
    available = np.ones(raw.shape, bool)
    available[-1] = False
    policy = TradingPolicy(confirmation_sessions=2, min_hold_sessions=3)
    actual = policy_scores(raw, available, policy)
    np.testing.assert_allclose(actual[:, 0], [0, 0.8, 0.8, 0.8, 0, 0, 0.8, 0])


@pytest.mark.parametrize("learner", ["weighted", "ridge"])
def test_signal_fit_and_predictions_cannot_read_future(market, program, base, learner):
    signal = SignalSpec(
        learner=learner,
        horizon=60,
        terms=(
            SignalTerm(feature=FeatureSpec(kind="normalized_momentum", transform="train_zscore")),
            SignalTerm(
                feature=FeatureSpec(kind="downside_variance_share"),
                weight=-1,
                center=0.5,
                scale=0.25,
            ),
        ),
        interactions=(Interaction(left=0, right=1),),
    )
    candidate = Candidate(config=base.config, signal=signal)
    first = build_signal(market, candidate, program)
    changed = build_signal(corrupt_future(market, 800), candidate, program)
    truncated = build_signal(prefix(market, 800), candidate, program)
    np.testing.assert_allclose(first.scores[:800], changed.scores[:800])
    np.testing.assert_allclose(first.scores[:800], truncated.scores)
    np.testing.assert_allclose(first.raw_scores[:800], truncated.raw_scores)
    assert np.isfinite(first.scores).all()
    assert np.all((first.scores >= 0) & (first.scores <= 1))
    for state in first.fits:
        assert (
            state["latest_label_exit"]
            < state["prediction_start"] - program.evaluation.embargo_sessions
        )
        if learner == "ridge":
            assert (
                state["inner"]["latest_label_exit"]
                < state["inner"]["validation_start"] - program.evaluation.embargo_sessions
            )
            assert state["model"]["training_rows"] > 0
            assert state["inner"]["tried"] == 6
    assert first.fits[: len(truncated.fits)] == truncated.fits


def test_train_scaler_ignores_later_values():
    values = np.arange(300.0).reshape(100, 3, 1)
    signal = SignalSpec(
        terms=(SignalTerm(feature=FeatureSpec(kind="momentum", transform="train_zscore")),)
    )
    _, state = fit_feature_transforms(values, signal, 0, 50)
    changed = values.copy()
    changed[50:] *= 10000
    _, updated = fit_feature_transforms(changed, signal, 0, 50)
    assert state == updated
    np.testing.assert_allclose(state[0]["mean"], values[:50, :, 0].mean(axis=0))


def test_continuous_signal_can_enter_when_core_trend_is_zero(market, base, program):
    # A reversed price trajectory makes the fixed core trend long score zero;
    # a bounded contrarian rule can still express a research entry.
    prices = (
        200 * np.exp(-np.arange(len(market))[:, None] * 0.001) * np.ones((1, len(market.symbols)))
    )
    panel = replace(market, adjusted_closes=prices)
    candidate = Candidate(
        config=base.config,
        signal=SignalSpec(
            terms=(SignalTerm(feature=FeatureSpec(kind="normalized_momentum"), weight=-1),)
        ),
    )
    trace = build_signal(panel, candidate, program)
    assert np.all(trace.baseline[600:] == 0)
    assert np.all(trace.scores[600:] > 0)


def test_rank_ties_and_degenerate_correlations():
    np.testing.assert_allclose(rankdata(np.array([5.0, 1.0, 1.0, 3.0])), [4, 1.5, 1.5, 3])
    assert correlation(np.ones(10), np.arange(10)) is None
    assert correlation(np.arange(10), np.arange(10), rank=True) == pytest.approx(1)


def test_bootstrap_rejects_identical_series_and_detects_consistent_advantage(program):
    returns = np.random.default_rng(5).normal(0.0001, 0.005, 800)
    same = paired_uncertainty(returns, returns, program)
    assert same["lower_sharpe_difference"] == 0
    assert not same["passes"]
    stronger = paired_uncertainty(returns + 0.002, returns, program)
    assert stronger["passes"]
    assert stronger["tail_probability"] == pytest.approx(0.1 / program.max_experiments)
    with pytest.raises(ValueError):
        paired_uncertainty(returns, returns[:-1], program)


def test_diagnostics_report_information_and_development_limits(market, program, base):
    candidate = Candidate(
        config=base.config,
        signal=SignalSpec(
            terms=(
                SignalTerm(feature=FeatureSpec(kind="normalized_momentum")),
                SignalTerm(feature=FeatureSpec(kind="volume_price")),
            )
        ),
    )
    trace = build_signal(market, candidate, program)
    result = feature_diagnostics(market, trace, program)
    assert result["evaluation_use"] == "adaptive_development"
    assert len(result["features"]) == 2
    assert len(result["redundancy"]) == 1
    feature = result["features"][0]
    assert feature["coverage"] == 1
    assert [row["horizon"] for row in feature["horizons"]] == [5, 20, 60]
    assert len(feature["horizons"][0]["assets"]) == len(market.symbols)
    assert (
        feature["horizons"][0]["bucket_edges_from_initial_training"]
        == trace.fits[0]["feature_quantiles"][0]
    )
    json.dumps(result, allow_nan=False)


def test_search_has_all_stages_and_revalidates_mutations(base):
    seeds = seed_proposals()
    assert len(seeds) == 10
    assert any(proposal.signal and proposal.signal.learner == "ridge" for proposal in seeds)
    candidates = [apply_proposal(base, proposal) for proposal in seeds]
    parent = candidates[1]
    variants = refinements(parent) + combinations(parent, candidates[2:4]) + policy_variants(parent)
    assert any(item.signal.interactions for item in variants if item.signal)
    assert any(item.signal.policy.confirmation_sessions > 1 for item in variants if item.signal)
    assert any(
        item.signal.terms[0].feature.transform == "train_zscore" for item in variants if item.signal
    )
    combined = combinations(parent, candidates[2:4])[0]
    assert len(ablations(combined)) == 2
    assert len(neighbors(combined)) == 2
    for candidate in variants:
        assert candidate.config == base.config


def test_screening_is_not_dependent_on_incumbent_improvements(tmp_path, program, base):
    history = [{"candidate": base.digest(), "status": "baseline", "parent": "", "result": None}]
    for _ in range(10):
        parent, proposal = choose_experiment(tmp_path, program, base, history)
        assert parent == base
        assert proposal.stage == "screen"
        candidate = apply_proposal(parent, proposal)
        assert candidate.digest() not in {row["candidate"] for row in history}
        history.append(
            {
                "candidate": candidate.digest(),
                "status": "discard",
                "parent": base.digest(),
                "result": None,
            }
        )


def test_frontier_is_diverse_and_checks_prediction_hashes(tmp_path, base, program, market):
    from autoresearch.search import frontier
    from autoresearch.workspace import digest_file, write_json

    pattern = np.random.default_rng(9).uniform(0, 1, (len(market), len(market.symbols)))
    history = []
    for index, (kind, scores) in enumerate(
        (
            ("normalized_momentum", pattern),
            ("volume_price", pattern),
            ("relative_strength", 1 - pattern),
        )
    ):
        candidate = Candidate(
            config=base.config,
            signal=SignalSpec(terms=(SignalTerm(feature=FeatureSpec(kind=kind)),)),
        )
        write_json(
            tmp_path / "candidates" / f"{candidate.digest()}.json",
            candidate.model_dump(mode="json"),
        )
        directory = tmp_path / "outputs/runs" / candidate.digest()
        directory.mkdir(parents=True)
        path = directory / "signals.npz"
        np.savez_compressed(
            path, effective_scores=scores, sessions=np.array([str(s) for s in market.sessions])
        )
        result = {
            "score": -index * 0.1,
            "failed_gates": [],
            "artifact_hashes": {str(path.relative_to(tmp_path)): digest_file(path)},
        }
        history.append(
            {"candidate": candidate.digest(), "status": "discard", "result": json.dumps(result)}
        )
    pool = frontier(tmp_path, program, history)
    assert [item.signal.terms[0].feature.kind for item in pool] == [
        "normalized_momentum",
        "relative_strength",
    ]
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        frontier(tmp_path, program, history)


def test_staged_search_combines_nonwinning_signals(tmp_path, program, base):
    from autoresearch.workspace import write_json

    history = [{"candidate": base.digest(), "status": "baseline", "parent": "", "result": None}]
    for proposal in seed_proposals():
        candidate = apply_proposal(base, proposal)
        write_json(
            tmp_path / "candidates" / f"{candidate.digest()}.json",
            candidate.model_dump(mode="json"),
        )
        result = {"score": 0.5 if candidate.signal else 0.1, "failed_gates": [], "eligible": False}
        history.append(
            {
                "candidate": candidate.digest(),
                "status": "discard",
                "parent": base.digest(),
                "result": json.dumps(result),
            }
        )
    # Completed routing, pattern, fresh exploration and refinement advance to combinations. Both
    # component recipes lost; their frontier membership is still exploratory.
    history.append(
        {"candidate": "done-refining", "parent": base.digest(), "status": "crash", "result": None}
    )
    history.append(
        {"candidate": "done-patterns", "parent": base.digest(), "status": "crash", "result": None}
    )
    history.append(
        {"candidate": "done-routing", "parent": base.digest(), "status": "crash", "result": None}
    )
    history.append(
        {"candidate": "done-exploring", "parent": base.digest(), "status": "crash", "result": None}
    )
    parent, proposal = choose_experiment(tmp_path, program, base, history)
    assert proposal.stage == "combine"
    assert parent == base
    assert len(proposal.signal.terms) > 1


def test_screening_failures_do_not_consume_post_screen_stall_budget(tmp_path):
    from autoresearch.ledger import Ledger
    from autoresearch.loop import finish, register

    with Ledger(tmp_path, 10000) as ledger:
        row = register(ledger, "base", "", "baseline")
        finish(ledger, row, "baseline", {"score": 1})
        for stage in ("screen", "screen", "refine"):
            row = register(ledger, "candidate", "base", json.dumps({"stage": stage}))
            finish(ledger, row, "crash", error="fixture interruption")
    with Ledger(tmp_path, 10000) as ledger:
        assert ledger.get("stall") == 1


def test_joint_neighborhood_and_interaction_ablation(base):
    candidate = Candidate(
        config=base.config,
        signal=SignalSpec(
            terms=(
                SignalTerm(feature=FeatureSpec(kind="normalized_momentum", lookback=63)),
                SignalTerm(feature=FeatureSpec(kind="volume_price", lookback=21)),
            ),
            interactions=(Interaction(left=0, right=1),),
        ),
    )
    nearby = neighbors(candidate)
    assert [term.feature.lookback for term in nearby[0][1].signal.terms] == [50, 17]
    assert [term.feature.lookback for term in nearby[1][1].signal.terms] == [76, 25]
    interaction_removed = dict(ablations(candidate))["interaction_0"]
    assert interaction_removed.signal.terms == candidate.signal.terms
    assert not interaction_removed.signal.interactions


def test_model_context_is_bounded_and_contains_control_findings(program, base):
    from autoresearch.proposer import MAX_CONTEXT_BYTES, context

    proposal = seed_proposals()[-1]
    measurement = {
        "score": 0.5,
        "eligible": False,
        "train": {"sharpe": 0.7},
        "control_summary": {"measurements": {"signal_0": {"sharpe": 0.6}}},
        "diagnostics": {"features": []},
        "folds": [],
    }
    row = {
        "candidate": "c" * 64,
        "parent": base.digest(),
        "status": "discard",
        "proposal": proposal.canonical(),
        "result": json.dumps(measurement),
    }
    value = context(program, base, [row] * 30)
    assert len(json.dumps(value).encode()) <= MAX_CONTEXT_BYTES
    assert len(value["history"]) <= 8
    assert value["history"][-1]["control_deltas"]["signal_0"][
        "difference_from_primary"
    ] == pytest.approx(-0.1)
    assert "validation_end" not in json.dumps(value)


def test_model_noop_fails_over_to_an_executable_recipe(tmp_path, program, monkeypatch):
    import asyncio

    from autoresearch.contracts import Proposal
    from autoresearch.ledger import Ledger
    from autoresearch.proposer import model_proposal
    from llmrouter.chain import Chain, Router, Target
    from llmrouter.contracts import ChatResponse

    proposal = Proposal(
        hypothesis="Test an explicit momentum forecast.",
        signal=SignalSpec(terms=(SignalTerm(feature=FeatureSpec(kind="normalized_momentum")),)),
    )

    class Transport:
        def __init__(self, provider):
            self.provider = provider

        async def generate(self, request, *, model, api_key, base_url):
            return ChatResponse(
                provider=self.provider,
                model=model,
                input_tokens=10,
                output_tokens=5,
                text=(
                    Proposal(hypothesis="A narrative without a recipe change.").canonical()
                    if self.provider == "openai"
                    else proposal.canonical()
                ),
                error_class="ok",
            )

    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    monkeypatch.setenv("GEMINI_API_KEY", "fixture")
    router = Router(
        Chain(
            targets=(
                Target(provider="openai", model="first"),
                Target(provider="gemini", model="second"),
            )
        ),
        transports={"openai": Transport("openai"), "gemini": Transport("gemini")},
    )
    with Ledger(tmp_path, 100000) as ledger:
        result = asyncio.run(model_proposal(router, ledger, program, program.baseline))
        assert result == proposal
        responses = [
            json.loads(row[0])
            for row in ledger.db.execute("SELECT response FROM calls ORDER BY id")
        ]
        assert [response["error_class"] for response in responses] == [
            "malformed",
            "malformed",
            "ok",
        ]
        assert ledger.db.execute("SELECT SUM(charged) FROM calls").fetchone()[0] == 45


def test_empty_research_catalog_is_valid(tmp_path):
    from autoresearch.ledger import Ledger
    from autoresearch.loop import publish_catalog
    from portfolio_team.evidence import load_catalog

    with Ledger(tmp_path, 100000) as ledger:
        publish_catalog(tmp_path, ledger)
    catalog = load_catalog(tmp_path / "catalog.toml")
    assert catalog.runs == ()
    assert catalog.comparisons == ()


def test_sequence_recognizes_order_and_propagates_missing_observations():
    from autoresearch.patterns import pattern_values
    from autoresearch.specs import PatternCondition, PatternSpec

    signal = SignalSpec(
        terms=(
            SignalTerm(feature=FeatureSpec(kind="momentum"), weight=0),
            SignalTerm(feature=FeatureSpec(kind="volume_price"), weight=0),
        ),
        patterns=(
            PatternSpec(
                name="quiet_then_move",
                mode="sequence",
                conditions=(
                    PatternCondition(term=0, threshold=0.5, lag=2, window=2),
                    PatternCondition(term=1, threshold=0.5),
                ),
            ),
        ),
    )
    values = np.array([[1, 0], [1, 0], [0, 0], [0, 1], [1, 0], [1, 1]], dtype=float)[:, None, :]
    result = pattern_values(values, signal)[:, 0, 0]
    np.testing.assert_allclose(result, [np.nan, np.nan, np.nan, 1, 0, 0], equal_nan=True)
    values[0, 0, 0] = np.nan
    assert np.isnan(pattern_values(values, signal)[3, 0, 0])


@pytest.mark.parametrize(
    "predicate,threshold,expected",
    [
        ("rising", 0.5, [np.nan, 1, 0, 0]),
        ("falling", -0.5, [np.nan, 0, 1, 0]),
        ("above", 1.5, [0, 1, 0, 0]),
        ("below", 1.0, [1, 0, 1, 1]),
    ],
)
def test_pattern_predicates_use_feature_units(predicate, threshold, expected):
    from autoresearch.patterns import pattern_values
    from autoresearch.specs import PatternCondition, PatternSpec

    signal = SignalSpec(
        terms=(SignalTerm(feature=FeatureSpec(kind="momentum"), center=9, scale=10),),
        patterns=(
            PatternSpec(
                name="event",
                conditions=(PatternCondition(term=0, predicate=predicate, threshold=threshold),),
            ),
        ),
    )
    values = np.array([1, 2, 1, 1], dtype=float)[:, None, None]
    np.testing.assert_allclose(pattern_values(values, signal)[:, 0, 0], expected, equal_nan=True)


def test_pattern_contract_rejects_bad_references_order_and_excess_history(base):
    from autoresearch.specs import PatternCondition, PatternSpec

    with pytest.raises(ValueError, match="ordered"):
        PatternSpec(
            name="overlap",
            mode="sequence",
            conditions=(
                PatternCondition(term=0, lag=1),
                PatternCondition(term=0, window=2),
            ),
        )
    with pytest.raises(ValueError, match="missing term"):
        SignalSpec(
            terms=(SignalTerm(feature=FeatureSpec(kind="momentum")),),
            patterns=(PatternSpec(name="bad", conditions=(PatternCondition(term=1),)),),
        )
    with pytest.raises(ValueError, match="pattern history"):
        Candidate(
            config=base.config,
            signal=SignalSpec(
                terms=(
                    SignalTerm(
                        feature=FeatureSpec(
                            kind="momentum",
                            lookback=126,
                            transform="time_series_rank",
                            normalization_window=126,
                        )
                    ),
                ),
                patterns=(
                    PatternSpec(name="too_old", conditions=(PatternCondition(term=0, lag=1),)),
                ),
            ),
        )
    for template in [(0, 0, 0), (0, float("nan"), 1), (0, 4, 1)]:
        with pytest.raises(ValueError):
            FeatureSpec(kind="path_similarity", template=template)


def test_path_templates_are_level_scale_invariant_and_discriminate_shape():
    from autoresearch.registry import path_similarity

    path = np.array([1, 0.5, 0, -0.5, -1, 0, 1], dtype=float)
    closes = np.exp(np.column_stack((path, path * 2 + 4, -path)))
    similarity = path_similarity(closes, 6, tuple(path))
    np.testing.assert_allclose(similarity[-1], [1, 1, -1], atol=1e-10)
    assert not np.isfinite(similarity[:-1]).any()
    assert path_similarity(np.ones((7, 1)), 6, tuple(path))[-1, 0] == 0
    closes[0, 0] = np.nan
    assert np.isnan(path_similarity(closes, 6, tuple(path))[-1, 0])


@pytest.mark.parametrize("learner", ["weighted", "ridge"])
def test_pattern_forecasts_and_fits_are_prefix_invariant(market, program, base, learner):
    from autoresearch.inspirations import pattern_proposals

    candidate = apply_proposal(base, pattern_proposals()[0])
    candidate = candidate.model_copy(
        update={"signal": candidate.signal.model_copy(update={"learner": learner})}
    )
    before = build_signal(market, candidate, program)
    after = build_signal(corrupt_future(market, 800), candidate, program)
    truncated = build_signal(prefix(market, 800), candidate, program)
    for other in (after, truncated):
        np.testing.assert_allclose(before.scores[:800], other.scores[:800])
        np.testing.assert_allclose(
            before.pattern_scores[:800], other.pattern_scores[:800], equal_nan=True
        )
        assert [f for f in before.fits if f["prediction_start"] < 800] == [
            f for f in other.fits if f["prediction_start"] < 800
        ]


def test_lagged_features_shift_after_causal_normalization(market):
    spec = FeatureSpec(kind="momentum", lookback=21, transform="rolling_zscore")
    delayed = spec.model_copy(update={"lag_sessions": 5})
    values, result = feature_values(market, spec), feature_values(market, delayed)
    np.testing.assert_allclose(result[5:], values[:-5], equal_nan=True)
    assert not np.isfinite(result[:5]).any()
    assert required_history(delayed) == required_history(spec) + 5


def test_pattern_ablations_remap_conditions_and_test_timing(base):
    from autoresearch.inspirations import pattern_proposals

    candidate = apply_proposal(base, pattern_proposals()[0])
    controls = dict(ablations(candidate))
    assert controls["pattern_0"].signal is None
    assert controls["pattern_timing_0"].signal.patterns[0].mode == "all"
    assert all(c.lag == 0 for c in controls["pattern_timing_0"].signal.patterns[0].conditions)
    for index in (0, 1):
        reduced = controls[f"signal_{index}"].signal
        assert len(reduced.terms) == 1
        assert reduced.patterns[0].mode == "all"
        assert len(reduced.patterns[0].conditions) == 1
        assert reduced.patterns[0].conditions[0].term == 0


def test_agent_can_refine_retained_best_and_reject_other_parent(base):
    from autoresearch.contracts import Patch, Proposal
    from autoresearch.research import selected_parent

    parent = apply_proposal(
        base,
        Proposal(
            hypothesis="Change the exploratory deadband.",
            patches=(Patch(path="trend.deadband_abs", value=0.3),),
        ),
    )
    proposal = Proposal(
        hypothesis="Refine an exploratory parent's recipe.",
        parent_candidate=parent.digest(),
        patches=(Patch(path="trend.clip_abs", value=2),),
    )
    selected = selected_parent(proposal, parent, {parent.digest(): parent})
    candidate = apply_proposal(selected, proposal)
    assert candidate.config.strategy.trend.deadband_abs == 0.3
    assert candidate.config.strategy.trend.clip_abs == 2
    with pytest.raises(ValueError, match="current retained best"):
        selected_parent(proposal, base, {base.digest(): base})


def test_pattern_stage_is_available_without_a_previous_keep(tmp_path, base, program):
    seeds = seed_proposals()
    history = [{"candidate": base.digest(), "status": "baseline", "parent": "", "result": None}]
    history += [
        {
            "candidate": apply_proposal(base, p).digest(),
            "status": "discard",
            "parent": base.digest(),
            "result": None,
        }
        for p in seeds
    ]
    parent, proposal = choose_experiment(tmp_path, program, base, history)
    assert parent == base
    assert proposal.stage == "route"
    history.append(
        {
            "candidate": apply_proposal(parent, proposal).digest(),
            "status": "discard",
            "parent": parent.digest(),
            "result": None,
        }
    )
    parent, proposal = choose_experiment(tmp_path, program, base, history)
    assert proposal.stage == "pattern"
    assert proposal.research.mechanism
    assert proposal.research.falsification
    assert proposal.signal.patterns


def test_model_validation_uses_the_selected_retained_parent(tmp_path, base, program):
    import asyncio

    from autoresearch.contracts import Patch, Proposal
    from autoresearch.inspirations import pattern_proposals
    from autoresearch.ledger import Ledger
    from autoresearch.proposer import model_proposal
    from llmrouter.contracts import ChatResponse

    parent = apply_proposal(
        base,
        Proposal(
            hypothesis="Explore a different trend threshold.",
            patches=(Patch(path="trend.deadband_abs", value=0.3),),
        ),
    )
    proposal = pattern_proposals()[0].model_copy(update={"parent_candidate": parent.digest()})
    captured = []

    class Proposer:
        async def complete(self, request, budget, *, validate, repair=False):
            assert repair is True
            payload = json.loads(request.user)
            assert {p["candidate"] for p in payload["research_parents"]} == {
                base.digest(),
                parent.digest(),
            }
            assert "sequence" in payload["pattern_semantics"]
            captured.append(validate(proposal.canonical()))
            return ChatResponse(
                provider="gemini",
                model="fixture",
                input_tokens=0,
                output_tokens=0,
                error_class="ok",
                text=proposal.canonical(),
            )

    with Ledger(tmp_path, 100000) as ledger:
        chosen = asyncio.run(
            model_proposal(
                Proposer(), ledger, program, parent, {base.digest(): base, parent.digest(): parent}
            )
        )
    assert chosen == proposal
    assert captured[0].config.strategy.trend.deadband_abs == 0.3
    assert captured[0].signal.patterns


def test_combination_preserves_both_pattern_definitions_and_remaps_inputs(base):
    from autoresearch.inspirations import pattern_proposals

    first, second = [apply_proposal(base, proposal) for proposal in pattern_proposals()]
    combined = combinations(first, [second])[0]
    assert len(combined.signal.terms) == 4
    assert len(combined.signal.patterns) == 2
    assert [c.term for c in combined.signal.patterns[0].conditions] == [0, 1]
    assert [c.term for c in combined.signal.patterns[1].conditions] == [2, 3]
    for name, variant in ablations(combined):
        if name == "signal_0":
            assert len(variant.signal.patterns) == 2
            assert [c.term for c in variant.signal.patterns[1].conditions] == [1, 2]


def test_low_exposure_pattern_is_exploratory_without_waiving_promotion(tmp_path, program, base):
    from autoresearch.inspirations import pattern_proposals
    from autoresearch.search import frontier
    from autoresearch.workspace import write_json

    candidate = apply_proposal(base, pattern_proposals()[0])
    write_json(
        tmp_path / "candidates" / f"{candidate.digest()}.json", candidate.model_dump(mode="json")
    )
    result = {"score": 0.1, "eligible": False, "failed_gates": ["exposure"]}
    history = [
        {
            "candidate": candidate.digest(),
            "status": "discard",
            "parent": base.digest(),
            "result": json.dumps(result),
        }
    ]
    assert frontier(tmp_path, program, history) == [candidate]
    assert not result["eligible"]
    result["failed_gates"] = ["no_fills"]
    history[0]["result"] = json.dumps(result)
    assert frontier(tmp_path, program, history) == []
