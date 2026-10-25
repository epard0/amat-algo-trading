"""Frozen symbolic terminals, arithmetic, strict missingness and causal invariance."""

from __future__ import annotations

import datetime as dt
import operator
from dataclasses import replace
from pathlib import Path
from typing import get_args

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.models import FeatureName, FeatureNode, parse_pattern
from autoresearch.genetic.symbolic import (
    SYMBOLIC_REGISTRY_VERSION,
    SymbolicEvaluator,
    evaluate_symbolic,
    evaluate_symbolic_trace,
    symbolic_feature_sources,
    symbolic_registry_digest,
    symbolic_registry_manifest,
    symbolic_required_history,
)
from autoresearch.genetic.symbolic_registry import (
    CANDLE_FEATURES,
    raw_feature,
    raw_history,
    terminal_values,
)
from autoresearch.registry import raw_values
from autoresearch.specs import FeatureSpec

DOCS = Path(__file__).parents[2] / "docs/genetic-patterns"


@pytest.fixture(scope="module")
def panel():
    rng = np.random.default_rng(21)
    shape = (260, 3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, shape), axis=0))
    opens = closes * np.exp(rng.normal(0, 0.003, shape))
    highs = np.maximum(opens, closes) * 1.01
    lows = np.minimum(opens, closes) * 0.99
    return MarketPanel(
        sessions=tuple(dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(shape[0])),
        symbols=("SPY", "QQQ", "GLD"),
        adjusted_closes=closes,
        raw_closes=closes.copy(),
        window_vwaps=opens.copy(),
        window_volumes=np.full(shape, 1e5),
        source="symbolic-fixture",
        adjusted_opens=opens,
        adjusted_highs=highs,
        adjusted_lows=lows,
        session_volumes=rng.uniform(1e6, 2e6, shape),
    )


def leaf(name="momentum", **kwargs):
    return {
        "node": "feature",
        "name": name,
        "source": "self",
        "lookback": 21,
        "lag": 0,
        "transform": "time_series_rank",
        **kwargs,
    }


def const(value):
    return {"node": "constant", "value": value}


def binary(op, left, right):
    return {"node": "binary", "op": op, "left": left, "right": right}


def pattern(root):
    data = parse_pattern((DOCS / "symbolic.example.json").read_text()).model_dump(by_alias=True)
    data["genome"]["root"] = root
    return parse_pattern(data)


def prefix(panel, stop):
    arrays = {
        field: getattr(panel, field)[:stop]
        for field in (
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
    return replace(panel, sessions=panel.sessions[:stop], **arrays)


@pytest.mark.parametrize("name", get_args(FeatureName))
def test_every_feature_resolves_and_matches_available_history(panel, name):
    node = FeatureNode.model_validate(leaf(name))
    result = terminal_values(panel, node)
    first = symbolic_required_history(pattern(leaf(name)))
    assert np.isnan(result[:first]).all()
    assert np.isfinite(result[first:]).all()
    assert result.shape == panel.adjusted_closes.shape
    assert ((result[first:] >= 0) & (result[first:] <= 1)).all()
    if name not in CANDLE_FEATURES:
        existing = raw_values(panel, FeatureSpec(kind=name, lookback=21, secondary_lookback=5))
        np.testing.assert_allclose(
            raw_feature(panel, node)[raw_history(node) :],
            existing[raw_history(node) :],
            rtol=1e-10,
            atol=1e-12,
        )


@pytest.mark.parametrize("name", get_args(FeatureName))
def test_all_terminal_prefix_future_and_missing_causality(panel, name):
    expression = pattern(leaf(name, lag=2))
    before = evaluate_symbolic(expression, panel)
    np.testing.assert_equal(evaluate_symbolic(expression, prefix(panel, 190)), before[:190])
    close = panel.adjusted_closes.copy()
    close[190:] = np.nan
    np.testing.assert_equal(
        evaluate_symbolic(expression, replace(panel, adjusted_closes=close))[:190], before[:190]
    )
    close = panel.adjusted_closes.copy()
    close[120, 0] = np.nan
    missing = evaluate_symbolic(expression, replace(panel, adjusted_closes=close))
    # All terminals mask the complete raw dependency and normalization windows.
    assert np.isnan(missing[122, 0])
    np.testing.assert_equal(missing[:120], before[:120])


def test_rank_ties_zscore_zero_variance_and_missing_windows(panel):
    closes = np.full_like(panel.adjusted_closes, 100)
    flat = replace(panel, adjusted_closes=closes)
    ranked = evaluate_symbolic(pattern(leaf()), flat)
    np.testing.assert_equal(ranked[83:], np.tanh(0.5))
    zscore = evaluate_symbolic(pattern(leaf(transform="rolling_zscore")), flat)
    assert np.isnan(zscore).all()
    dirty = closes.copy()
    dirty[100, 0] = np.nan
    masked = evaluate_symbolic(pattern(leaf()), replace(flat, adjusted_closes=dirty))
    assert np.isnan(masked[100:184, 0]).all()
    assert np.isfinite(masked[184:, 0]).all()


def test_named_source_alignment_lag_and_authorization(panel):
    base = terminal_values(panel, FeatureNode.model_validate(leaf()))
    expression = pattern(leaf(source="QQQ", lag=3))
    result = evaluate_symbolic(expression, panel)
    expected = np.broadcast_to(np.tanh(base[:-3, 1, None]), result[3:].shape)
    np.testing.assert_equal(result[3:], expected)
    assert np.isnan(result[:3]).all()
    assert symbolic_feature_sources(expression) == {"QQQ"}
    assert symbolic_feature_sources(pattern(leaf("relative_strength"))) == {"SPY"}
    with pytest.raises(ValueError, match="unauthorized"):
        evaluate_symbolic(pattern(leaf(source="BAD")), panel)
    with pytest.raises(ValueError, match="SPY"):
        evaluate_symbolic(pattern(leaf("relative_strength")), panel.slice_symbols(("QQQ", "GLD")))


def test_candle_primitives_hand_calculated_and_zero_range(panel):
    closes = np.full_like(panel.adjusted_closes, 10)
    candles = replace(
        panel,
        adjusted_closes=closes,
        adjusted_opens=closes - 1,
        adjusted_highs=closes + 2,
        adjusted_lows=closes - 2,
    )
    expected = {
        "body_atr": 0.25,
        "range_atr": 1,
        "upper_wick_fraction": 0.5,
        "lower_wick_fraction": 0.25,
    }
    for name, value in expected.items():
        node = FeatureNode.model_validate(leaf(name))
        np.testing.assert_allclose(raw_feature(candles, node)[21:], value)
    flat = replace(candles, adjusted_opens=closes, adjusted_highs=closes, adjusted_lows=closes)
    for name in CANDLE_FEATURES:
        assert np.isnan(raw_feature(flat, FeatureNode.model_validate(leaf(name)))).all()


@pytest.mark.parametrize("op", ["add", "sub", "mul", "safe_div", "min", "max"])
def test_vectorized_arithmetic_matches_scalar_reference(panel, op):
    left, right = leaf(), leaf("efficiency", transform="rolling_zscore")
    expression = pattern(binary(op, left, right))
    a = terminal_values(panel, FeatureNode.model_validate(left))
    b = terminal_values(panel, FeatureNode.model_validate(right))
    expected = np.full_like(a, np.nan)
    for row in range(len(a)):
        for col in range(a.shape[1]):
            x, y = float(a[row, col]), float(b[row, col])
            if not np.isfinite([x, y]).all() or (op == "safe_div" and abs(y) < 1e-8):
                continue
            value = {
                "add": operator.add,
                "sub": operator.sub,
                "mul": operator.mul,
                "safe_div": operator.truediv,
                "min": min,
                "max": max,
            }[op](x, y)
            expected[row, col] = np.tanh(np.clip(value, -1e6, 1e6))
    np.testing.assert_allclose(evaluate_symbolic(expression, panel), expected, equal_nan=True)


@pytest.mark.parametrize("op", ["neg", "abs", "tanh"])
def test_unary_arithmetic_matches_scalar_reference(panel, op):
    expression = pattern({"node": "unary", "op": op, "arg": const(-2)})
    expected = {"neg": 2, "abs": 2, "tanh": np.tanh(-2)}[op]
    np.testing.assert_equal(evaluate_symbolic(expression, panel), np.tanh(expected))


def test_division_boundary_clipping_diagnostics_and_strict_missing(panel):
    assert np.isnan(
        evaluate_symbolic(pattern(binary("safe_div", const(1), const(1e-9))), panel)
    ).all()
    safe = evaluate_symbolic_trace(pattern(binary("safe_div", const(1), const(1e-8))), panel)
    np.testing.assert_equal(safe.values, 1)
    assert safe.diagnostics["clipped_intermediate_cells"] == panel.adjusted_closes.size
    missing = leaf(transform="rolling_zscore")
    flat = replace(panel, adjusted_closes=np.full_like(panel.adjusted_closes, 100))
    for op in ("mul", "min", "max"):
        assert np.isnan(evaluate_symbolic(pattern(binary(op, const(0), missing)), flat)).all()
    trace = evaluate_symbolic_trace(pattern(binary("safe_div", const(1), const(0))), panel)
    assert trace.diagnostics["invalid_division_cells"] == panel.adjusted_closes.size


def test_registry_identity_caching_and_contract_rejection(panel):
    manifest = symbolic_registry_manifest()
    assert manifest["version"] == SYMBOLIC_REGISTRY_VERSION
    assert manifest["normalization_window"] == 63
    assert manifest["secondary_lookback"] == 5
    assert len(manifest["features"]) == len(get_args(FeatureName))
    assert len(symbolic_registry_digest()) == 64
    evaluator = SymbolicEvaluator(panel)
    node = FeatureNode.model_validate(leaf())
    assert evaluator.feature(node) is evaluator.feature(node)
    assert not evaluator.feature(node).flags.writeable
    np.testing.assert_equal(
        evaluator.evaluate(pattern(leaf())).values, evaluate_symbolic(pattern(leaf()), panel)
    )
    with pytest.raises(ValueError, match="symbolic_indicator"):
        evaluate_symbolic(parse_pattern((DOCS / "candle.example.json").read_text()), panel)
    data = pattern(leaf()).model_dump(by_alias=True)
    data["timeframe"] = "1h"
    with pytest.raises(ValueError, match="daily"):
        evaluate_symbolic(parse_pattern(data), panel)


def test_nonbinary_constant_zscore_unavailable(panel, monkeypatch):
    import autoresearch.genetic.symbolic_registry as registry

    monkeypatch.setattr(
        registry, "raw_feature", lambda panel, _node: np.full_like(panel.adjusted_closes, 0.1)
    )
    assert np.isnan(
        terminal_values(panel, FeatureNode.model_validate(leaf(transform="rolling_zscore")))
    ).all()
