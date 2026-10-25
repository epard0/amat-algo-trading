"""Typed feature, forecast and trading-policy contracts for research only."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from llmrouter.contracts import Record

MIN_SEQUENCE_STEPS = 2
TEMPLATE_BOUND = 3
MAX_TERM_INDEX = 3

FeatureKind = Literal[
    "momentum",
    "volatility",
    "drawdown",
    "volume_ratio",
    "efficiency",
    "normalized_momentum",
    "trend_agreement",
    "trend_acceleration",
    "donchian_position",
    "breakout_distance",
    "breakout_persistence",
    "relative_strength",
    "volume_price",
    "variance_ratio",
    "downside_variance_share",
    "rsi",
    "path_similarity",
    "breadth",
    "volatility_shock",
    "volatility_of_volatility",
    "close_location",
    "opening_gap",
    "window_markout",
    "illiquidity",
]
Transform = Literal[
    "identity",
    "time_series_rank",
    "rolling_zscore",
    "cross_sectional_rank",
    "group_rank",
    "volatility_scaled",
    "train_zscore",
]


class FeatureSpec(Record):
    kind: FeatureKind
    lookback: int = Field(default=63, ge=5, le=252)
    secondary_lookback: int = Field(default=21, ge=5, le=126)
    smoothing: int = Field(default=1, ge=1, le=21)
    transform: Transform = "identity"
    normalization_window: int = Field(default=63, ge=21, le=126)
    benchmark: str = Field(default="SPY", pattern=r"^[A-Z][A-Z0-9.]{0,9}$")
    clip: float = Field(default=3.0, ge=0.5, le=10)
    lag_sessions: int = Field(default=0, ge=0, le=63)
    template: tuple[float, ...] | None = Field(default=None, min_length=3, max_length=12)

    @model_validator(mode="after")
    def windows(self) -> FeatureSpec:
        if self.kind == "volatility_shock" and self.secondary_lookback >= self.lookback:
            raise ValueError("volatility shock requires a shorter secondary window")
        if self.kind == "variance_ratio" and self.secondary_lookback >= self.lookback:
            raise ValueError("variance ratio requires secondary_lookback < lookback")
        if self.kind == "path_similarity":
            if not self.template or len(set(self.template)) < MIN_SEQUENCE_STEPS:
                raise ValueError("path similarity requires a nonconstant template")
            if any(not -TEMPLATE_BOUND <= value <= TEMPLATE_BOUND for value in self.template):
                raise ValueError("template control points must be finite and within -3..3")
        elif self.template is not None:
            raise ValueError("templates are only supported by path_similarity")
        return self


class SignalTerm(Record):
    feature: FeatureSpec
    weight: float = Field(default=1.0, ge=-2, le=2)
    center: float = Field(default=0.0, ge=-10, le=10)
    scale: float = Field(default=1.0, ge=0.01, le=10)


class Interaction(Record):
    left: int = Field(ge=0, le=3)
    right: int = Field(ge=0, le=3)
    operation: Literal["product", "difference", "minimum", "maximum", "gate"] = "product"
    weight: float = Field(default=0.25, ge=-1, le=1)


class TradingPolicy(Record):
    entry_threshold: float = Field(default=0.55, ge=0.05, le=0.95)
    exit_threshold: float = Field(default=0.45, ge=0, le=0.9)
    confirmation_sessions: int = Field(default=1, ge=1, le=5)
    min_hold_sessions: int = Field(default=0, ge=0, le=63)

    @model_validator(mode="after")
    def hysteresis(self) -> TradingPolicy:
        if self.exit_threshold >= self.entry_threshold:
            raise ValueError("exit threshold must be below entry threshold")
        return self


class PatternCondition(Record):
    term: int = Field(ge=0, le=3)
    predicate: Literal["above", "below", "rising", "falling"] = "above"
    threshold: float = Field(default=0.0, ge=-10, le=10)
    lag: int = Field(default=0, ge=0, le=63)
    window: int = Field(default=1, ge=1, le=21)
    min_fraction: float = Field(default=1.0, ge=0.5, le=1)


class PatternSpec(Record):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    mode: Literal["all", "any", "sequence"] = "all"
    conditions: tuple[PatternCondition, ...] = Field(min_length=1, max_length=4)
    weight: float = Field(default=1.0, ge=-2, le=2)

    @model_validator(mode="after")
    def chronological(self) -> PatternSpec:
        if self.mode == "sequence":
            if len(self.conditions) < MIN_SEQUENCE_STEPS:
                raise ValueError("a sequence needs at least two conditions")
            for earlier, later in zip(self.conditions, self.conditions[1:], strict=False):
                if earlier.lag < later.lag + later.window:
                    raise ValueError(
                        "sequence windows must be ordered oldest first and non-overlapping"
                    )
        return self


class RouterSpec(Record):
    """Blend two weighted sleeves using an observed state, not fitted probabilities."""

    trend_terms: tuple[int, ...] = Field(default=(0,), min_length=1, max_length=3)
    range_terms: tuple[int, ...] = Field(default=(1,), min_length=1, max_length=3)
    state_term: int = Field(default=2, ge=0, le=3)
    stress_term: int | None = Field(default=None, ge=0, le=3)
    mode: Literal["adaptive", "static", "training_mean"] = "adaptive"
    state_floor: float = Field(default=0.15, ge=-10, le=10)
    state_ceiling: float = Field(default=0.45, ge=-10, le=10)
    static_trend_weight: float = Field(default=0.5, ge=0, le=1)
    smoothing_sessions: int = Field(default=3, ge=1, le=21)
    range_trend_floor: float = Field(default=0.5, ge=0, le=1)
    stress_threshold: float = Field(default=0.4, ge=-10, le=10)
    stress_scale: float = Field(default=0.6, ge=0.01, le=10)
    max_attenuation: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> RouterSpec:
        if self.state_floor >= self.state_ceiling:
            raise ValueError("router state floor must be below its ceiling")
        for indices in (self.trend_terms, self.range_terms):
            if len(set(indices)) != len(indices) or any(
                not 0 <= i <= MAX_TERM_INDEX for i in indices
            ):
                raise ValueError("sleeve references must be distinct term indices in 0..3")
        return self


class SelectionSpec(Record):
    """Rank eligible assets before sizing; retain the declared universe and risk caps."""

    mode: Literal["entries", "portfolio"] = "portfolio"
    top_k: int = Field(default=4, ge=1, le=30)
    lookback: int = Field(default=20, ge=5, le=126)
    min_dollar_volume: float = Field(default=10_000_000, ge=0, le=1e10)
    min_window_dollar_volume: float = Field(default=250_000, ge=0, le=1e9)
    min_relative_volume: float = Field(default=0.65, ge=0, le=5)
    max_relative_volume: float = Field(default=3.0, ge=0.1, le=20)
    max_per_cluster: int = Field(default=2, ge=1, le=30)
    retention_bonus: float = Field(default=0.015, ge=0, le=0.2)

    @model_validator(mode="after")
    def volume_band(self) -> SelectionSpec:
        if self.min_relative_volume >= self.max_relative_volume:
            raise ValueError("minimum relative volume must be below maximum")
        return self


class EntryModelSpec(Record):
    """Learn bounded entry timing while retaining the baseline portfolio and exits."""

    horizon: Literal[20, 60] = 60
    training_sessions: int = Field(default=756, ge=252, le=1260)
    recency_half_life: int = Field(default=126, ge=63, le=504)
    refit_sessions: int = Field(default=63, ge=21, le=126)
    min_training_events: int = Field(default=96, ge=64, le=512)
    min_validation_events: int = Field(default=8, ge=5, le=63)
    minimum_defer_bps: float = Field(default=5.0, ge=0, le=100)
    adverse_weight: float = Field(default=0.1, ge=0, le=1)
    uncertainty_weight: float = Field(default=0.25, ge=0, le=2)
    max_entry_reduction: float = Field(default=0.5, gt=0, le=0.75)
    max_deferral_sessions: int = Field(default=1, ge=1, le=3)
    max_feature_z: float = Field(default=6.0, ge=3, le=10)
    regime_validation: bool = True
    recent_health: bool = True


class SignalSpec(Record):
    terms: tuple[SignalTerm, ...] = Field(min_length=1, max_length=4)
    interactions: tuple[Interaction, ...] = Field(default=(), max_length=2)
    patterns: tuple[PatternSpec, ...] = Field(default=(), max_length=3)
    learner: Literal["weighted", "ridge"] = "weighted"
    horizon: Literal[5, 20, 60] = 20
    baseline_mix: float = Field(default=0.0, ge=0, le=0.9)
    policy: TradingPolicy = Field(default_factory=TradingPolicy)
    router: RouterSpec | None = None
    selection: SelectionSpec | None = None

    @model_validator(mode="after")
    def routing_composition(self) -> SignalSpec:
        if self.router:
            router = self.router
            references = {*router.trend_terms, *router.range_terms, router.state_term}
            if router.stress_term is not None:
                references.add(router.stress_term)
            if references != set(range(len(self.terms))):
                raise ValueError("router must reference every term and no missing terms")
            if self.learner != "weighted" or self.interactions or self.patterns:
                raise ValueError(
                    "router requires weighted sleeves without interactions or patterns"
                )
            if any(term.feature.transform == "train_zscore" for term in self.terms):
                raise ValueError("rule router requires causal rolling transforms")
            for indices in (router.trend_terms, router.range_terms):
                if not any(self.terms[i].weight for i in indices):
                    raise ValueError("each router sleeve needs nonzero weights")
        return self

    @model_validator(mode="after")
    def composition(self) -> SignalSpec:
        if not (
            any(term.weight for term in self.terms)
            or any(p.weight for p in self.patterns)
            or any(i.weight for i in self.interactions)
        ):
            raise ValueError("signal needs a nonzero term weight")
        if len({term.feature.digest() for term in self.terms}) != len(self.terms):
            raise ValueError("duplicate signal features")
        for interaction in self.interactions:
            if max(interaction.left, interaction.right) >= len(self.terms):
                raise ValueError("interaction references a missing term")
            if interaction.left == interaction.right:
                raise ValueError("interaction requires distinct terms")
        if len({pattern.name for pattern in self.patterns}) != len(self.patterns):
            raise ValueError("pattern names must be unique")
        for pattern in self.patterns:
            if any(condition.term >= len(self.terms) for condition in pattern.conditions):
                raise ValueError("pattern references a missing term")
        return self


class EvaluationSpec(Record):
    initial_training_sessions: int = Field(default=252, ge=252, le=756)
    refit_sessions: int = Field(default=126, ge=63, le=252)
    inner_validation_sessions: int = Field(default=63, ge=42, le=126)
    embargo_sessions: int = Field(default=1, ge=1, le=5)
    bootstrap_resamples: int = Field(default=1000, ge=200, le=5000)
    block_sessions: int = Field(default=60, ge=60, le=126)
    confidence: float = Field(default=0.90, ge=0.8, le=0.99)
    beam_width: int = Field(default=4, ge=2, le=6)
    screening_trials: int = Field(default=10, ge=8, le=20)
    max_forecast_correlation: float = Field(default=0.98, ge=0.5, le=0.999)
    exposure_tolerance: float = Field(default=0.02, ge=0.005, le=0.05)
    robustness: Literal["historical", "extended"] = "historical"
    scenario_seed: int = Field(default=20260911, ge=0, le=2147483647)
    scenario_bootstrap_paths: int = Field(default=2, ge=1, le=8)
    scenario_synthetic_paths: int = Field(default=1, ge=1, le=8)


def required_history(spec: FeatureSpec) -> int:
    """Index of the first potentially available value (zero-based session axis)."""
    history = spec.lookback
    if spec.kind in ("donchian_position", "close_location"):
        history -= 1
    if spec.kind == "window_markout":
        history -= 1
    if spec.kind in ("normalized_momentum", "trend_agreement", "volume_price", "breakout_distance"):
        history = max(history, spec.secondary_lookback)
    if spec.kind in ("trend_acceleration", "breakout_persistence"):
        history = max(history, spec.secondary_lookback) + spec.secondary_lookback
    if spec.kind == "volatility_of_volatility":
        history = spec.lookback + spec.secondary_lookback - 1
    history += spec.smoothing - 1
    if spec.transform in ("time_series_rank", "rolling_zscore", "group_rank"):
        history += spec.normalization_window - 1
    if spec.transform == "volatility_scaled":
        history = max(history, spec.normalization_window)
    return history + spec.lag_sessions
