"""Unit-variance joint innovations for declared hypothetical market processes.

Student's t and Laplace use a shared random scale on a correlated Gaussian
vector. This preserves cross-asset covariance and creates simultaneous tail
events. Skewed shocks mix standardized skew-normal common and asset factors;
their marginals are convolutions, not a fitted skew-t distribution. Parameters
are stress assumptions, never estimates of future return probabilities.
"""

from __future__ import annotations

import numpy as np

DISTRIBUTIONS = ("gaussian", "student_t", "laplace", "skewed", "gaussian_mixture")
SKEW_SHAPE = -4.0
MIXTURE_PROBABILITY = 0.08
MIXTURE_SCALES = (0.65, 2.5)
GARCH_ALPHA = 0.08
GARCH_BETA = 0.88
GARCH_LEVERAGE = 0.04


def _skew_factors(values: np.ndarray, *, seed: int) -> np.ndarray:
    delta = SKEW_SHAPE / np.sqrt(1 + SKEW_SHAPE**2)
    other = np.random.default_rng(seed).standard_normal(values.shape)
    mean = delta * np.sqrt(2 / np.pi)
    variance = 1 - 2 * delta**2 / np.pi
    return np.asarray(
        (delta * np.abs(values) + np.sqrt(1 - delta**2) * other - mean) / np.sqrt(variance)
    )


def joint_innovations(
    *,
    count: int,
    assets: int,
    seed: int,
    distribution: str = "gaussian",
    correlation: float | np.ndarray = 0.35,
    degrees_of_freedom: float = 5.0,
) -> np.ndarray:
    """Zero-mean, unit-variance shocks with declared nonnegative correlation.

    ``correlation`` is a scalar or one value per session, common to every asset
    pair. Streams are separate by purpose: extending the horizon preserves the
    entire existing prefix. Student's t requires df > 2 because scaling an
    infinite-variance distribution to historical volatility is undefined.
    """
    if distribution not in DISTRIBUTIONS:
        raise ValueError(f"unknown innovation distribution: {distribution}")
    if type(count) is not int or count < 1:
        raise ValueError("count must be a positive integer")
    if type(assets) is not int or assets < 1:
        raise ValueError("assets must be a positive integer")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    rho = np.asarray(correlation, dtype=float)
    if rho.shape == (count,):
        rho = rho[:, None]
    if rho.shape not in ((), (count, 1)):
        raise ValueError("correlation must be scalar or have one value per session")
    if not np.isfinite(rho).all() or np.any((rho < 0) | (rho > 1)):
        raise ValueError("correlation must be finite and between zero and one")
    if distribution == "student_t" and (
        not np.isfinite(degrees_of_freedom) or degrees_of_freedom <= 2  # noqa: PLR2004
    ):
        raise ValueError("Student's t degrees_of_freedom must be finite and greater than 2")

    common = np.random.default_rng(seed).standard_normal((count, 1))
    independent = np.random.default_rng(seed + 1).standard_normal((count, assets))
    if distribution == "skewed":
        common = _skew_factors(common, seed=seed + 3)
        independent = _skew_factors(independent, seed=seed + 4)
    values = np.sqrt(rho) * common + np.sqrt(1 - rho) * independent
    scales = np.random.default_rng(seed + 3)
    if distribution == "student_t":
        values *= np.sqrt(
            (degrees_of_freedom - 2) / scales.chisquare(degrees_of_freedom, (count, 1))
        )
    elif distribution == "laplace":
        values *= np.sqrt(scales.exponential(size=(count, 1)))
    elif distribution == "gaussian_mixture":
        low, high = MIXTURE_SCALES
        scale = np.where(scales.random((count, 1)) < MIXTURE_PROBABILITY, high, low)
        values *= scale / np.sqrt(
            (1 - MIXTURE_PROBABILITY) * low**2 + MIXTURE_PROBABILITY * high**2
        )
    return values


def clustered_innovations(innovations: np.ndarray) -> np.ndarray:
    """Causal GJR-GARCH(1,1), initially at unit unconditional variance.

    h[t+1] = omega + alpha*x[t]^2 + beta*h[t] + gamma*x[t]^2*I(x[t]<0).
    Omega makes unconditional variance one for symmetric unit-variance input.
    Negative shocks increase tomorrow's variance more than equal positive ones.
    """
    if innovations.ndim != 2 or not innovations.size or not np.isfinite(innovations).all():  # noqa: PLR2004
        raise ValueError("clustered innovations require a finite nonempty two-dimensional array")
    variance = np.ones(innovations.shape[1])
    omega = 1 - GARCH_ALPHA - GARCH_BETA - GARCH_LEVERAGE / 2
    result = np.empty_like(innovations, dtype=float)
    for index, row in enumerate(innovations):
        result[index] = row * np.sqrt(variance)
        squared = result[index] ** 2
        variance = (
            omega
            + GARCH_ALPHA * squared
            + GARCH_BETA * variance
            + GARCH_LEVERAGE * squared * (result[index] < 0)
        )
    return result
