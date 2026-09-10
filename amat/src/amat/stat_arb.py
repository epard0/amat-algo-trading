"""Causal statistical-arbitrage structure diagnostics.

This module measures whether two assets have historically displayed a
tradeable convergence structure.  It does not emit portfolio weights or trade
instructions.  A pair can pass these descriptive gates and still fail after
costs, borrow constraints, multiple-testing correction, or forward validation.

For log prices ``y`` and ``x`` the fitted spread is

``s_t = y_t - (alpha + beta * x_t)``.

The augmented Dickey-Fuller regression is

``Delta s_t = c + gamma * s_(t-1) + sum(delta_i * Delta s_(t-i)) + error_t``.

The reported ADF value is the t-statistic for ``gamma``.  It is intentionally
not presented as a p-value: finite-sample critical values depend on the exact
deterministic terms and sample.  The default -2.86 screen is an approximate 5%
large-sample intercept-only threshold and is only one eligibility guard.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from amat.errors import DataIntegrityError

__all__ = [
    "PairSelectionConfig",
    "PairStructure",
    "analyze_pair",
    "scan_pairs",
]

_MIN_OBSERVATIONS = 60
_DEFAULT_ADF_THRESHOLD = -2.86
_PRICE_PANEL_DIMENSIONS = 2


@dataclass(frozen=True, slots=True)
class PairSelectionConfig:
    """Transparent structure gates applied to every candidate pair."""

    minimum_abs_return_correlation: float = 0.50
    maximum_adf_t_statistic: float = _DEFAULT_ADF_THRESHOLD
    minimum_half_life_sessions: float = 1.0
    maximum_half_life_sessions: float = 63.0
    minimum_zero_crossings: int = 4
    require_positive_hedge_ratio: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.minimum_abs_return_correlation <= 1.0:
            raise ValueError("minimum_abs_return_correlation must be in [0, 1]")
        if self.minimum_half_life_sessions <= 0:
            raise ValueError("minimum_half_life_sessions must be positive")
        if self.maximum_half_life_sessions <= self.minimum_half_life_sessions:
            raise ValueError("maximum_half_life_sessions must exceed the minimum")
        if self.minimum_zero_crossings < 1:
            raise ValueError("minimum_zero_crossings must be positive")


@dataclass(frozen=True, slots=True)
class PairStructure:
    """Measured convergence structure for one oriented pair ``left ~ right``."""

    left_symbol: str
    right_symbol: str
    observations: int
    hedge_ratio: float
    intercept: float
    return_correlation: float
    spread_mean: float
    spread_std: float
    spread_zscore: float
    adf_t_statistic: float
    adf_lags: int
    half_life_sessions: float | None
    zero_crossings: int
    crossing_rate: float
    eligible: bool
    rejection_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_symbol": self.left_symbol,
            "right_symbol": self.right_symbol,
            "observations": self.observations,
            "hedge_ratio": self.hedge_ratio,
            "intercept": self.intercept,
            "return_correlation": self.return_correlation,
            "spread_mean": self.spread_mean,
            "spread_std": self.spread_std,
            "spread_zscore": self.spread_zscore,
            "adf_t_statistic": self.adf_t_statistic,
            "adf_lags": self.adf_lags,
            "half_life_sessions": self.half_life_sessions,
            "zero_crossings": self.zero_crossings,
            "crossing_rate": self.crossing_rate,
            "eligible": self.eligible,
            "rejection_reasons": list(self.rejection_reasons),
        }


def _validated_prices(values: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim != 1:
        raise DataIntegrityError(f"{name} prices must be one-dimensional")
    if len(array) < _MIN_OBSERVATIONS:
        raise DataIntegrityError(
            f"pair analysis needs at least {_MIN_OBSERVATIONS} observations, got {len(array)}"
        )
    if not np.all(np.isfinite(array)) or np.any(array <= 0.0):
        raise DataIntegrityError(f"{name} prices must be finite and strictly positive")
    return array


def _ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float, np.ndarray]:
    design = np.column_stack([np.ones(len(x)), x])
    coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    if rank < design.shape[1]:
        raise DataIntegrityError("pair regression is rank deficient")
    intercept, beta = coefficients
    spread = y - design @ coefficients
    return float(intercept), float(beta), np.asarray(spread, dtype=float)


def _adf(spread: np.ndarray, lags: int) -> tuple[float, float | None]:
    if lags < 0:
        raise ValueError("adf_lags cannot be negative")
    differences = np.diff(spread)
    start = lags
    response = differences[start:]
    lagged_level = spread[start:-1]
    columns = [np.ones(len(response)), lagged_level]
    for lag in range(1, lags + 1):
        columns.append(differences[start - lag : -lag])
    design = np.column_stack(columns)
    if len(response) <= design.shape[1]:
        raise DataIntegrityError("not enough observations for the requested ADF lags")

    coefficients, _, rank, _ = np.linalg.lstsq(design, response, rcond=None)
    if rank < design.shape[1]:
        raise DataIntegrityError("ADF regression is rank deficient")
    residuals = response - design @ coefficients
    degrees_of_freedom = len(response) - design.shape[1]
    residual_variance = float(residuals @ residuals) / degrees_of_freedom
    covariance = residual_variance * np.linalg.pinv(design.T @ design)
    standard_error = math.sqrt(max(float(covariance[1, 1]), 0.0))
    gamma = float(coefficients[1])
    statistic = gamma / standard_error if standard_error > 0.0 else 0.0

    autoregressive_root = 1.0 + gamma
    half_life = None
    if 0.0 < autoregressive_root < 1.0:
        half_life = -math.log(2.0) / math.log(autoregressive_root)
    return statistic, half_life


def analyze_pair(
    left_prices: np.ndarray,
    right_prices: np.ndarray,
    *,
    left_symbol: str,
    right_symbol: str,
    config: PairSelectionConfig | None = None,
    adf_lags: int = 1,
) -> PairStructure:
    """Measure one pair using only the supplied historical prefix."""
    left = _validated_prices(left_prices, "left")
    right = _validated_prices(right_prices, "right")
    if len(left) != len(right):
        raise DataIntegrityError("left and right price histories must have equal length")
    rules = config or PairSelectionConfig()

    log_left = np.log(left)
    log_right = np.log(right)
    intercept, hedge_ratio, spread = _ols(log_left, log_right)
    spread_mean = float(spread.mean())
    spread_std = float(spread.std(ddof=1))
    if spread_std <= 0.0:
        raise DataIntegrityError("fitted pair spread has zero variance")
    zscore = (float(spread[-1]) - spread_mean) / spread_std
    correlation = float(np.corrcoef(np.diff(log_left), np.diff(log_right))[0, 1])
    if not math.isfinite(correlation):
        raise DataIntegrityError("pair return correlation is undefined")
    adf_statistic, half_life = _adf(spread, adf_lags)

    centered = spread - spread_mean
    nonzero_sign = np.sign(centered)
    crossings = int(np.sum(nonzero_sign[1:] * nonzero_sign[:-1] < 0.0))
    crossing_rate = crossings / max(len(centered) - 1, 1)

    rejection_reasons: list[str] = []
    if abs(correlation) < rules.minimum_abs_return_correlation:
        rejection_reasons.append("return_correlation")
    if adf_statistic > rules.maximum_adf_t_statistic:
        rejection_reasons.append("residual_stationarity")
    if half_life is None or not (
        rules.minimum_half_life_sessions <= half_life <= rules.maximum_half_life_sessions
    ):
        rejection_reasons.append("half_life")
    if crossings < rules.minimum_zero_crossings:
        rejection_reasons.append("zero_crossings")
    if rules.require_positive_hedge_ratio and hedge_ratio <= 0.0:
        rejection_reasons.append("hedge_ratio_direction")

    return PairStructure(
        left_symbol=left_symbol.upper(),
        right_symbol=right_symbol.upper(),
        observations=len(left),
        hedge_ratio=hedge_ratio,
        intercept=intercept,
        return_correlation=correlation,
        spread_mean=spread_mean,
        spread_std=spread_std,
        spread_zscore=zscore,
        adf_t_statistic=adf_statistic,
        adf_lags=adf_lags,
        half_life_sessions=half_life,
        zero_crossings=crossings,
        crossing_rate=crossing_rate,
        eligible=not rejection_reasons,
        rejection_reasons=tuple(rejection_reasons),
    )


def scan_pairs(
    adjusted_closes: np.ndarray,
    symbols: tuple[str, ...],
    *,
    lookback_sessions: int = 252,
    config: PairSelectionConfig | None = None,
    adf_lags: int = 1,
) -> tuple[PairStructure, ...]:
    """Analyze every unique pair on the trailing window and rank opportunities.

    Eligible pairs come first, then candidates are ordered by absolute current
    z-score (largest divergence first), ADF statistic (more negative first),
    and symbol names for deterministic ties.
    """
    prices = np.asarray(adjusted_closes, dtype=float)
    if prices.ndim != _PRICE_PANEL_DIMENSIONS:
        raise DataIntegrityError("adjusted_closes must be a (sessions, symbols) matrix")
    if prices.shape[1] != len(symbols):
        raise DataIntegrityError("symbol count does not match adjusted-closes columns")
    if len(set(symbols)) != len(symbols):
        raise DataIntegrityError("symbols must be unique")
    if lookback_sessions < _MIN_OBSERVATIONS:
        raise ValueError(f"lookback_sessions must be at least {_MIN_OBSERVATIONS}")
    if prices.shape[0] < lookback_sessions:
        raise DataIntegrityError(
            f"pair scan needs {lookback_sessions} sessions, panel has {prices.shape[0]}"
        )

    window = prices[-lookback_sessions:]
    structures = [
        analyze_pair(
            window[:, left],
            window[:, right],
            left_symbol=symbols[left],
            right_symbol=symbols[right],
            config=config,
            adf_lags=adf_lags,
        )
        for left, right in itertools.combinations(range(len(symbols)), 2)
    ]
    structures.sort(
        key=lambda item: (
            not item.eligible,
            -abs(item.spread_zscore),
            item.adf_t_statistic,
            item.left_symbol,
            item.right_symbol,
        )
    )
    return tuple(structures)
