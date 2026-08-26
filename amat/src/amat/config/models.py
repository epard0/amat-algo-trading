"""Typed, validated configuration for the frozen v1 strategy.

Every model is frozen and rejects unknown keys (``extra="forbid"``). A typo in a
configuration file is a hard failure, not a silently ignored setting.

The validators here re-state the invariants of
``.claude/references/strategy-contract.md``. That reference remains normative:
if the two ever disagree, the reference wins and this module is the defect.

No default value in this module is a substitute for an explicit configuration
file. Defaults exist so that a partially specified *test* fixture is still
meaningful; the canonical run always loads ``configs/strategy/amat_v1.toml``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amat.domain.instruments import Cluster, Universe
from amat.domain.types import Symbol, parse_symbol

__all__ = [
    "CashConfig",
    "ClusterSpec",
    "ConstraintConfig",
    "CostConfig",
    "CovarianceConfig",
    "CrossSectionConfig",
    "ExecutionConfig",
    "RebalanceConfig",
    "RiskBudgetConfig",
    "RiskOverlayConfig",
    "StrategyConfig",
    "TrendConfig",
    "UniverseSpec",
    "VolatilityConfig",
    "WarmupConfig",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid", strict=False)

#: Absolute warm-up floor from the strategy contract, in completed sessions.
#: Applied independently of the component lookbacks so that a short-horizon
#: override cannot collapse it.
CONTRACT_MIN_WARMUP_SESSIONS = 252

UnitFraction = Annotated[float, Field(gt=0.0, le=1.0)]
NonNegativeFloat = Annotated[float, Field(ge=0.0)]
PositiveInt = Annotated[int, Field(gt=0)]


class ClusterSpec(BaseModel):
    """One risk cluster and its gross-exposure cap."""

    model_config = _MODEL_CONFIG

    name: str = Field(min_length=1)
    gross_cap: UnitFraction
    symbols: tuple[str, ...] = Field(min_length=1)

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(str(parse_symbol(raw)) for raw in value)
        if len(set(normalized)) != len(normalized):
            message = "duplicate symbols within a cluster"
            raise ValueError(message)
        return normalized

    def to_domain(self) -> Cluster:
        """Convert to the immutable domain record."""
        return Cluster(
            name=self.name,
            gross_cap=self.gross_cap,
            symbols=tuple(Symbol(symbol) for symbol in self.symbols),
        )


class UniverseSpec(BaseModel):
    """The tradable universe, declared as an ordered list of clusters.

    Cluster order fixes the symbol order used by every downstream matrix, so it
    is part of the frozen configuration rather than an incidental detail.
    """

    model_config = _MODEL_CONFIG

    clusters: tuple[ClusterSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_exclusive_membership(self) -> Self:
        seen: dict[str, str] = {}
        for cluster in self.clusters:
            for symbol in cluster.symbols:
                if symbol in seen:
                    message = (
                        f"symbol {symbol} appears in clusters "
                        f"{seen[symbol]!r} and {cluster.name!r}; membership must be exclusive"
                    )
                    raise ValueError(message)
                seen[symbol] = cluster.name
        names = [cluster.name for cluster in self.clusters]
        if len(set(names)) != len(names):
            message = "duplicate cluster names"
            raise ValueError(message)
        return self

    @property
    def symbols(self) -> tuple[str, ...]:
        """Deterministic symbol order across all clusters."""
        return tuple(symbol for cluster in self.clusters for symbol in cluster.symbols)

    def to_domain(self) -> Universe:
        """Convert to the immutable domain universe."""
        return Universe.from_clusters([cluster.to_domain() for cluster in self.clusters])


class VolatilityConfig(BaseModel):
    """Exponentially weighted volatility estimator.

    ``sigma = max(sigma_fast, slow_weight * sigma_slow, floor_annualized)``
    where each component is annualized by ``sqrt(annualization_sessions)``.
    """

    model_config = _MODEL_CONFIG

    fast_half_life_sessions: PositiveInt = 20
    slow_half_life_sessions: PositiveInt = 60
    slow_weight: NonNegativeFloat = 0.75
    # Strictly positive: the contract's sigma floor exists so that
    # q = long_score / sigma cannot diverge for a low-volatility symbol. At
    # zero the volatility target becomes meaningless and the symbol/cluster
    # caps silently truncate the result instead of the run failing closed.
    floor_annualized: float = Field(default=0.05, gt=0.0)
    annualization_sessions: PositiveInt = 252
    min_periods_sessions: PositiveInt = 60

    @model_validator(mode="after")
    def _require_ordered_half_lives(self) -> Self:
        if self.fast_half_life_sessions >= self.slow_half_life_sessions:
            message = (
                "fast_half_life_sessions must be strictly shorter than slow_half_life_sessions"
            )
            raise ValueError(message)
        if self.min_periods_sessions < self.slow_half_life_sessions:
            message = (
                "min_periods_sessions must be at least slow_half_life_sessions so that "
                "the slow estimate is not reported from an unconverged window"
            )
            raise ValueError(message)
        return self


class TrendConfig(BaseModel):
    """Volatility-normalized trend signal.

    ``z_h = clip(r_h / (sigma * sqrt(h / annualization)), -clip_abs, +clip_abs)``
    ``score = tanh(mean(z_h))`` then zeroed where ``abs(score) < deadband_abs``.
    """

    model_config = _MODEL_CONFIG

    horizons_sessions: tuple[PositiveInt, ...] = (21, 63, 252)
    clip_abs: float = Field(default=2.0, gt=0.0)
    deadband_abs: float = Field(default=0.20, ge=0.0, lt=1.0)
    long_only: bool = True

    @field_validator("horizons_sessions")
    @classmethod
    def _require_increasing(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            message = "at least one horizon is required"
            raise ValueError(message)
        if list(value) != sorted(set(value)):
            message = "horizons_sessions must be strictly increasing and unique"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _require_long_only(self) -> Self:
        if not self.long_only:
            message = (
                "the v1 strategy contract is long-or-cash; enabling short exposure "
                "requires a new strategy version, not a configuration flag"
            )
            raise ValueError(message)
        return self

    @property
    def max_horizon_sessions(self) -> int:
        """Longest lookback the trend signal requires."""
        return max(self.horizons_sessions)


class CovarianceConfig(BaseModel):
    """Covariance estimator used for portfolio volatility forecasting."""

    model_config = _MODEL_CONFIG

    lookback_sessions: PositiveInt = 120
    estimator: Literal["ledoit_wolf"] = "ledoit_wolf"
    annualization_sessions: PositiveInt = 252


class ConstraintConfig(BaseModel):
    """Volatility target and the exposure caps applied after it.

    Caps are applied in the fixed order stated by the strategy contract:
    symbol cap, cluster caps, gross cap, optional risk-contribution limit,
    then risk is recomputed. Residual stays in cash; the portfolio is never
    re-levered to recover the volatility target.
    """

    model_config = _MODEL_CONFIG

    target_annualized_volatility: float = Field(default=0.08, gt=0.0, le=1.0)
    max_symbol_weight: UnitFraction = 0.20
    max_gross_exposure: UnitFraction = 1.00
    risk_contribution_limit: UnitFraction | None = None
    allow_releverage_after_clipping: Literal[False] = False


class CrossSectionConfig(BaseModel):
    """Which assets compete for capital, and how many win.

    Off by default, and a no-op when off, so every configuration written before
    this section existed keeps its exact meaning. Turning it on without setting
    a rule is rejected rather than accepted as a no-op: a section that appears
    in a config file and changes nothing is a trap for the next reader.

    ``max_positions`` is applied to the dead-banded long scores, so it selects
    among assets that already have a trend -- it never manufactures a position
    to reach the count. ``min_horizon_agreement`` requires that many of the
    trend horizons be strictly positive, which is the confirmation rule the v6
    audit measured (entries with all three horizons positive averaged +0.19%
    per round trip against -0.23% and -0.27% for the disagreeing groups) and
    deliberately declined to promote, because that split was found on the same
    burned sample it was measured on. It is exposed here as a registered,
    falsifiable option and defaults to off for exactly that reason.
    """

    model_config = _MODEL_CONFIG

    enabled: bool = False
    max_positions: PositiveInt | None = None
    min_horizon_agreement: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _require_a_rule_when_enabled(self) -> Self:
        if self.enabled and self.max_positions is None and self.min_horizon_agreement == 0:
            message = (
                "cross_sectional.enabled is true but neither max_positions nor "
                "min_horizon_agreement is set; that is a no-op wearing the name of a change"
            )
            raise ValueError(message)
        if not self.enabled and (self.max_positions is not None or self.min_horizon_agreement > 0):
            message = (
                "cross_sectional rules are configured but enabled is false; leaving rules "
                "that do nothing in a frozen config is how a later reader mis-describes a run"
            )
            raise ValueError(message)
        return self

    def require_horizons(self, horizon_count: int) -> None:
        """Reject an agreement threshold no configuration of horizons can meet.

        Left as an explicit call from :class:`StrategyConfig` rather than a
        field validator because the horizon count lives on a sibling section.
        Without it, ``min_horizon_agreement = 4`` against three horizons would
        validate cleanly and then hold 100% cash for the entire backtest --
        a silent, plausible-looking zero rather than a configuration error.
        """
        if self.min_horizon_agreement > horizon_count:
            message = (
                f"cross_sectional.min_horizon_agreement ({self.min_horizon_agreement}) "
                f"exceeds the {horizon_count} configured trend horizons; no asset could "
                f"ever qualify and the strategy would hold cash forever"
            )
            raise ValueError(message)


class RiskBudgetConfig(BaseModel):
    """Shift risk toward the assets the signal has actually been paid on.

    Off by default and inert when off, so every configuration written before
    this section existed keeps its exact meaning.

    The multipliers are rank-based and mean-normalised, so this *reallocates*
    risk rather than changing how much is taken -- the volatility target still
    sets the total. ``min_multiplier`` is strictly positive on purpose: a
    budget that can reach zero is a selection rule, and experiments 0006 and
    0007 both found that selection does not help this strategy.
    """

    model_config = _MODEL_CONFIG

    enabled: bool = False
    #: Sessions of signal payoff the trailing information ratio reads.
    lookback_sessions: PositiveInt = 252
    #: Observations required before an asset is ranked at all. Below this the
    #: asset gets a multiplier of exactly 1.0 rather than a guessed one.
    min_periods_sessions: PositiveInt = 126
    min_multiplier: float = Field(default=0.35, gt=0.0, le=1.0)
    max_multiplier: float = Field(default=1.75, ge=1.0)
    #: What "paying" means. ``information_ratio`` is the trailing mean payoff
    #: over its own standard deviation; ``mean_payoff`` is the mean alone.
    #:
    #: The default is the ratio, and the reason is worth recording because the
    #: obvious argument points the other way. The allocator already sizes
    #: inverse to volatility, so dividing again looks like double-counting, and
    #: against realised ten-year P&L the plain mean is the better *identifier*
    #: (+0.893 correlation against +0.687). It is nonetheless the worse
    #: *allocator*: measured on this sample the mean-payoff budget lost $10,244
    #: against no budget at all while the ratio gained $596. The volatility
    #: term is doing portfolio-construction work, not stock-picking work --
    #: it prefers assets whose contribution is steady, and steadiness is what
    #: survives into the next window. Switching to the mean because it matched
    #: a P&L table already read would have been fitting to a known outcome.
    measure: Literal["mean_payoff", "information_ratio"] = "information_ratio"

    @model_validator(mode="after")
    def _require_a_usable_range(self) -> Self:
        if self.enabled and self.min_multiplier >= self.max_multiplier:
            message = (
                f"risk_budget.min_multiplier ({self.min_multiplier}) must be below "
                f"max_multiplier ({self.max_multiplier}); an inverted or empty range "
                f"cannot reallocate anything"
            )
            raise ValueError(message)
        if self.min_periods_sessions > self.lookback_sessions:
            message = (
                f"risk_budget.min_periods_sessions ({self.min_periods_sessions}) exceeds "
                f"lookback_sessions ({self.lookback_sessions}); no asset could ever qualify"
            )
            raise ValueError(message)
        if not self.enabled and (self.min_multiplier != 0.35 or self.max_multiplier != 1.75):  # noqa: PLR2004
            message = (
                "risk_budget bounds are configured but enabled is false; leaving settings "
                "that do nothing in a frozen config misdescribes the run"
            )
            raise ValueError(message)
        return self

    @property
    def required_warmup_sessions(self) -> int:
        """Sessions of payoff history needed before the budget says anything."""
        return self.min_periods_sessions if self.enabled else 0


class RebalanceConfig(BaseModel):
    """When a target change becomes a simulated trade."""

    model_config = _MODEL_CONFIG

    min_weight_delta: float = Field(default=0.02, gt=0.0, lt=1.0)
    min_trade_notional: Decimal = Field(default=Decimal("25"), gt=Decimal(0))


class ExecutionConfig(BaseModel):
    """Execution timing and the missing-data policy.

    ``signal_to_execution_lag_sessions`` must be at least 1. A value of 0 would
    execute on the bar that produced the signal, which is the canonical
    look-ahead defect and an automatic rejection condition.
    """

    model_config = _MODEL_CONFIG

    timezone: str = "America/New_York"
    window_start: dt.time = dt.time(9, 30)
    window_end: dt.time = dt.time(9, 35)
    price_method: Literal["vwap"] = "vwap"
    price_basis: Literal["raw"] = "raw"
    signal_to_execution_lag_sessions: int = Field(default=1, ge=1)
    missing_bar_policy: Literal["record_missed_fill"] = "record_missed_fill"

    @field_validator("timezone")
    @classmethod
    def _require_known_timezone(cls, value: str) -> str:
        """Reject an unknown IANA zone at the configuration boundary.

        Left unvalidated, an invalid zone surfaced only when a downstream
        ``ZoneInfo`` call raised -- after the run had already started, rather
        than at the boundary where guardrail 9 requires it to fail closed.
        """
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            message = f"unknown IANA timezone: {value!r}"
            raise ValueError(message) from exc
        return value

    @model_validator(mode="after")
    def _require_ordered_window(self) -> Self:
        if self.window_start >= self.window_end:
            message = "window_start must be strictly before window_end"
            raise ValueError(message)
        return self


class CostConfig(BaseModel):
    """One-way transaction cost assumptions.

    ``stress_one_way_bps`` is required and must contain at least one value
    strictly greater than ``baseline_one_way_bps``: a configuration that cannot
    be stressed upward cannot satisfy the robustness protocol.
    """

    model_config = _MODEL_CONFIG

    baseline_one_way_bps: NonNegativeFloat = 2.0
    stress_one_way_bps: tuple[NonNegativeFloat, ...] = (0.0, 4.0, 6.0, 10.0)
    is_primary: bool = True
    """Whether this cost assumption may back a headline result.

    Mirrors :class:`CashConfig`. Guardrail 7 makes zero-cost runs diagnostic
    only, so a zero baseline must mark itself non-primary; otherwise a
    frictionless backtest would resolve to a configuration -- and therefore a
    manifest -- indistinguishable from the canonical 2 bps run.
    """

    @model_validator(mode="after")
    def _require_primary_has_positive_cost(self) -> Self:
        if self.is_primary and self.baseline_one_way_bps <= 0:
            message = (
                "a primary cost assumption requires a strictly positive "
                "baseline_one_way_bps; zero-cost runs are diagnostic only and must "
                "set is_primary = false"
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _require_full_stress_ladder(self) -> Self:
        """Require the 2x/3x/5x ladder the validation protocol runs.

        A single upward stress value used to satisfy this check, so a
        configuration that cannot complete the robustness protocol validated
        cleanly and failed only at the promotion gate.
        """
        if self.baseline_one_way_bps <= 0:
            return self
        present = {round(value, 6) for value in self.stress_one_way_bps}
        missing = [
            f"{multiple}x ({round(self.baseline_one_way_bps * multiple, 6)} bps)"
            for multiple in (2, 3, 5)
            if round(self.baseline_one_way_bps * multiple, 6) not in present
        ]
        if missing:
            message = (
                "stress_one_way_bps must contain the mandatory cost ladder; missing "
                + ", ".join(missing)
            )
            raise ValueError(message)
        return self


class CashConfig(BaseModel):
    """Cash return assumption.

    The canonical primary result uses a constant 0%. A Treasury-yield series is
    a separate sensitivity and may not be merged into the primary result, so
    selecting it requires ``is_primary`` to be false.
    """

    model_config = _MODEL_CONFIG

    source: Literal["constant", "treasury_3m"] = "constant"
    constant_annualized_rate: NonNegativeFloat = 0.0
    is_primary: bool = True

    @model_validator(mode="after")
    def _require_primary_is_constant_zero(self) -> Self:
        if self.is_primary and self.source != "constant":
            message = (
                "the primary result must use the constant cash source; a Treasury-yield "
                "assumption is a sensitivity and must set is_primary = false"
            )
            raise ValueError(message)
        if self.is_primary and self.constant_annualized_rate != 0.0:
            message = "the primary cash assumption is 0%; a non-zero rate is a sensitivity"
            raise ValueError(message)
        return self


class RiskOverlayConfig(BaseModel):
    """The v2 managed overlay: hard guards, Kelly sizing, and regime detection.

    This section is inert for a v1 run -- the frozen research runner never reads
    it -- and normative for a managed run. It lives inside
    :class:`StrategyConfig` so that the configuration digest recorded in a run
    manifest covers the risk settings too. An overlay tuned outside the hashed
    configuration would make two runs with identical manifests produce different
    results, which is precisely the reproducibility failure the manifest exists
    to prevent.

    Every field is a *limit*, not a target. Nothing here can increase a position
    beyond what the v1 allocator proposed; see :mod:`amat.risk`.
    """

    model_config = _MODEL_CONFIG

    max_risk_per_trade: float = Field(default=0.01, gt=0.0, le=1.0)
    max_portfolio_heat: float = Field(default=0.06, gt=0.0, le=1.0)
    max_daily_loss: float = Field(default=0.03, gt=0.0, le=1.0)
    max_drawdown_halt: float = Field(default=0.20, gt=0.0, le=1.0)
    min_expectancy_trades: PositiveInt = 30
    min_expectancy: float = 0.0
    expectancy_window_sessions: PositiveInt = 252
    max_correlation: float = Field(default=0.80, ge=0.0, lt=1.0)
    min_effective_bets: float = Field(default=2.0, ge=1.0)
    max_positions: PositiveInt = 12
    min_grade: Literal[1, 2, 3] = 2

    use_kelly: bool = True
    kelly_fraction_multiplier: float = Field(default=0.25, gt=0.0, le=1.0)
    """Fraction of full Kelly actually applied.

    Full Kelly maximizes long-run growth only under a *known* edge. The edge here
    is estimated from a finite trailing sample, and estimation error in the win
    rate is amplified by the Kelly formula, so a fraction well below 1.0 is the
    standard defensive choice rather than a conservatism tax.
    """

    regime_clusters: PositiveInt = 3
    regime_fit_window_sessions: PositiveInt = 504
    regime_refit_interval_sessions: PositiveInt = 21
    #: Smallest multiplier a *measured* regime may justify.
    regime_risk_floor: float = Field(default=0.25, gt=0.0, le=1.0)
    #: Multiplier applied while no regime has been established -- the whole
    #: warm-up window, and any session the model cannot classify. Held in
    #: configuration, and therefore in the run digest, because it was previously
    #: an unexposed constant that silently governed a fifth of every backtest.
    regime_unknown_scalar: float = Field(default=1.0, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _require_trade_within_heat(self) -> Self:
        if self.max_risk_per_trade > self.max_portfolio_heat:
            message = (
                f"max_risk_per_trade ({self.max_risk_per_trade}) cannot exceed "
                f"max_portfolio_heat ({self.max_portfolio_heat}): one position would "
                "breach the aggregate limit on entry"
            )
            raise ValueError(message)
        return self


class WarmupConfig(BaseModel):
    """Minimum completed history before the strategy may hold a position."""

    model_config = _MODEL_CONFIG

    min_completed_sessions: PositiveInt = 252


class StrategyConfig(BaseModel):
    """The complete, frozen strategy definition for one run."""

    model_config = _MODEL_CONFIG

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    universe: UniverseSpec
    volatility: VolatilityConfig = VolatilityConfig()
    trend: TrendConfig = TrendConfig()
    covariance: CovarianceConfig = CovarianceConfig()
    constraints: ConstraintConfig = ConstraintConfig()
    cross_sectional: CrossSectionConfig = CrossSectionConfig()
    risk_budget: RiskBudgetConfig = RiskBudgetConfig()
    rebalance: RebalanceConfig = RebalanceConfig()
    execution: ExecutionConfig = ExecutionConfig()
    costs: CostConfig = CostConfig()
    cash: CashConfig = CashConfig()
    warmup: WarmupConfig = WarmupConfig()
    risk: RiskOverlayConfig = RiskOverlayConfig()

    @model_validator(mode="after")
    def _require_sufficient_warmup(self) -> Self:
        self.cross_sectional.require_horizons(len(self.trend.horizons_sessions))
        required = max(
            self.trend.max_horizon_sessions,
            self.covariance.lookback_sessions,
            self.volatility.min_periods_sessions,
        )
        # The risk budget reads payoffs, and a payoff needs a score, so its
        # history sits ON TOP of the trend horizon rather than beside it.
        # Without this the first decisions would rank assets on a handful of
        # observations and call it an information ratio.
        required = max(
            required,
            self.trend.max_horizon_sessions + self.risk_budget.required_warmup_sessions,
        )
        # The contract states an absolute floor of 252 completed sessions *plus*
        # enough history for the covariance estimator. Taking only the max of the
        # component lookbacks would let a short-horizon override collapse the
        # floor to volatility.min_periods_sessions (60), so the 252 is applied
        # independently rather than folded into the max.
        required = max(required, CONTRACT_MIN_WARMUP_SESSIONS)
        if self.warmup.min_completed_sessions < required:
            message = (
                f"warmup.min_completed_sessions ({self.warmup.min_completed_sessions}) "
                f"must be at least {required}: the contract floor of "
                f"{CONTRACT_MIN_WARMUP_SESSIONS} completed sessions, the longest trend "
                "horizon, the covariance lookback, and the volatility minimum period "
                "must all be satisfied"
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _require_feasible_caps(self) -> Self:
        caps = {cluster.name: cluster.gross_cap for cluster in self.universe.clusters}
        if sum(caps.values()) < self.constraints.max_gross_exposure:
            message = (
                f"cluster gross caps sum to {sum(caps.values()):.4f}, which is below "
                f"max_gross_exposure {self.constraints.max_gross_exposure:.4f}; the gross "
                "cap would be unreachable"
            )
            raise ValueError(message)
        # A cluster cap that the symbol cap makes non-binding is legal, not
        # infeasible: the contract applies caps in sequence and imposes no
        # reachability requirement. Rejecting it here made the canonical universe
        # unloadable under a tightened max_symbol_weight, which is exactly the
        # constraint sensitivity the validation protocol requires.
        return self

    def to_universe(self) -> Universe:
        """Convert the declared universe to its immutable domain form."""
        return self.universe.to_domain()
