"""Statistical properties, not snapshots of particular random draws."""

import json

import numpy as np
import pytest

from amat.metrics.distribution import return_distribution_summary
from amat.validation.distributions import (
    DISTRIBUTIONS,
    clustered_innovations,
    joint_innovations,
)


@pytest.mark.parametrize("distribution", DISTRIBUTIONS)
def test_joint_families_preserve_mean_variance_and_cross_asset_dependence(distribution):
    values = joint_innovations(
        count=150_000, assets=3, seed=42, distribution=distribution, correlation=0.4
    )
    np.testing.assert_allclose(values.mean(axis=0), 0, atol=0.015)
    np.testing.assert_allclose(values.var(axis=0), 1, atol=0.05)
    correlation = np.corrcoef(values.T)
    np.testing.assert_allclose(correlation[np.triu_indices(3, 1)], 0.4, atol=0.025)


@pytest.mark.parametrize("distribution", DISTRIBUTIONS)
def test_joint_draws_are_repeatable_prefix_stable_and_seed_sensitive(distribution):
    args = {"count": 100, "assets": 3, "seed": 17, "distribution": distribution}
    values = joint_innovations(**args)
    np.testing.assert_array_equal(values, joint_innovations(**args))
    np.testing.assert_array_equal(values, joint_innovations(**{**args, "count": 150})[:100])
    assert not np.array_equal(values, joint_innovations(**{**args, "seed": 18}))


def test_fat_tails_skewness_and_clustering_are_present():
    families = {
        name: joint_innovations(count=150_000, assets=1, seed=45, distribution=name)[:, 0]
        for name in DISTRIBUTIONS
    }
    gaussian = families["gaussian"]
    for name in ("student_t", "laplace", "gaussian_mixture"):
        assert np.mean(np.abs(families[name]) > 4) > 10 * np.mean(np.abs(gaussian) > 4)
        assert return_distribution_summary(families[name])["excess_kurtosis"] > 1
    assert return_distribution_summary(families["skewed"])["skewness"] < -0.4
    clustered = clustered_innovations(gaussian[:, None])[:, 0]
    assert return_distribution_summary(clustered)["squared_return_autocorrelation_lag1"] > 0.15
    assert abs(return_distribution_summary(gaussian)["squared_return_autocorrelation_lag1"]) < 0.02


def test_more_extreme_student_tail_and_garch_negative_shock_response():
    light = joint_innovations(count=200_000, assets=1, seed=23, distribution="student_t")
    heavy = joint_innovations(
        count=200_000, assets=1, seed=23, distribution="student_t", degrees_of_freedom=3
    )
    assert np.mean(np.abs(heavy) > 8) > 2 * np.mean(np.abs(light) > 8)
    positive = clustered_innovations(np.array([[3.0], [1.0]]))
    negative = clustered_innovations(np.array([[-3.0], [1.0]]))
    assert negative[1, 0] > positive[1, 0]
    full = clustered_innovations(light)
    np.testing.assert_array_equal(full[:100], clustered_innovations(light[:100]))


@pytest.mark.parametrize(
    "overrides",
    [
        {"count": 0},
        {"count": True},
        {"assets": 0},
        {"seed": -1},
        {"distribution": "unknown"},
        {"correlation": -0.1},
        {"correlation": np.nan},
        {"correlation": np.ones((3, 3))},
        {"distribution": "student_t", "degrees_of_freedom": 2},
        {"distribution": "student_t", "degrees_of_freedom": np.inf},
    ],
)
def test_invalid_distribution_parameters_are_rejected(overrides):
    with pytest.raises(ValueError):
        joint_innovations(**{"count": 100, "assets": 3, "seed": 2, **overrides})


def test_exact_tail_mass_includes_a_fractional_boundary_and_ties():
    # Worst 5% of 30 observations = 1.5 observations, not every tied VaR day.
    returns = np.array([-0.2, -0.1, -0.1] + [0.01] * 27)
    result = return_distribution_summary(returns)
    assert result["loss_var_95"] == pytest.approx(0.1)
    assert result["loss_cvar_95"] == pytest.approx((0.2 + 0.5 * 0.1) / 1.5)
    assert result["loss_cvar_99"] == pytest.approx(0.2)
    assert result["tail_observations_95"] == 1.5
    assert result["loss_cvar_99"] >= result["loss_cvar_95"] >= result["loss_var_95"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("value", [0.0, 0.01, -0.01])
def test_constant_market_has_finite_tail_losses_and_undefined_shape(value):
    result = return_distribution_summary(np.full(100, value))
    assert result["skewness"] is None
    assert result["excess_kurtosis"] is None
    assert result["squared_return_autocorrelation_lag1"] is None
    assert result["loss_var_95"] == max(0, -value)
    assert result["loss_cvar_99"] == pytest.approx(max(0, -value))
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("values", [[], [np.nan], [np.inf], [[0.1, 0.2]]])
def test_invalid_return_samples_are_rejected(values):
    with pytest.raises(ValueError):
        return_distribution_summary(np.array(values))
