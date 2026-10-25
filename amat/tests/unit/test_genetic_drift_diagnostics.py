"""Frozen prevalence explanations agree with monitoring on finite data and gaps."""

import json

import numpy as np
import pytest

from autoresearch.genetic.drift_diagnostics import explain_prevalence_drift
from autoresearch.genetic.shadow_inference import ShadowMonitorPolicy, monitor_values


def reference(fraction=2 / 7):
    return {
        "IEF": {
            "positive_fraction": fraction,
            "mean": 0.0,
            "std": 1.0,
            "available_fraction": 1.0,
        }
    }


def test_observed_ief_drift_explanation():
    values = np.array([1.0] * 15 + [-1.0] * 5)[:, None]
    policy = ShadowMonitorPolicy()
    result = explain_prevalence_drift(
        values, ["IEF"], reference(), policy, reference_window_sessions=252
    )
    row = result["assets"][0]
    assert row["positive_count"] == 15
    assert row["finite_count"] == 20
    assert row["observed_positive_fraction"] == 0.75
    assert row["absolute_shift"] == pytest.approx(13 / 28)
    assert row["threshold_margin"] == pytest.approx(0.1142857142857143)
    assert row["threshold_exceeded"]
    assert {"symbol": "IEF", "code": "positive_prevalence_drift"} in monitor_values(
        values, ["IEF"], reference(), policy
    )["alerts"]
    assert result["suspension_reset_authority"] == "none"
    json.dumps(result, allow_nan=False)


def test_exact_threshold_does_not_alert():
    row = explain_prevalence_drift(
        np.array([[1.0], [0.0]]),
        ["IEF"],
        reference(0.25),
        ShadowMonitorPolicy(max_prevalence_shift=0.25),
    )["assets"][0]
    assert row["threshold_margin"] == 0
    assert not row["threshold_exceeded"]


def test_negative_shift_and_missing_values():
    row = explain_prevalence_drift(
        np.array([[np.nan], [np.inf], [0.0], [-1.0]]),
        ["IEF"],
        reference(0.75),
        ShadowMonitorPolicy(),
    )["assets"][0]
    assert row["finite_count"] == 2
    assert row["unavailable_count"] == 2
    assert row["positive_count"] == 0
    assert row["signed_shift"] == -0.75
    assert row["threshold_exceeded"]


@pytest.mark.parametrize(
    "values,stats",
    [
        (np.array([[np.nan]]), reference()),
        (np.array([[1.0]]), reference(None)),
    ],
)
def test_unavailable_comparison_is_explicit(values, stats):
    result = explain_prevalence_drift(values, ["IEF"], stats, ShadowMonitorPolicy())
    row = result["assets"][0]
    assert not row["comparison_available"]
    assert row["absolute_shift"] is None
    assert not row["threshold_exceeded"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("fraction", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_invalid_reference_fraction(fraction):
    with pytest.raises(ValueError, match="reference positive fraction"):
        explain_prevalence_drift(
            np.ones((1, 1)), ["IEF"], reference(fraction), ShadowMonitorPolicy()
        )


@pytest.mark.parametrize(
    "values,symbols",
    [
        (np.array([]), ["IEF"]),
        (np.ones((0, 1)), ["IEF"]),
        (np.ones((1, 2)), ["IEF"]),
        (np.ones((1, 2)), ["IEF", "IEF"]),
    ],
)
def test_invalid_shape(values, symbols):
    with pytest.raises(ValueError, match="aligned values"):
        explain_prevalence_drift(values, symbols, reference(), ShadowMonitorPolicy())


def test_missing_reference():
    with pytest.raises(ValueError, match="missing reference"):
        explain_prevalence_drift(np.ones((1, 1)), ["SPY"], reference(), ShadowMonitorPolicy())


@pytest.mark.parametrize("sessions", [0, -1, True, 1.5])
def test_invalid_reference_window(sessions):
    with pytest.raises(ValueError, match="positive integer"):
        explain_prevalence_drift(
            np.ones((1, 1)),
            ["IEF"],
            reference(),
            ShadowMonitorPolicy(),
            reference_window_sessions=sessions,
        )
