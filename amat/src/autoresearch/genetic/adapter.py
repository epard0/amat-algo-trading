"""Versioned, hash-bound candle and symbolic features for research replay only."""

from __future__ import annotations

from typing import Literal, Self

import numpy as np
from pydantic import Field, model_validator

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.evaluator import evaluate_candles
from autoresearch.genetic.models import (
    BinaryNode,
    CandleGenome,
    PatternDefinition,
    SymbolicGenome,
    UnaryNode,
    canonical_genome_digest,
)
from autoresearch.genetic.symbolic import (
    SYMBOLIC_REGISTRY_VERSION,
    evaluate_symbolic,
    symbolic_registry_digest,
)
from llmrouter.contracts import Record

GENETIC_PROGRAM_VERSION = "strategy-development/8"
SYMBOLIC_PROGRAM_VERSION: Literal["strategy-development/9"] = "strategy-development/9"


class GeneticAdapterSpec(Record):
    """An inert formula bound to its operational digest, never a promotion grant."""

    schema_version: Literal["genetic-candle-adapter/1"] = "genetic-candle-adapter/1"
    pattern: PatternDefinition
    genome_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def supported_pattern(self) -> Self:
        # Reparse to reject bypassed/mutated nested model instances at this boundary.
        pattern = PatternDefinition.model_validate(self.pattern.model_dump(by_alias=True))
        if canonical_genome_digest(pattern) != self.genome_sha256:
            raise ValueError("genetic pattern operational hash mismatch")
        if not isinstance(pattern.genome, CandleGenome):
            raise ValueError("genetic adapter supports candle conjunctions only")
        if pattern.timeframe != "1d" or pattern.price_basis != "corporate_action_adjusted":
            raise ValueError("genetic replay requires daily corporate-action-adjusted candles")
        if pattern.binding.role != "feature" or pattern.binding.direction != "long_support":
            raise ValueError("genetic replay supports long-support feature binding only")
        return self


class GeneticSymbolicAdapterSpec(Record):
    """Versioned numerical contribution bound to both formula and feature registry."""

    schema_version: Literal["genetic-symbolic-adapter/1"] = "genetic-symbolic-adapter/1"
    pattern: PatternDefinition
    genome_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    registry_version: str
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def supported_pattern(self) -> Self:
        pattern = PatternDefinition.model_validate(self.pattern.model_dump(by_alias=True))
        if canonical_genome_digest(pattern) != self.genome_sha256:
            raise ValueError("genetic pattern operational hash mismatch")
        if not isinstance(pattern.genome, SymbolicGenome):
            raise ValueError("symbolic adapter requires a symbolic indicator")
        if (
            self.registry_version != SYMBOLIC_REGISTRY_VERSION
            or self.registry_sha256 != symbolic_registry_digest()
        ):
            raise ValueError("symbolic feature registry binding mismatch")
        if pattern.timeframe != "1d" or pattern.price_basis != "corporate_action_adjusted":
            raise ValueError("symbolic replay requires daily corporate-action-adjusted inputs")
        if pattern.binding.role != "feature" or pattern.binding.direction != "long_support":
            raise ValueError("symbolic replay supports long-support feature binding only")
        return self


GeneticPatternAdapter = GeneticAdapterSpec | GeneticSymbolicAdapterSpec


def require_genetic_program(spec: GeneticPatternAdapter, version: str) -> None:
    """Keep legacy candle execution fixed while requiring explicit symbolic opt-in."""
    if isinstance(spec, GeneticSymbolicAdapterSpec):
        if version != SYMBOLIC_PROGRAM_VERSION:
            raise ValueError("symbolic patterns require strategy-development/9")
    elif version not in (GENETIC_PROGRAM_VERSION, SYMBOLIC_PROGRAM_VERSION):
        raise ValueError("genetic patterns require strategy-development/8 or /9")


def symbolic_complexity(pattern: PatternDefinition) -> int:
    """Count every terminal and operator once; the required output tanh adds no node."""
    if not isinstance(pattern.genome, SymbolicGenome):
        raise ValueError("symbolic complexity requires a symbolic genome")
    pending = [pattern.genome.root]
    count = 0
    while pending:
        node = pending.pop()
        count += 1
        if isinstance(node, UnaryNode):
            pending.append(node.arg)
        elif isinstance(node, BinaryNode):
            pending.extend((node.left, node.right))
    return count


def apply_genetic_pattern(
    panel: MarketPanel, scores: np.ndarray, spec: GeneticPatternAdapter
) -> tuple[np.ndarray, np.ndarray]:
    """Apply at most maxEffect score points to existing positive decisions.

    Candle match/nonmatch is centered to +1/-1; symbolic values remain [-1,1].
    Missing is neutral. Existing zero scores
    remain zero, preserving upstream entry gates. No transform fitting, labels,
    portfolio allocation or execution occurs here. Returns scores and raw matches.
    """
    if isinstance(spec, GeneticSymbolicAdapterSpec):
        spec = GeneticSymbolicAdapterSpec.model_validate(spec.model_dump())
        values = evaluate_symbolic(spec.pattern, panel)
        contribution = values
    else:
        spec = GeneticAdapterSpec.model_validate(spec)
        opens, highs, lows, closes = panel.candle_inputs()
        values = evaluate_candles(
            spec.pattern, {"open": opens, "high": highs, "low": lows, "close": closes}
        )
        contribution = 2.0 * values - 1.0
    if scores.shape != panel.adjusted_closes.shape:
        raise ValueError("genetic scores must align with the daily candle panel")
    active = np.isfinite(scores) & (scores > 0) & np.isfinite(values)
    result = scores.copy()
    result[active] = np.clip(
        scores[active] + spec.pattern.binding.max_effect * contribution[active],
        0.0,
        1.0,
    )
    return result, values
