"""Portfolio stage: covariance, volatility targeting, constraints, risk contribution.

Implements :class:`amat.boundaries.DecisionBoundary` together with
:mod:`amat.features`.

Contract for this package:

* the covariance estimate uses only rows through the decision session;
* constraints apply in the fixed order stated by the strategy contract, and
  forecast risk is recomputed after clipping;
* residual exposure stays in cash; the portfolio is never re-levered beyond the
  gross cap to recover the volatility target;
* when every score is zero the portfolio is fully in cash.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.covariance import LedoitWolf

from amat.config.models import ConstraintConfig, CovarianceConfig, StrategyConfig
from amat.domain.instruments import Universe
from amat.domain.types import Symbol, Weight
from amat.errors import CausalityError, DataIntegrityError
from amat.portfolio.cross_section import select_cross_section
from amat.portfolio.risk_budget import budget_multipliers, signal_payoffs, trailing_quality

#: A market panel is always 2-D: (sessions, symbols).
_PANEL_NDIM = 2

__all__ = [
    "AllocationResult",
    "PrecomputedDecisionInputs",
    "apply_constraints",
    "decide",
    "forecast_volatility",
    "ledoit_wolf_covariance",
    "precompute_decision_inputs",
    "raw_exposure",
    "target_weights",
    "volatility_scaled_weights",
]


@dataclass(frozen=True, slots=True)
class AllocationResult:
    """Target weights plus the risk figures that justify them.

    Attributes:
        weights: Non-negative target weight per symbol, in universe order.
        forecast_volatility_pre: Annualized forecast volatility of the
            unconstrained volatility-targeted portfolio.
        forecast_volatility_post: Annualized forecast volatility recomputed
            *after* constraint clipping. The contract requires this recompute:
            clipping changes the risk of the portfolio actually held, and
            reporting the pre-clip figure would overstate how tightly the 8%
            target was met.
        gross_exposure: Sum of weights.
        cash_weight: ``1 - gross_exposure``. Residual always stays in cash.
        binding: Names of the constraints that actually bound, for attribution.
    """

    weights: np.ndarray
    forecast_volatility_pre: float
    forecast_volatility_post: float
    gross_exposure: float
    cash_weight: float
    binding: tuple[str, ...]

    def as_mapping(self, universe: Universe) -> dict[Symbol, Weight]:
        """Weights keyed by symbol, in the universe's deterministic order."""
        return dict(zip(universe.symbols, (float(w) for w in self.weights), strict=True))


def ledoit_wolf_covariance(returns: np.ndarray, config: CovarianceConfig) -> np.ndarray:
    """Annualized covariance with Ledoit-Wolf shrinkage.

    Args:
        returns: ``(T, N)`` log-return window ending at the decision session.
            The caller is responsible for having sliced it causally; this
            function cannot see the calendar.
        config: Lookback, estimator, and annualization settings.

    Returns:
        ``(N, N)`` annualized covariance matrix.

    Raises:
        DataIntegrityError: if the window is shorter than the configured
            lookback, contains non-finite values, or the estimator is unknown.
    """
    if returns.ndim != _PANEL_NDIM:
        message = f"returns must be a 2-D (T, N) window, received shape {returns.shape}"
        raise DataIntegrityError(message)
    if returns.shape[0] < config.lookback_sessions:
        message = (
            f"covariance needs {config.lookback_sessions} sessions, received {returns.shape[0]}"
        )
        raise DataIntegrityError(message)
    if not np.all(np.isfinite(returns)):
        message = "covariance window contains non-finite returns; fail closed rather than impute"
        raise DataIntegrityError(message)
    if config.estimator != "ledoit_wolf":
        message = f"unsupported covariance estimator: {config.estimator!r}"
        raise DataIntegrityError(message)

    window = returns[-config.lookback_sessions :]
    # assume_centered=False lets the estimator remove the sample mean, matching
    # the sample-covariance convention the shrinkage target is defined against.
    estimator = LedoitWolf(assume_centered=False).fit(window)
    return np.asarray(estimator.covariance_) * config.annualization_sessions


def raw_exposure(long_score: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """Inverse-volatility raw exposure, ``q = long_score / sigma``.

    Raises:
        DataIntegrityError: on a non-positive sigma, which would make the
            quotient diverge instead of failing closed.
    """
    if np.any(~np.isfinite(sigma)) or np.any(sigma <= 0):
        message = "sigma must be finite and strictly positive for every sized symbol"
        raise DataIntegrityError(message)
    quotient = np.asarray(long_score, dtype=float) / np.asarray(sigma, dtype=float)
    return np.asarray(quotient, dtype=float)


def forecast_volatility(weights: np.ndarray, covariance: np.ndarray) -> float:
    """Annualized forecast volatility, ``sqrt(w' C w)``."""
    variance = float(weights @ covariance @ weights)
    return float(np.sqrt(max(variance, 0.0)))


def volatility_scaled_weights(
    exposure: np.ndarray,
    covariance: np.ndarray,
    target_annualized_volatility: float,
) -> tuple[np.ndarray, float]:
    """Scale raw exposure to hit the annualized volatility target.

    Returns:
        The scaled weights and the pre-scaling forecast volatility.

    A zero forecast volatility means every score was zero, which the contract
    resolves as 100% cash rather than as an undefined scaling.
    """
    pre = forecast_volatility(exposure, covariance)
    if pre <= 0.0:
        return np.zeros_like(exposure), 0.0
    return exposure * (target_annualized_volatility / pre), pre


def apply_constraints(
    weights: np.ndarray,
    universe: Universe,
    config: ConstraintConfig,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Apply symbol, cluster, and gross caps in the contract's fixed order.

    Order is normative and not an implementation detail: symbol cap, then
    cluster gross caps, then the overall gross cap. Each stage can only reduce
    exposure, so the result is monotone and the order is what makes it
    reproducible.

    Cluster and gross breaches are resolved by scaling the offending group
    proportionally rather than by truncating the largest position, so relative
    conviction inside a cluster is preserved.

    Returns:
        The constrained weights and the names of the constraints that bound.
    """
    constrained = np.clip(np.asarray(weights, dtype=float), 0.0, None)
    binding: list[str] = []

    # 1. symbol cap
    if np.any(constrained > config.max_symbol_weight):
        binding.append("symbol_cap")
        constrained = np.minimum(constrained, config.max_symbol_weight)

    # 2. cluster gross caps
    symbols = universe.symbols
    index_of = {symbol: position for position, symbol in enumerate(symbols)}
    for cluster in universe.clusters:
        members = [index_of[symbol] for symbol in cluster.symbols if symbol in index_of]
        if not members:
            continue
        gross = float(constrained[members].sum())
        if gross > cluster.gross_cap and gross > 0:
            binding.append(f"cluster_cap:{cluster.name}")
            constrained[members] *= cluster.gross_cap / gross

    # 3. gross exposure cap
    gross = float(constrained.sum())
    if gross > config.max_gross_exposure and gross > 0:
        binding.append("gross_cap")
        constrained *= config.max_gross_exposure / gross

    # 4. risk-contribution limit is unset for v1; enabling it is a new version.
    if config.risk_contribution_limit is not None:  # pragma: no cover - not enabled in v1
        message = "risk_contribution_limit is not implemented for v1"
        raise DataIntegrityError(message)

    return constrained, tuple(binding)


def target_weights(
    long_score: np.ndarray,
    sigma: np.ndarray,
    covariance: np.ndarray,
    universe: Universe,
    config: ConstraintConfig,
) -> AllocationResult:
    """Full allocation: exposure, volatility targeting, constraints, cash residual.

    Step 5 of the contract -- recomputing forecast risk after clipping -- is
    performed here and reported on the result. Step 6 is enforced structurally:
    the residual is whatever the gross cap leaves, and there is no code path
    that re-levers to recover the target.
    """
    exposure = raw_exposure(long_score, sigma)
    if not np.any(exposure > 0):
        # Every score dead-banded to zero: 100% cash, by contract.
        zeros = np.zeros_like(exposure)
        return AllocationResult(
            weights=zeros,
            forecast_volatility_pre=0.0,
            forecast_volatility_post=0.0,
            gross_exposure=0.0,
            cash_weight=1.0,
            binding=("all_scores_zero",),
        )

    scaled, pre = volatility_scaled_weights(
        exposure, covariance, config.target_annualized_volatility
    )
    constrained, binding = apply_constraints(scaled, universe, config)
    post = forecast_volatility(constrained, covariance)
    gross = float(constrained.sum())

    if config.allow_releverage_after_clipping:  # pragma: no cover - pinned False by the type
        message = "re-levering after clipping is forbidden by the strategy contract"
        raise DataIntegrityError(message)

    return AllocationResult(
        weights=constrained,
        forecast_volatility_pre=pre,
        forecast_volatility_post=post,
        gross_exposure=gross,
        cash_weight=1.0 - gross,
        binding=binding,
    )


@dataclass(frozen=True, slots=True)
class PrecomputedDecisionInputs:
    """Feature panels computed once, read row-by-row across a session loop.

    Every feature recursion is strictly causal -- row ``t`` reads rows at or
    before ``t`` only -- so row ``t`` of a full-panel pass is bit-identical to
    the last row of a pass over the prefix ``adjusted_closes[: t + 1]``. That
    equivalence is what makes this cache safe: :func:`decide` produces the
    same allocation from it that it would produce recomputing from scratch,
    and the future-mutation leakage tests police the property directly by
    rewriting rows after ``t`` and asserting outputs through ``t`` unchanged.

    Without the cache a ``T``-session backtest recomputes ``O(T)`` feature
    rows per decision -- ``O(T^2)`` for the run, and measured at ~91% of
    backtest runtime. With it, feature work is ``O(T)`` once.

    Attributes:
        returns: ``(T, N)`` single-session log returns; row 0 is ``nan``.
        sigma_panel: ``(T, N)`` blended annualized volatility.
        long_scores: ``(T, N)`` dead-banded long-or-cash trend scores.
    """

    returns: np.ndarray
    sigma_panel: np.ndarray
    long_scores: np.ndarray
    horizon_scores: np.ndarray | None = None
    """``(H, T, N)`` per-horizon trends, or ``None``. Populated only when a
    configuration actually reads them, because it is ``H`` times the size of
    the panels beside it and every session loop holds it in memory."""
    signal_quality: np.ndarray | None = None
    """``(T, N)`` trailing information ratio of the signal's own payoff per
    asset, or ``None`` when no risk budget is configured. Strictly backward
    looking at every row, which is what lets the cached path stay identical to
    recomputing from the visible prefix."""

    def __post_init__(self) -> None:
        if not (self.returns.shape == self.sigma_panel.shape == self.long_scores.shape):
            message = (
                "precomputed feature panels disagree in shape: "
                f"returns {self.returns.shape}, sigma {self.sigma_panel.shape}, "
                f"long_scores {self.long_scores.shape}"
            )
            raise DataIntegrityError(message)


def precompute_decision_inputs(
    strategy: StrategyConfig, adjusted_closes: np.ndarray
) -> PrecomputedDecisionInputs:
    """Compute every feature panel :func:`decide` needs, in one causal pass."""
    # Deferred import: amat.features and amat.portfolio are peers in the
    # layer order, so a module-scope import would create a cycle.
    from amat import features  # noqa: PLC0415

    returns = features.log_returns(adjusted_closes)
    sigma_panel = features.annualized_volatility(returns, strategy.volatility)
    scores = features.trend_scores(
        adjusted_closes,
        sigma_panel,
        strategy.trend,
        annualization_sessions=strategy.volatility.annualization_sessions,
    )
    horizon_scores = None
    if strategy.cross_sectional.min_horizon_agreement > 0:
        horizon_scores = features.horizon_trend_scores(
            adjusted_closes,
            sigma_panel,
            strategy.trend,
            annualization_sessions=strategy.volatility.annualization_sessions,
        )
    long = features.long_scores(scores, strategy.trend)
    quality = None
    if strategy.risk_budget.enabled:
        quality = trailing_quality(
            signal_payoffs(long, returns),
            lookback=strategy.risk_budget.lookback_sessions,
            min_periods=strategy.risk_budget.min_periods_sessions,
            measure=strategy.risk_budget.measure,
        )
    return PrecomputedDecisionInputs(
        returns=returns,
        sigma_panel=sigma_panel,
        long_scores=long,
        horizon_scores=horizon_scores,
        signal_quality=quality,
    )


def decide(
    strategy: StrategyConfig,
    adjusted_closes: np.ndarray,
    universe: Universe,
    *,
    decision_index: int,
    precomputed: PrecomputedDecisionInputs | None = None,
) -> AllocationResult:
    """Produce target weights for one decision session from a price panel.

    This is the single entry point both runners call, so the research runner and
    the event-driven adapter cannot diverge in signal or allocation mathematics.

    Args:
        strategy: Validated configuration.
        adjusted_closes: ``(T, N)`` adjusted-close panel in universe order.
        universe: Column ordering and cluster membership.
        decision_index: Row index of the completed decision session. Rows after
            it are not read.
        precomputed: Optional cache from :func:`precompute_decision_inputs`
            over this exact panel. Bit-identical to recomputing (the feature
            recursions are strictly causal); callers looping over decision
            indices pass it to avoid ``O(T^2)`` recomputation. ``None``
            recomputes from the visible prefix.

    Raises:
        CausalityError: if the decision row is out of range.
        DataIntegrityError: if warm-up is unsatisfied at that row, or a
            supplied cache does not match the panel's shape.
    """
    total_rows = adjusted_closes.shape[0]
    if not 0 <= decision_index < total_rows:
        message = f"decision_index {decision_index} outside panel of {total_rows} sessions"
        raise CausalityError(message)

    completed = decision_index + 1
    if completed < strategy.warmup.min_completed_sessions:
        message = (
            f"warm-up unsatisfied: {completed} completed sessions at index "
            f"{decision_index}, need {strategy.warmup.min_completed_sessions}"
        )
        raise DataIntegrityError(message)

    if precomputed is None:
        # Slice first, compute second. Nothing after the decision session is
        # ever materialized, so a look-ahead defect cannot hide inside a later
        # reduction. The cached path earns the same guarantee from causal
        # recursion, policed by the future-mutation leakage tests.
        precomputed = precompute_decision_inputs(strategy, adjusted_closes[: decision_index + 1])
    elif precomputed.returns.shape != adjusted_closes.shape:
        message = (
            f"precomputed features have shape {precomputed.returns.shape} but the "
            f"panel has shape {adjusted_closes.shape}; a misaligned cache would "
            "decide every session from some other session's features"
        )
        raise DataIntegrityError(message)

    long_score = precomputed.long_scores[decision_index]
    sigma = precomputed.sigma_panel[decision_index]

    usable = np.isfinite(long_score) & np.isfinite(sigma) & (sigma > 0)
    safe_scores = np.where(usable, np.nan_to_num(long_score, nan=0.0), 0.0)
    safe_sigma = np.where(usable, sigma, 1.0)

    if strategy.cross_sectional.enabled:
        # After the dead-band and the usability mask, before any sizing: the
        # selection decides membership, and every cap, the volatility target,
        # and the cash residual then apply to the survivors exactly as they
        # apply to the full set when selection is off.
        horizon_row = (
            precomputed.horizon_scores[:, decision_index, :]
            if precomputed.horizon_scores is not None
            else None
        )
        safe_scores = select_cross_section(
            safe_scores, strategy.cross_sectional, horizon_z=horizon_row
        ).scores

    if strategy.risk_budget.enabled:
        # Between the signal and the sizing: the budget decides who carries the
        # risk, and the volatility target below still decides how much there is.
        if precomputed.signal_quality is None:
            message = (
                "risk_budget is enabled but the cache carries no signal-quality panel; "
                "a budget silently skipped would misdescribe every weight in the run"
            )
            raise DataIntegrityError(message)
        multipliers = budget_multipliers(
            precomputed.signal_quality[decision_index], strategy.risk_budget
        )
        safe_scores = safe_scores * multipliers

    covariance = ledoit_wolf_covariance(
        precomputed.returns[1 : decision_index + 1], strategy.covariance
    )
    return target_weights(safe_scores, safe_sigma, covariance, universe, strategy.constraints)
