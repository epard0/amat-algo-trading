"""Independent checks of split isolation, pairing, and small-sample honesty."""

import json
from datetime import date, timedelta

import numpy as np
import pytest

from autoresearch.genetic.validation import (
    ValidationProtocol,
    WalkForwardFold,
    make_null_mapping,
    paired_return_uncertainty,
    search_null_test,
)


def _fold():
    return WalkForwardFold("fold_1", date(2020, 1, 20), date(2020, 2, 9), date(2020, 2, 29))


def test_protocol_roundtrip_and_overlap():
    protocol = ValidationProtocol((_fold(),))
    assert ValidationProtocol.from_dict(json.loads(json.dumps(protocol.to_dict()))) == protocol
    with pytest.raises(ValueError, match="nonoverlapping"):
        ValidationProtocol(
            (
                _fold(),
                WalkForwardFold("fold2", date(2020, 2, 1), date(2020, 2, 20), date(2020, 3, 20)),
            )
        )
    with pytest.raises(ValueError, match="unique"):
        ValidationProtocol((_fold(),), seeds=(11, 11))
    with pytest.raises(ValueError, match="integer"):
        ValidationProtocol((_fold(),), null_replicates=True)
    with pytest.raises(ValueError, match="search-run budget"):
        ValidationProtocol((_fold(),), null_replicates=100_000)
    with pytest.raises(TypeError):
        ValidationProtocol.from_dict({**protocol.to_dict(), "unknown": 1})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "../escape"},
        {"fit_end": date(2020, 2, 9)},
        {"selection_end": "2020-02-09"},
    ],
)
def test_fold_invalid(kwargs):
    with pytest.raises(ValueError):
        WalkForwardFold(**{**vars(_fold()), **kwargs})


def test_null_mapping_preserves_joint_rows_and_boundaries():
    sessions = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(65))
    eligible = np.ones(65, dtype=bool)
    eligible[[2, 24, 43]] = False
    mapping, metadata = make_null_mapping(sessions, eligible, _fold(), np.random.default_rng(7))
    same, _ = make_null_mapping(sessions, eligible, _fold(), np.random.default_rng(7))
    np.testing.assert_array_equal(mapping, same)
    assert sorted(mapping.tolist()) == list(range(65))
    assert np.array_equal(mapping[~eligible], np.flatnonzero(~eligible))
    assert np.array_equal(mapping[60:], np.arange(60, 65))
    rows = np.arange(65 * 3, dtype=float).reshape(65, 3)
    rows[7, 1] = np.nan
    for start, end in ((0, 20), (20, 40), (40, 60)):
        active = np.flatnonzero(eligible[start:end]) + start
        assert set(mapping[active]) == set(active)
        assert np.all(mapping[active] != active)
        assert np.isnan(rows[mapping[active]]).sum() == np.isnan(rows[active]).sum()
    assert len(metadata["segments"]) == 3
    assert "Noncausal" in metadata["limitation"]


def test_null_rejects_short_or_misaligned_data():
    sessions = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(60))
    eligible = np.ones(60, dtype=bool)
    eligible[:15] = False
    with pytest.raises(ValueError, match="too few"):
        make_null_mapping(sessions, eligible, _fold(), np.random.default_rng(0))
    with pytest.raises(ValueError, match="boolean"):
        make_null_mapping(sessions, np.ones(60), _fold(), np.random.default_rng(0))
    with pytest.raises(ValueError, match="increasing"):
        make_null_mapping(sessions[::-1], eligible, _fold(), np.random.default_rng(0))


def test_paired_bootstrap_same_series_has_exact_zero_difference():
    baseline = np.random.default_rng(0).normal(0.001, 0.02, 80)
    result = paired_return_uncertainty(
        baseline, baseline, replicates=100, block_sessions=5, seed=7, segments=(40, 40)
    )
    assert result["annualized_mean_difference_ci95"] == [0, 0]
    assert result["sharpe_difference_ci95"] == [0, 0]
    assert result["sessions"] == 80
    assert result["approximate_independent_blocks"] == 16
    assert result == paired_return_uncertainty(
        baseline, baseline, replicates=100, block_sessions=5, seed=7, segments=(40, 40)
    )


def test_bootstrap_preserves_fold_counts_and_constant_offset():
    baseline = np.linspace(-0.02, 0.02, 40)
    candidate = baseline + np.repeat([0.001, 0.005], 20)
    result = paired_return_uncertainty(
        candidate, baseline, replicates=100, block_sessions=6, seed=9, segments=(20, 20)
    )
    # Every draw retains 20 dates from each fold; combining folds before drawing would vary.
    np.testing.assert_allclose(result["annualized_mean_difference_ci95"], [0.756, 0.756])


def test_undefined_sharpe_is_explicit_not_silently_dropped():
    result = paired_return_uncertainty(
        np.ones(20) * 0.001, np.zeros(20), replicates=30, block_sessions=5, seed=0
    )
    assert result["sharpe_difference"] is None
    assert result["sharpe_difference_ci95"] is None
    assert result["undefined_sharpe_replicates"] == 30
    np.testing.assert_allclose(result["annualized_mean_difference_ci95"], [0.252, 0.252])


@pytest.mark.parametrize(
    "candidate,segments,block",
    [
        ([0, 1, 2, np.nan], None, 2),
        ([0, 1, 2, 3], (3,), 2),
        ([0, 1, 2, 3], None, 5),
    ],
)
def test_bootstrap_rejects_invalid(candidate, segments, block):
    with pytest.raises(ValueError):
        paired_return_uncertainty(
            np.array(candidate),
            np.zeros(4),
            replicates=10,
            block_sessions=block,
            seed=0,
            segments=segments,
        )


def test_null_p_value_ties_resolution_and_wilson():
    result = search_null_test(2, [0, 1, 2, 3])
    assert result["p_value"] == 0.6
    assert result["resolution"] == 0.2
    assert result["exceedances_including_ties"] == 2
    np.testing.assert_allclose(result["tail_probability_wilson_ci95"], [0.15003899, 0.84996101])
    assert search_null_test(10, list(range(19)))["p_value"] == 0.5
    assert search_null_test(30, list(range(19)))["p_value"] == 0.05
    assert search_null_test(-1, list(range(19)))["p_value"] == 1


@pytest.mark.parametrize("observed,nulls", [(1, []), (1, [0, np.nan]), (np.inf, [0])])
def test_null_failure_not_counted_as_zero(observed, nulls):
    with pytest.raises(ValueError):
        search_null_test(observed, nulls)
