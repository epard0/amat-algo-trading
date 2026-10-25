"""Versioned research programs and strictly bounded feature/config proposals."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import Field, model_validator

from amat.config.v6 import V6Config
from autoresearch.baseline import CoreV13Config
from autoresearch.genetic.adapter import (
    GeneticPatternAdapter,
    GeneticSymbolicAdapterSpec,
    require_genetic_program,
)
from autoresearch.genetic.models import CandleGenome
from autoresearch.genetic.symbolic import symbolic_feature_sources, symbolic_required_history
from autoresearch.specs import EntryModelSpec, EvaluationSpec, SignalSpec, required_history
from llmrouter.chain import Chain
from llmrouter.contracts import Record

MAX_FEATURES = 4
MAX_HORIZONS = 4
MIN_HORIZON = 5
MAX_HORIZON = 252
BASE_COST = 2.0

# The proposer cannot edit costs, fills, dates, universe, sizing caps or warm-up.
BOUNDS: dict[str, tuple[float, float]] = {
    "trend.deadband_abs": (0.05, 0.5),
    "trend.clip_abs": (0.5, 3.0),
    "volatility.fast_half_life_sessions": (5, 40),
    "volatility.slow_half_life_sessions": (41, 60),
    "volatility.slow_weight": (0.25, 1.0),
    "rebalance.min_weight_delta": (0.005, 0.05),
}


class Feature(Record):
    kind: Literal["momentum", "volatility", "drawdown", "volume_ratio", "efficiency"]
    lookback: int = Field(ge=5, le=126)
    smoothing: int = Field(default=1, ge=1, le=21)
    cross_sectional_rank: bool = False
    threshold: float = Field(ge=-1, le=10)
    direction: Literal["above", "below"] = "above"
    attenuation: float = Field(default=0.25, gt=0, le=0.75)


class Patch(Record):
    path: str
    value: float | list[int]


class ResearchIntent(Record):
    mechanism: str = Field(min_length=10, max_length=700)
    falsification: str = Field(min_length=10, max_length=700)


class Proposal(Record):
    hypothesis: str = Field(min_length=10, max_length=1500)
    patches: tuple[Patch, ...] = Field(default=(), max_length=3)
    features: tuple[Feature, ...] = Field(default=(), max_length=4)
    signal: SignalSpec | None = None
    entry_model: EntryModelSpec | None = Field(default=None, exclude_if=lambda value: value is None)
    genetic_pattern: GeneticPatternAdapter | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    stage: Literal[
        "screen", "explore", "route", "pattern", "refine", "combine", "ablate", "policy", "manual"
    ] = "manual"
    parent_candidate: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    research: ResearchIntent | None = None


class Candidate(Record):
    config: V6Config | CoreV13Config
    features: tuple[Feature, ...] = Field(default=(), max_length=4)
    signal: SignalSpec | None = None
    # Preserve canonical hashes of every historical candidate without this extension.
    entry_model: EntryModelSpec | None = Field(default=None, exclude_if=lambda value: value is None)
    genetic_pattern: GeneticPatternAdapter | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def genetic_inputs(self) -> Candidate:
        if self.genetic_pattern:
            if self.entry_model or (self.signal and self.signal.router):
                raise ValueError("genetic patterns cannot combine with entry models or routers")
            if (
                isinstance(self.genetic_pattern.pattern.genome, CandleGenome)
                and self.genetic_pattern.pattern.genome.max_lag
                >= self.config.strategy.warmup.min_completed_sessions
            ):
                raise ValueError("genetic history exceeds the fixed warm-up budget")
            if isinstance(self.genetic_pattern, GeneticSymbolicAdapterSpec):
                pattern = self.genetic_pattern.pattern
                if (
                    symbolic_required_history(pattern)
                    >= self.config.strategy.warmup.min_completed_sessions
                ):
                    raise ValueError("symbolic history exceeds the fixed warm-up budget")
                sources = symbolic_feature_sources(pattern)
                authorized = {str(symbol) for symbol in self.config.strategy.universe.symbols}
                if sources - authorized:
                    raise ValueError(
                        "symbolic sources outside authorized universe: "
                        f"{sorted(sources - authorized)}"
                    )
        return self

    @model_validator(mode="after")
    def entry_inputs(self) -> Candidate:
        if self.entry_model:
            strategy = self.config.strategy
            if self.signal or self.features or self.config.asset_regime.capital_control_enabled:
                raise ValueError("entry model requires baseline scores without competing overlays")
            if (
                strategy.cross_sectional.enabled
                or strategy.structure.enabled
                or strategy.risk_budget.enabled
            ):
                raise ValueError("entry model requires the plain baseline allocator")
            if strategy.execution.signal_to_execution_lag_sessions != 1:
                raise ValueError("entry model trains on next-session baseline opportunities")
        return self

    @model_validator(mode="after")
    def signal_inputs(self) -> Candidate:
        if self.signal:
            strategy = self.config.strategy
            if (
                self.signal.selection
                and self.signal.selection.lookback >= strategy.warmup.min_completed_sessions
            ):
                raise ValueError("selection history exceeds the fixed warm-up budget")
            if (
                strategy.cross_sectional.enabled
                or strategy.structure.enabled
                or strategy.risk_budget.enabled
            ):
                raise ValueError(
                    "signal adapter requires plain v6; extra selectors are unsupported"
                )
            for term in self.signal.terms:
                if (
                    required_history(term.feature)
                    >= self.config.strategy.warmup.min_completed_sessions
                ):
                    raise ValueError("feature history exceeds the fixed warm-up budget")
                if (
                    term.feature.kind == "relative_strength"
                    and term.feature.benchmark not in self.config.strategy.universe.symbols
                ):
                    raise ValueError("benchmark must belong to the frozen universe")
            for pattern in self.signal.patterns:
                for condition in pattern.conditions:
                    history = required_history(self.signal.terms[condition.term].feature)
                    history += condition.lag + condition.window - 1
                    history += int(condition.predicate in ("rising", "falling"))
                    if history >= strategy.warmup.min_completed_sessions:
                        raise ValueError("pattern history exceeds the fixed warm-up budget")
            if self.signal.router:
                history = max(required_history(term.feature) for term in self.signal.terms)
                history += self.signal.router.smoothing_sessions - 1
                if history >= strategy.warmup.min_completed_sessions:
                    raise ValueError("router history exceeds the fixed warm-up budget")
        return self


def apply_proposal(parent: Candidate, proposal: Proposal) -> Candidate:
    payload = parent.config.model_dump(mode="json")
    seen = set()
    for patch in proposal.patches:
        if patch.path in seen:
            raise ValueError("duplicate patch path")
        seen.add(patch.path)
        if patch.path == "trend.horizons_sessions":
            if not isinstance(patch.value, list) or not 1 <= len(patch.value) <= MAX_HORIZONS:
                raise ValueError("horizons must be a list of one to four integers")
            if any(not MIN_HORIZON <= value <= MAX_HORIZON for value in patch.value):
                raise ValueError("horizons outside fixed warm-up budget")
        elif patch.path in BOUNDS:
            low, high = BOUNDS[patch.path]
            if isinstance(patch.value, list) or not low <= patch.value <= high:
                raise ValueError(f"patch outside bounds: {patch.path}")
        else:
            raise ValueError(f"protected or unknown field: {patch.path}")
        section, key = patch.path.split(".")
        payload["strategy"][section][key] = patch.value
    return Candidate(
        config=type(parent.config).model_validate(payload),
        features=proposal.features,
        signal=proposal.signal,
        entry_model=proposal.entry_model,
        genetic_pattern=proposal.genetic_pattern,
    )


def validate_candidate(baseline: Candidate, candidate: Candidate) -> None:
    """Recheck accumulated edits at the process boundary, including disk tampering."""
    expected = baseline
    values = candidate.config.strategy.model_dump(mode="json")
    for path in (*BOUNDS, "trend.horizons_sessions"):
        section, key = path.split(".")
        expected = apply_proposal(
            expected,
            Proposal(
                hypothesis="Validate the registered candidate against the frozen contract.",
                patches=(Patch(path=path, value=values[section][key]),),
                features=candidate.features,
                signal=candidate.signal,
                entry_model=candidate.entry_model,
                genetic_pattern=candidate.genetic_pattern,
            ),
        )
    if expected != candidate:
        raise ValueError("candidate changes protected configuration")


class Program(Record):
    schema_version: Literal[
        "strategy-development/5",
        "strategy-development/6",
        "strategy-development/7",
        "strategy-development/8",
        "strategy-development/9",
    ] = "strategy-development/7"
    extensions_only: bool = Field(default=False, exclude_if=lambda value: not value)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    train_end: dt.date
    validation_end: dt.date
    holdout_after: dt.date = dt.date(2026, 8, 10)
    measured_start: dt.date
    load_start: dt.date
    baseline: Candidate
    frozen_files: dict[str, str]
    dependencies: dict[str, str]
    evaluation: EvaluationSpec = Field(default_factory=EvaluationSpec)
    timeout_seconds: int = Field(default=240, ge=1, le=1800)
    max_experiments: int = Field(default=50, ge=1, le=1000)
    stall_limit: int = Field(default=12, ge=1, le=1000)
    min_improvement: float = Field(default=0.05, gt=0, le=1)
    max_drawdown: float = Field(default=0.2, gt=0, le=0.3)
    min_positive_folds: float = Field(default=0.6, ge=0.5, le=1)
    min_exposure: float = Field(default=0.2, ge=0.1, le=1)
    max_model_tokens_per_day: int = Field(default=100000, ge=256)
    execution_authority: Literal["none"] = "none"
    summary_chain: Chain | None = None

    @model_validator(mode="after")
    def ordered(self) -> Program:
        if self.baseline.genetic_pattern:
            require_genetic_program(self.baseline.genetic_pattern, self.schema_version)
        if not self.load_start < self.measured_start < self.train_end < self.validation_end:
            raise ValueError("program needs ordered load/train/validation windows")
        if self.validation_end > self.holdout_after:
            raise ValueError("validation must not enter the holdout")
        strategy = self.baseline.config.strategy
        if (
            strategy.costs.baseline_one_way_bps != BASE_COST
            or strategy.cash.constant_annualized_rate != 0
        ):
            raise ValueError("development contract requires 2 bps and zero cash yield")
        if self.baseline.config.risk_learning.enabled or strategy.learned_regime.enabled:
            raise ValueError("learned overlays need a separate nested-training adapter")
        if strategy.execution.signal_to_execution_lag_sessions != 1:
            raise ValueError("development contract requires next-session execution")
        return self
