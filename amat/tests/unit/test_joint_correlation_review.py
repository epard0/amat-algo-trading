"""Independent pre-outcome checks for the fixed joint-downside hypothesis."""

import datetime as dt
import importlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("run_joint_correlation_study")


def _panel(returns):
    values = np.vstack([np.zeros((1, returns.shape[1])), np.cumsum(returns, axis=0)])
    sessions = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(len(values)))
    return SimpleNamespace(
        adjusted_closes=100 * np.exp(values), sessions=sessions, symbols=("A", "B", "C")
    )


def _joint_losses():
    common = np.where(np.arange(100) % 2, 0.002, -0.012)
    return _panel(np.repeat(common[:, None], 3, axis=1))


def test_joint_loss_trigger_is_reduce_only_and_bounded(driver):
    panel = _joint_losses()
    policy, observations = driver.make_policy(panel, "joint_downside75")
    targets = dict.fromkeys(panel.symbols, 0.2)
    reduced = policy(targets, None, panel.sessions[70], None)
    assert reduced == {s: pytest.approx(0.15) for s in panel.symbols}
    assert targets == dict.fromkeys(panel.symbols, 0.2)
    assert observations[0]["downside_correlation"] == pytest.approx(1)
    assert observations[0]["trailing_log_return"] < 0


def test_future_prices_cannot_change_completed_decision(driver):
    panel = _joint_losses()
    corrupted = panel.adjusted_closes.copy()
    corrupted[71:] *= np.exp(np.arange(30)[:, None] * 5)
    alternative = SimpleNamespace(
        adjusted_closes=corrupted, sessions=panel.sessions, symbols=panel.symbols
    )
    targets = dict.fromkeys(panel.symbols, 0.2)
    first, a = driver.make_policy(panel, "joint_downside75")
    second, b = driver.make_policy(alternative, "joint_downside75")
    assert first(targets, None, panel.sessions[70], None) == second(
        targets, None, panel.sessions[70], None
    )
    assert a == b


def test_current_completed_close_is_included(driver):
    panel = _joint_losses()
    policy, observations = driver.make_policy(panel, "joint_downside75")
    targets = dict.fromkeys(panel.symbols, 0.2)
    policy(targets, None, panel.sessions[70], None)
    log_returns = np.diff(np.log(panel.adjusted_closes), axis=0)
    expected = float(np.sum(log_returns[50:70] @ np.full(3, 0.2)))
    assert observations[0]["trailing_log_return"] == pytest.approx(expected)


def test_static_control_scales_without_correlation_or_history(driver):
    panel = _joint_losses()
    policy, observations = driver.make_policy(panel, "static75")
    targets = {"A": 0.01, "B": 0.02, "C": 0}
    assert policy(targets, None, panel.sessions[1], None) == {
        s: pytest.approx(w * 0.75) for s, w in targets.items()
    }
    assert observations[0]["downside_correlation"] is None


def test_conditional_policy_requires_three_meaningful_positions(driver):
    panel = _joint_losses()
    policy, observations = driver.make_policy(panel, "joint_downside75")
    targets = {"A": 0.2, "B": 0.2, "C": 0.049}
    assert policy(targets, None, panel.sessions[70], None) == targets
    assert observations[0]["scale"] == 1


def test_uncorrelated_losses_do_not_trigger_conditional_reduction(driver):
    returns = np.full((100, 3), 0.001)
    for i in range(100):
        returns[i, i % 3] = -0.012
    panel = _panel(returns)
    policy, observations = driver.make_policy(panel, "joint_downside75")
    targets = dict.fromkeys(panel.symbols, 0.2)
    assert policy(targets, None, panel.sessions[70], None) == targets
    assert observations[0]["downside_correlation"] < 0
    assert observations[0]["trailing_log_return"] < 0


def test_joint_losses_incomplete_lookback_do_not_trigger(driver):
    panel = _joint_losses()
    policy, observations = driver.make_policy(panel, "joint_downside75")
    targets = dict.fromkeys(panel.symbols, 0.2)
    assert policy(targets, None, panel.sessions[59], None) == targets
    assert observations[0]["scale"] == 1
