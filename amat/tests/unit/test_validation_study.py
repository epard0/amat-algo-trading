"""Causality, delayed labels and bounded recovery for the fixed exit study."""

import datetime as dt

import numpy as np
import pytest

from autoresearch.improvements.validation_study import (
    adverse_labels,
    feature_matrix,
    monthly_metrics,
    recovery_episodes,
    recovery_scalars,
    walk_forward,
)


def test_features_are_prefix_invariant_and_ignore_future_mutation():
    rng = np.random.default_rng(7)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (800, 4)), axis=0))
    equity = 100000 * np.exp(np.cumsum(rng.normal(0, 0.01, 800)))
    expected = feature_matrix(prices[:600], equity[:600], 1)
    prices[600:] *= 25
    equity[600:] *= 0.01
    actual = feature_matrix(prices, equity, 1)
    np.testing.assert_allclose(actual[:600], expected, equal_nan=True)
    assert np.isnan(actual[:200]).all()


def test_labels_mature_after_ten_closes_and_include_path_losses():
    nav = np.full(30, 100.0)
    nav[6] = 96
    labels = adverse_labels(nav)
    assert labels[0] == 1
    assert labels[6] == 0
    assert np.isnan(labels[-10:]).all()
    # Altering a later close cannot alter an already matured label.
    nav[15:] = 80
    np.testing.assert_equal(labels[:5], adverse_labels(nav)[:5])


def test_walk_forward_purges_labels_and_future_test_year_cannot_change_predictions():
    sessions = tuple(dt.date(2017, 1, 1) + dt.timedelta(days=i) for i in range(365 * 5))
    rng = np.random.default_rng(12)
    features = rng.normal(size=(len(sessions), 9))
    labels = (features[:, 0] + rng.normal(size=len(sessions)) > 0.5).astype(float)
    p, folds, _ = walk_forward(sessions, features, labels)
    assert len(folds) == 2
    for fold in folds:
        assert fold["last_label_close"] < fold["test_start"]
    cutoff = next(i for i, d in enumerate(sessions) if d.year == 2021)
    prefix, _, _ = walk_forward(sessions[:cutoff], features[:cutoff], labels[:cutoff])
    features[cutoff:] = 1000
    labels[cutoff:] = 1
    altered, _, _ = walk_forward(sessions, features, labels)
    np.testing.assert_allclose(p[:cutoff], prefix, equal_nan=True)
    np.testing.assert_allclose(p[:cutoff], altered[:cutoff], equal_nan=True)
    assert np.isnan(p[: next(i for i, d in enumerate(sessions) if d.year == 2020)]).all()


def test_recovery_waits_then_steps_and_new_danger_resets():
    danger = np.zeros(30, dtype=bool)
    danger[0] = True
    scalar = recovery_scalars(danger, True)
    np.testing.assert_equal(scalar[:8], np.full(8, 0.25))
    assert scalar[8] == 0.5
    assert scalar[11] == 0.75
    assert scalar[14] == 1
    danger[12] = True
    reset = recovery_scalars(danger, True)
    assert reset[12] == 0.25
    assert np.all((reset >= 0.25) & (reset <= 1))
    np.testing.assert_equal(recovery_scalars(danger[:2], False), [0.25, 1])


def test_months_use_previous_close_and_recovery_open_episode():
    rows = [
        {"session": d, "equity": v, "fees": 1.0, "exposure": 0.5, "fills": 1, "missed": 0}
        for d, v in [
            ("2020-01-30", 100000.0),
            ("2020-01-31", 90000.0),
            ("2020-02-03", 99000.0),
            ("2020-02-04", 101000.0),
            ("2020-02-05", 95000.0),
        ]
    ]
    months = monthly_metrics(rows)
    assert months[0]["net_return"] == pytest.approx(-0.1)
    assert months[1]["net_return"] == pytest.approx(101000 / 90000 * (95000 / 101000) - 1)
    episodes = recovery_episodes(rows)
    assert episodes[0]["recovered"] == "2020-02-04"
    assert episodes[0]["sessions"] == 2
    assert episodes[1]["recovered"] is None
