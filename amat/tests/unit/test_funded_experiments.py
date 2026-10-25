import numpy as np
import pandas as pd
import pytest

from autoresearch.funded import trade_day
from autoresearch.funded_experiments import candidates, max_statistic, subgroup
from autoresearch.funded_signals import causal_kalman_state, decelerated_signals


def flat():
    return np.tile([20000., 20001., 19999., 20000., 1000.], (390, 1))


def test_one_contract_and_side_quote_execution(monkeypatch):
    monkeypatch.setattr('autoresearch.funded_signals.signal_plan', lambda *_: (50, 1, 10.))
    b = flat()
    bid, ask = b.copy(), b.copy()
    bid[:, :4] -= .25
    ask[:, :4] += .25
    cost = {'commission_side': 1., 'slip_ticks': 1, 'quotes': {'bid': bid, 'ask': ask}}
    candidate = {'strategy': 'rsi_reversal', 'risk': 100, 'fixed_contracts': 1}
    r = trade_day(b, candidate, cost, 50000, 48000, 'eod')
    assert r['contracts'] == 1
    assert r['entry'] == 20000.5
    assert r['exit_reference'] == 19999.75
    assert r['pnl'] == -4


def test_holding_exit_at_next_open_before_extremes(monkeypatch):
    monkeypatch.setattr('autoresearch.funded_signals.signal_plan', lambda *_: (50, 1, 10.))
    b = flat()
    b[81, 2] = 19000
    r = trade_day(b, {'strategy': 'rsi_reversal', 'risk': 100, 'max_holding_minutes': 30},
                  {'commission_side': 1., 'slip_ticks': 1}, 50000, 48000, 'eod')
    assert r['reason'] == 'holding_limit'
    assert not r['failed']


def test_rsi_midpoint_exit_uses_previous_close(monkeypatch):
    monkeypatch.setattr('autoresearch.funded_signals.signal_plan', lambda *_: (50, 1, 10.))
    rsi = np.zeros(390)
    rsi[55:] = 60
    monkeypatch.setattr('autoresearch.funded_signals.indicators', lambda _: pd.DataFrame({'rsi': rsi}))
    b = flat()
    b[56:, :4] += 3
    r = trade_day(b, {'strategy': 'rsi_reversal', 'risk': 100, 'exit_rule': 'rsi_midpoint'},
                  {'commission_side': 1., 'slip_ticks': 1}, 50000, 48000, 'eod')
    assert r['reason'] == 'rsi_midpoint'
    assert r['exit_reference'] == 20003


def test_deceleration_waits_two_completed_bars_and_is_causal():
    long = pd.Series(False, index=range(100))
    short = long.copy()
    long.iloc[45] = True
    slope = np.linspace(-2., -1., 100)
    a, _ = decelerated_signals(long, short, slope)
    assert np.flatnonzero(a.to_numpy()).tolist() == [47]
    changed = slope.copy()
    changed[48:] = -100
    b, _ = decelerated_signals(long, short, changed)
    assert a.iloc[:48].equals(b.iloc[:48])


def test_model_uncertainty_is_positive_and_prefix_causal():
    prices = np.arange(100.)+20000
    slope, variance = causal_kalman_state(prices)
    prefix = causal_kalman_state(prices[:50])
    assert (variance >= 0).all()
    np.testing.assert_allclose(slope[:50], prefix[0])
    np.testing.assert_allclose(variance[:50], prefix[1])


def test_multiplicity_never_rewards_zero_variance_and_is_reproducible():
    rng = np.random.default_rng(7)
    matrix = np.column_stack([np.zeros(30), rng.normal(-1, 1, 30), rng.normal(5, 1, 30)])
    result = max_statistic(matrix, 200)
    assert result[0]['maxT_adjusted_p'] == 1
    assert result[1]['maxT_adjusted_p'] > result[2]['maxT_adjusted_p']
    assert result == max_statistic(matrix, 200)
    with pytest.raises(ValueError):
        max_statistic(np.zeros((3, 2)))


def test_grid_is_controlled_and_subgroup_counts_do_not_create_trades():
    grid = candidates()
    assert len(grid) == 13
    assert all(p['policy']['risk'] == 100 for p in grid)
    rows = [{'pnl': 10, 'contracts': 1, 'direction': 1, 'signal_minute': 60},
            {'pnl': -5, 'contracts': 1, 'direction': -1, 'signal_minute': 100}]
    result = subgroup(rows)
    assert result['long']['entries'] == result['before_11']['entries'] == 1
    assert not result['long']['enough_observations']


def test_rsi_exit_cache_avoids_repeated_computation_without_mutation(monkeypatch):
    from autoresearch.funded_signals import _rsi_values, rsi_values
    _rsi_values.cache_clear()
    calls = []
    def calculate(bars):
        calls.append(True)
        return pd.DataFrame({'rsi': np.arange(len(bars), dtype=float)})
    monkeypatch.setattr('autoresearch.funded_signals.indicators', calculate)
    a = rsi_values(flat())
    b = rsi_values(flat())
    assert a is b
    assert len(calls) == 1
    assert not a.flags.writeable
    _rsi_values.cache_clear()
