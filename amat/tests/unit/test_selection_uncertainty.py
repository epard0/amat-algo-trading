import numpy as np
import pytest

from autoresearch.improvements.selection_uncertainty import simultaneous_growth_intervals


def evaluate(values):
    return simultaneous_growth_intervals(values, block_months=3, draws=300, seed=39)


def test_zero_difference_has_zero_interval():
    result = evaluate(np.zeros((24, 2)))
    assert result["simultaneous_lower"] == [0.0, 0.0]
    assert result["simultaneous_upper"] == [0.0, 0.0]
    assert result["untouched_holdout"] is False


def test_shared_resampling_preserves_identical_histories():
    values = np.random.default_rng(4).normal(0, 0.01, (36, 1))
    result = evaluate(np.repeat(values, 2, axis=1))
    assert result["simultaneous_lower"][0] == result["simultaneous_lower"][1]
    assert result == evaluate(np.repeat(values, 2, axis=1))


def test_adding_comparisons_cannot_narrow_first_interval():
    values = np.random.default_rng(2).normal(0, 0.01, (36, 8))
    single, family = evaluate(values[:, :1]), evaluate(values)
    assert family["simultaneous_lower"][0] <= single["simultaneous_lower"][0]
    assert family["simultaneous_upper"][0] >= single["simultaneous_upper"][0]


@pytest.mark.parametrize("values", [np.zeros(24), np.zeros((0, 2)), np.array([[0.0], [np.nan]])])
def test_invalid_inputs_fail_closed(values):
    with pytest.raises(ValueError):
        evaluate(values)
