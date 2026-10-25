import numpy as np
import pandas as pd
import pytest

from autoresearch.crypto_fast_breakout import prepare, replay_events, short_weights, combined_metrics
from autoresearch.crypto_intraday import run_intraday


def market(n=2880):
    idx = pd.date_range('2024-01-01', periods=n, freq='1min', tz='UTC')
    price = 100+np.arange(n)*.002+np.sin(np.arange(n)/20)
    f = pd.DataFrame({k: price.copy() for k in ('open', 'high', 'low', 'close', 'mark_open', 'mark_high', 'mark_low', 'mark_close')}, index=idx)
    f['tradable'] = 1.
    return f


@pytest.mark.parametrize('unit', ['ns', 'us', 'ms'])
def test_event_replay_matches_canonical_wrapper_with_funding_ticks_and_rebalancing(unit):
    f = market()
    f.index = f.index.as_unit(unit)
    weights = np.full(len(f), .98)
    weights[150:300] = 0
    weights[1700:1900] = 0
    funding = {int(f.index[i].timestamp()*1000)//3600000: (int(f.index[i].timestamp()*1000)+2, '.001') for i in (0, 480, 960, 1440, 1920, 2400)}
    specs = dict(multiplier='.1', price_tick='.001')
    stats, path, _ = replay_events(prepare(f, funding), weights, ('2024-01-01', '2024-01-02'), capital=2500,
        leverage=1.5, symbol='SOL', specs=specs, full_path=True)
    expected, expected_path, _, _ = run_intraday({'SOL': f}, {'SOL': funding}, weights[:, None]*1.5,
        ('2024-01-01', '2024-01-02'), capital=2500, leverage_limit=1.5, contract_specs={'SOL': specs})
    np.testing.assert_allclose(path.equity, expected_path.equity, atol=1e-7, rtol=0)
    for key in ('pnl', 'funding_pnl', 'fees', 'liquidations', 'margin_rejections'):
        assert stats[key] == pytest.approx(expected[key])


def test_vectorized_intermediate_minute_liquidation_matches_decimal_wallet():
    f = market()
    f.loc[f.index[187], 'mark_low'] = 1.
    w = np.full(len(f), .98)
    specs = dict(multiplier='.1', price_tick='.001')
    s, p, _ = replay_events(prepare(f, {}), w, ('2024-01-01', '2024-01-02'), capital=10000,
        leverage=2, symbol='SOL', specs=specs, full_path=True)
    e, ep, _, _ = run_intraday({'SOL': f}, {'SOL': {}}, w[:, None]*2, ('2024-01-01', '2024-01-02'),
        capital=10000, leverage_limit=2, contract_specs={'SOL': specs})
    np.testing.assert_allclose(p.equity, ep.equity, atol=1e-7, rtol=0)
    assert s['liquidations'] == e['liquidations'] == 1


@pytest.mark.parametrize('minutes', [1, 5, 15, 30])
def test_short_windows_and_profit_targets_cannot_read_future(minutes):
    f = market()
    before = short_weights(f, minutes, 2, .5, 1)
    f.loc[f.index[1801]:, ['open', 'high', 'low', 'close']] *= 2
    after = short_weights(f, minutes, 2, .5, 1)
    np.testing.assert_allclose(before[:1802], after[:1802], equal_nan=True)
    assert np.nanmin(before)>=0
    assert np.nanmax(before)<=.98


def test_portfolio_aggregation_excludes_forced_final_closes_from_frequency():
    f = market()
    weights = np.full(len(f), .98)
    data = prepare(f, {})
    specs = dict(multiplier='.1', price_tick='.001')
    sleeves = [replay_events(data, weights, ('2024-01-01', '2024-01-02'), capital=c,
        leverage=1, symbol='SOL', specs=specs) for c in (2500, 7500)]
    end = int((f.index[-1].timestamp()*1000)+59999)
    stats, _ = combined_metrics(sleeves, end_ms=end)
    assert stats['pnl'] == pytest.approx(sum(s[0]['pnl'] for s in sleeves))
    assert stats['natural_closes'] == stats['profitable_closes'] == 0


def test_missing_marks_force_flat_before_gap_and_do_not_create_profit_frequency():
    f = market()
    f['mark_available'] = True
    f.loc[f.index[187:189], 'mark_available'] = False
    f.loc[f.index[187:189], ['mark_open', 'mark_low', 'mark_high', 'mark_close']] = 1.
    s, p, ledger = replay_events(prepare(f, {}), np.full(len(f), .98), ('2024-01-01', '2024-01-02'), capital=10000,
        leverage=1, symbol='SOL', specs=dict(multiplier='.1', price_tick='.001'), full_path=True, capture=True)
    assert p.equity.iloc[186] == p.equity.iloc[187] == p.equity.iloc[188]
    assert s['liquidations'] == 0
    assert s['natural_closes'] == 0
    assert any(e['forced_blackout'] for e in s['closed_episodes'])
    # Four entry/exit fills plus the existing daily size rebalance.
    assert sum(e['event']=='fill' for e in ledger)==5


def test_training_selection_never_substitutes_a_loss_for_a_frequency_qualifier():
    from autoresearch.crypto_short_window_study import choose_training
    rows = [dict(id=0, config=dict(minutes=1), pnl=-10., max_drawdown=-.1,
                 profitable_closes_per_month=8, liquidations=0, natural_closes=100),
            dict(id=1, config=dict(minutes=5), pnl=200., max_drawdown=-.4,
                 profitable_closes_per_month=8, liquidations=0, natural_closes=100),
            dict(id=2, config=dict(minutes=15), pnl=100., max_drawdown=-.2,
                 profitable_closes_per_month=5, liquidations=0, natural_closes=100)]
    selection = choose_training(rows)
    assert selection['global_training_pnl']=='5m-pnl'
    assert selection['primary']==selection['global_training_frequent']=='15m-frequent'
    assert '1m-frequent' not in selection['selected']
    assert selection['selected']['1m-pnl']['pnl']<0


def test_pending_order_retries_when_volume_resumes_without_new_signal():
    f = market()
    f.loc[f.index[60:63], 'tradable'] = 0.
    w = np.zeros(len(f))
    w[60:100] = .98
    specs = dict(multiplier='.1', price_tick='.001')
    s, p, ledger = replay_events(prepare(f, {}), w, ('2024-01-01', '2024-01-02'), capital=10000,
        leverage=1, symbol='SOL', specs=specs, full_path=True, capture=True)
    e, ep, _, _ = run_intraday({'SOL':f}, {'SOL':{}}, w[:,None], ('2024-01-01','2024-01-02'),
        contract_specs={'SOL':specs}, capital=10000, leverage_limit=1)
    np.testing.assert_allclose(p.equity, ep.equity, atol=1e-7, rtol=0)
    assert s['pnl']==pytest.approx(e['pnl'])
    first_fill=next(e for e in ledger if e['event']=='fill')
    assert first_fill['timestamp_ms']==int(f.index[63].timestamp()*1000)+6000
