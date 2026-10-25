import numpy as np
import pytest

from autoresearch.funded import trade_day
from autoresearch.funded_signals import FAMILIES, causal_kalman_slope, indicators, kalman_gate, signal_plan


def sample():
    rng = np.random.default_rng(11)
    close = 20000+np.cumsum(rng.choice([-1., -.5, .5, 1.], 390))
    return np.column_stack([close, close+1., close-1., close, np.full(390, 1000.)])


def test_indicators_and_kalman_cannot_see_future():
    a = sample()
    b = a.copy()
    b[100:, :4] += 500
    np.testing.assert_allclose(indicators(a).iloc[:100], indicators(b).iloc[:100], equal_nan=True)
    np.testing.assert_allclose(causal_kalman_slope(a[:, 3])[:100], causal_kalman_slope(b[:, 3])[:100])


@pytest.mark.parametrize('family', FAMILIES)
def test_flat_prices_abstain(family):
    bars = np.tile([20000., 20000., 20000., 20000., 100.], (390, 1))
    assert signal_plan(bars, {'strategy': family, 'atr_multiplier': 2.}) is None


@pytest.mark.parametrize('family', FAMILIES)
def test_signal_plan_is_prefix_causal(family):
    bars = sample()
    candidate = {'strategy': family, 'atr_multiplier': 2.}
    plan = signal_plan(bars, candidate)
    if plan is not None:
        changed = bars.copy()
        changed[plan[0]+1:, :4] += 500
        assert signal_plan(changed, candidate) == plan


def test_indicator_trade_uses_next_open_and_frozen_atr(monkeypatch):
    bars = sample()
    monkeypatch.setattr('autoresearch.funded_signals.signal_plan', lambda *_: (50, -1, 5.))
    r = trade_day(bars, {'strategy': 'rsi_reversal', 'risk': 100, 'take_profit_r': 1.},
                  {'commission_side': 1., 'slip_ticks': 2}, 50000., 48000., 'eod')
    assert r['entry'] == bars[51, 0]-.5
    assert 0 < r['contracts'] <= 4


def test_unknown_family_rejected():
    with pytest.raises(ValueError, match='Unknown'):
        signal_plan(sample(), {'strategy': 'future_leak', 'atr_multiplier': 2.})


def test_kalman_gates_respect_trade_direction():
    assert kalman_gate(1, .2, 'aligned', .05)
    assert not kalman_gate(-1, .2, 'aligned', .05)
    assert kalman_gate(-1, .2, 'countertrend', .05)
    assert not kalman_gate(1, .2, 'countertrend', .05)
    assert kalman_gate(-1, .02, 'calm', .05)
    assert not kalman_gate(1, .2, 'calm', .05)
    assert kalman_gate(1, -.02, 'no_strong_opposition', .05)
    assert not kalman_gate(1, -.2, 'no_strong_opposition', .05)


@pytest.mark.parametrize('mode', ['aligned', 'countertrend', 'calm', 'no_strong_opposition'])
@pytest.mark.parametrize('variance', [.001, .01])
def test_rsi_kalman_gate_and_tuning_are_causal(mode, variance):
    bars = sample()
    candidate = {'strategy': 'rsi_kalman', 'atr_multiplier': 1.5,
                 'kalman_gate': mode, 'kalman_threshold': .05,
                 'kalman_process_variance': variance}
    plan = signal_plan(bars, candidate)
    if plan is not None:
        changed = bars.copy()
        changed[plan[0]+1:, :4] += 500
        assert signal_plan(changed, candidate) == plan
    np.testing.assert_allclose(causal_kalman_slope(bars[:, 3], variance)[:100],
                               causal_kalman_slope(bars[:100, 3], variance))


def test_rsi_kalman_grid_is_controlled_and_unique():
    import json
    from autoresearch.funded_rsi_kalman import candidates
    grid = candidates()
    assert len(grid) == len({json.dumps(p, sort_keys=True) for p in grid}) == 34
    assert {p['risk'] for p in grid} == {100}
    assert {p['atr_multiplier'] for p in grid} == {1.5}
