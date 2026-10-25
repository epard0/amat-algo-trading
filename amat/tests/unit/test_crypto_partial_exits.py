import numpy as np
import pandas as pd
import pytest

from autoresearch.crypto_fast_breakout import prepare, replay_events
from autoresearch.crypto_partial_exits import exit_plan, policies, replay_exit_plan, select_partial_training


def market(n=2880):
    idx = pd.date_range('2024-01-01', periods=n, freq='1min', tz='UTC')
    p = 100+np.arange(n)*.002+np.sin(np.arange(n)/30)
    f = pd.DataFrame({k:p.copy() for k in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close')}, index=idx)
    f['tradable'] = 1.
    return f


def test_full_exit_plan_preserves_canonical_wallet():
    f = market()
    w = np.where(np.arange(len(f))%600<300, .98, 0.)
    fu = {int(f.index[480].timestamp()*1000)//3600000:(int(f.index[480].timestamp()*1000),'.001')}
    data = prepare(f, fu)
    kwargs = dict(capital=2500, leverage=1.5, symbol='X', specs=dict(multiplier='.1',price_tick='.001'), full_path=True)
    before, bp, _ = replay_events(data, w, ('2024-01-01','2024-01-02'), **kwargs)
    after, ap, _ = replay_exit_plan(data, w, np.full(len(w),6), ('2024-01-01','2024-01-02'), **kwargs)
    np.testing.assert_allclose(bp.equity, ap.equity, atol=1e-7, rtol=0)
    assert before['pnl']==pytest.approx(after['pnl'])
    assert sum(r['net_pnl'] for r in after['reductions'])==pytest.approx(after['pnl'])


def test_partial_tp_is_not_bought_back_at_midnight_and_costs_reconcile():
    f = market()
    w = np.full(len(f), .98)
    w[600:]=.49
    why = np.zeros(len(w),dtype=np.int8)
    why[600:]=2
    fu = {int(f.index[960].timestamp()*1000)//3600000:(int(f.index[960].timestamp()*1000),'.001')}
    stats, _, ledger = replay_exit_plan(prepare(f,fu), w, why, ('2024-01-01','2024-01-02'), capital=2500,
        leverage=1.5,symbol='X',specs=dict(multiplier='.1',price_tick='.001'),capture=True)
    after_partial = [e for e in ledger if e['event']=='fill' and e['timestamp_ms']>=int(f.index[600].timestamp()*1000)]
    assert all(float(e['contracts'])<0 for e in after_partial)
    assert any(r['reason']=='partial_tp' for r in stats['reductions'])
    assert sum(r['net_pnl'] for r in stats['reductions'])==pytest.approx(stats['pnl'])


@pytest.mark.parametrize('cfg',[policies()[1],policies()[13],policies()[-7],policies()[-1]])
def test_exit_plans_only_use_completed_candles(cfg):
    # 45 days provides the20day entry warmup.
    f = market(45*1440)
    w, why = exit_plan(f,cfg,.5)
    cutoff = 35*1440+1
    f.loc[f.index[cutoff]:,['open','high','low','close']]*=2
    after, reasons = exit_plan(f,cfg,.5)
    np.testing.assert_allclose(w[:cutoff+1],after[:cutoff+1],equal_nan=True)
    np.testing.assert_equal(why[:cutoff+1],reasons[:cutoff+1])


def test_no_sacrifice_selection_does_not_relax_pnl_risk_or_frequency():
    b = dict(id=0,pnl=1000.,max_drawdown=-.2,liquidations=0,profitable_realization_days_per_month=2.)
    rows=[b,dict(b,id=1,pnl=999.,profitable_realization_days_per_month=4.),
        dict(b,id=2,pnl=1200.,max_drawdown=-.3,profitable_realization_days_per_month=4.),
        dict(b,id=3,pnl=1100.,profitable_realization_days_per_month=2.5)]
    s=select_partial_training(rows)
    assert s['no_sacrifice']['id']==3
    assert s['max_training_pnl']['id']==2
    assert select_partial_training(rows[:3])['no_sacrifice'] is None


def test_lot_rounding_that_closes_runner_does_not_reopen_it():
    f=market()
    w=np.full(len(f),.98)
    w[600:]=.735
    why=np.zeros(len(w),dtype=np.int8)
    why[600:]=2
    _,_,ledger=replay_exit_plan(prepare(f,{}),w,why,('2024-01-01','2024-01-02'),capital=110,
        leverage=1.5,symbol='X',specs=dict(multiplier='1',price_tick='.001'),capture=True)
    fills=[e for e in ledger if e['event']=='fill']
    assert len(fills)==2
    assert float(fills[0]['contracts'])==1
    assert float(fills[1]['contracts'])==-1
