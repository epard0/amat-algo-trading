"""Typed symbolic expression inference. No Python execution or train-time fitting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.models import (
    BinaryNode,
    ConstantNode,
    FeatureNode,
    NumericNode,
    PatternDefinition,
    SymbolicGenome,
    UnaryNode,
)
from autoresearch.genetic.symbolic_registry import (
    DIVISION_FLOOR,
    INTERMEDIATE_BOUND,
    NORMALIZATION_WINDOW,
    SYMBOLIC_REGISTRY_VERSION,
    raw_history,
    symbolic_registry_digest,
    symbolic_registry_manifest,
    terminal_values,
)

__all__ = [
    "SYMBOLIC_REGISTRY_VERSION",
    "SymbolicEvaluator",
    "SymbolicTrace",
    "evaluate_symbolic",
    "evaluate_symbolic_trace",
    "symbolic_feature_sources",
    "symbolic_feature_values",
    "symbolic_registry_digest",
    "symbolic_registry_manifest",
    "symbolic_required_history",
]


@dataclass(frozen=True)
class SymbolicTrace:
    values: np.ndarray
    diagnostics: dict[str, Any]


def _root(pattern: PatternDefinition) -> NumericNode:
    pattern = PatternDefinition.model_validate(pattern.model_dump(by_alias=True))
    if not isinstance(pattern.genome, SymbolicGenome):
        raise ValueError("symbolic evaluator requires symbolic_indicator")
    if (
        pattern.timeframe not in {"1d", "1w", "1mo", "15m", "1h"}
        or pattern.price_basis != "corporate_action_adjusted"
    ):
        raise ValueError("symbolic evaluator requires supported corporate-action-adjusted panel")
    return pattern.genome.root


def _leaves(node: NumericNode) -> list[FeatureNode]:
    if isinstance(node, FeatureNode):
        return [node]
    if isinstance(node, UnaryNode):
        return _leaves(node.arg)
    if isinstance(node, BinaryNode):
        return _leaves(node.left) + _leaves(node.right)
    return []


def symbolic_feature_sources(pattern: PatternDefinition) -> set[str]:
    leaves = _leaves(_root(pattern))
    sources = {node.source for node in leaves if node.source != "self"}
    if any(node.name == "relative_strength" for node in leaves):
        sources.add("SPY")
    return sources


def symbolic_required_history(pattern: PatternDefinition) -> int:
    """Zero-based first potentially available index; constants require zero history."""
    return max(
        (
            raw_history(node) + NORMALIZATION_WINDOW - 1 + node.lag
            for node in _leaves(_root(pattern))
        ),
        default=0,
    )


def _evaluate_trace(
    pattern: PatternDefinition, panel: MarketPanel, cache: dict[FeatureNode, np.ndarray]
) -> SymbolicTrace:
    root = _root(pattern)
    if pattern.timeframe != "1d":
        marker = (
            f"completed-XNYS-{pattern.timeframe}("
            if pattern.timeframe in {"1w", "1mo"}
            else f"alpaca:sip:all:shadow-intraday:{pattern.timeframe}"
        )
        markers: tuple[str, ...] = (marker,)
        if pattern.timeframe in {"15m", "1h"}:
            from autoresearch.genetic.data import digest_json  # noqa: PLC0415
            from autoresearch.genetic.native_views import (  # noqa: PLC0415
                SOURCE_PREFIX,
                native_view_contract,
            )

            markers += (
                f"{SOURCE_PREFIX}{pattern.timeframe}:contract={digest_json(native_view_contract())}",
            )
        if not panel.source.startswith(markers):
            raise ValueError("non-daily pattern requires explicitly aggregated timeframe panel")
    unauthorized = symbolic_feature_sources(pattern) - {str(symbol) for symbol in panel.symbols}
    if unauthorized:
        raise ValueError(f"unauthorized symbolic sources: {sorted(unauthorized)}")
    diagnostics: dict[str, Any] = {
        "registry_version": SYMBOLIC_REGISTRY_VERSION,
        "registry_sha256": symbolic_registry_digest(),
        "required_history": symbolic_required_history(pattern),
        "clipped_intermediate_cells": 0,
        "invalid_division_cells": 0,
        "nonfinite_intermediate_cells": 0,
        "nodes": [],
    }

    def evaluate(node: NumericNode) -> np.ndarray:
        if isinstance(node, FeatureNode):
            if node not in cache:
                cache[node] = terminal_values(panel, node)
            values = cache[node].copy()
            label = f"feature:{node.name}:{node.source}"
        elif isinstance(node, ConstantNode):
            values = np.full_like(panel.adjusted_closes, node.value, dtype=float)
            label = "constant"
        elif isinstance(node, UnaryNode):
            child = evaluate(node.arg)
            operations = {"neg": np.negative, "abs": np.abs, "tanh": np.tanh}
            values = operations[node.op](child)
            label = node.op
        else:
            left, right = evaluate(node.left), evaluate(node.right)
            valid = np.isfinite(left) & np.isfinite(right)
            with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
                if node.op == "safe_div":
                    invalid = valid & (np.abs(right) < DIVISION_FLOOR)
                    diagnostics["invalid_division_cells"] += int(invalid.sum())
                    values = np.divide(
                        left, right, out=np.full_like(left, np.nan), where=valid & ~invalid
                    )
                else:
                    binary_operations = {
                        "add": np.add,
                        "sub": np.subtract,
                        "mul": np.multiply,
                        "min": np.minimum,
                        "max": np.maximum,
                    }
                    values = binary_operations[node.op](left, right)
                values = np.where(valid, values, np.nan)
            label = node.op
        finite = np.isfinite(values)
        clipped = finite & (np.abs(values) > INTERMEDIATE_BOUND)
        diagnostics["clipped_intermediate_cells"] += int(clipped.sum())
        diagnostics["nonfinite_intermediate_cells"] += int((~finite).sum())
        diagnostics["nodes"].append(
            {
                "operation": label,
                "clipped_cells": int(clipped.sum()),
                "unavailable_cells": int((~finite).sum()),
            }
        )
        return np.where(finite, np.clip(values, -INTERMEDIATE_BOUND, INTERMEDIATE_BOUND), np.nan)

    values = np.tanh(evaluate(root))
    diagnostics["unavailable_output_cells"] = int((~np.isfinite(values)).sum())
    return SymbolicTrace(values, diagnostics)


def symbolic_feature_values(node: FeatureNode, panel: MarketPanel) -> np.ndarray:
    """Resolve one frozen, normalized dimensionless feature with strict missingness."""
    return terminal_values(panel, node)


class SymbolicEvaluator:
    """Reusable terminal cache bound to one immutable-in-practice panel.

    Do not mutate the panel after construction. Research callers bind its content
    digest separately; returned feature arrays are read-only to protect the cache.
    """

    def __init__(self, panel: MarketPanel) -> None:
        self.panel = panel
        self._cache: dict[FeatureNode, np.ndarray] = {}

    def feature(self, node: FeatureNode) -> np.ndarray:
        if node not in self._cache:
            values = symbolic_feature_values(node, self.panel)
            values.setflags(write=False)
            self._cache[node] = values
        return self._cache[node]

    def evaluate(self, pattern: PatternDefinition) -> SymbolicTrace:
        for node in _leaves(_root(pattern)):
            self.feature(node)
        return _evaluate_trace(pattern, self.panel, self._cache)


def evaluate_symbolic_trace(pattern: PatternDefinition, panel: MarketPanel) -> SymbolicTrace:
    return SymbolicEvaluator(panel).evaluate(pattern)


def evaluate_symbolic(pattern: PatternDefinition, panel: MarketPanel) -> np.ndarray:
    return evaluate_symbolic_trace(pattern, panel).values
