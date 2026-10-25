import numpy as np
import pandas as pd
import pytest
from autoresearch.crypto_walkforward import sizing_path,fresh_plan,replay_sized_plan
from autoresearch.crypto_walkforward_study import folds,select_size,robust
from autoresearch.crypto_partial_exits import replay_exit_plan
from autoresearch.crypto_fast_breakout import prepare


def market(n=2880):
    idx=pd.date_range('2024-01-01',periods=n,freq='1min',tz='UTC');p=100+np.arange(n)*.002+np.sin(np.arange(n)/30)
    f=pd.DataFrame({k:p.copy() for k in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close')},index=idx);f['tradable']=1
    return f


def test_sized_kernel_matches_frozen_accounting():
    f=market();w=np.full(len(f),.98);w[600:]=.49;why=np.full(len(f),2)
    args=dict(capital=2500,leverage=2,symbol='X',specs=dict(multiplier='.1',price_tick='.001'),capture=True,full_path=True)
    a,ap,al=replay_exit_plan(prepare(f,{}),w,why,('2024-01-01','2024-01-02'),**dict(args,leverage=1.5))
    b,bp,bl=replay_sized_plan(prepare(f,{}),w,why,('2024-01-01','2024-01-02'),sizing=np.full(len(f),1.5),**args)
    np.testing.assert_allclose(ap.equity,bp.equity,atol=1e-8);assert a['pnl']==pytest.approx(b['pnl']);assert al==bl


def test_sizing_updates_daily_is_causal_and_never_rebuys_partial():
    f=market(45*1440);before=sizing_path(f,dict(target=.3));cut=35*1440+123
    f.loc[f.index[cut]:,'close']*=4
    after=sizing_path(f,dict(target=.3));np.testing.assert_equal(before[:cut],after[:cut])
    assert np.all((before>=.25)&(before<=2))
    changes=np.flatnonzero(np.diff(before)!=0)+1
    assert all(f.index[i].hour==0 and f.index[i].minute==0 for i in changes)
    f=market();w=np.full(len(f),.98);w[600:]=.49;sizing=np.ones(len(f));sizing[1440:]=2
    _,_,ledger=replay_sized_plan(prepare(f,{}),w,np.full(len(f),2),('2024-01-01','2024-01-02'),capital=2500,leverage=2,symbol='X',specs=dict(multiplier='.1',price_tick='.001'),sizing=sizing,capture=True)
    assert all(float(e['contracts'])<0 for e in ledger if e['event']=='fill' and e['timestamp_ms']>=f.index[600].timestamp()*1000)


def test_fold_boundaries_selection_and_robust_gate():
    ff=folds();assert len(ff)==7
    for f in ff:assert pd.Timestamp(f['train'][1])<pd.Timestamp(f['test'][0])
    b=dict(size='fixed-1',pnl=100,max_drawdown=-.2,liquidations=0,margin_rejections=0,natural_closes=6)
    assert select_size([b,dict(b,size='fixed-2',pnl=200,max_drawdown=-.3)])['size']=='fixed-1'
    assert not select_size([dict(b,pnl=-1)])['eligible']
    a=dict(net_pnl_15=100,net_pnl_30=50,positive_quarters_15=4,worst_quarter_return_15=-.1,max_drawdown_15=-.2,liquidations=0,margin_rejections=0,all_training_eligible=True)
    assert robust(a);assert not robust(dict(a,net_pnl_30=-1))


def test_fresh_plan_skips_carried_signal_without_mutation():
    idx=pd.date_range('2024-01-01',periods=72,freq='1h',tz='UTC');w=np.full(72,.98);w[30:40]=0
    a,_=fresh_plan(w,np.zeros(72),idx,('2024-01-02','2024-01-03'))
    assert np.all(a[24:30]==0);assert a[40]==.98;assert w[24]==.98


def test_compounded_capital_reconciles_portfolio_metrics():
    from autoresearch.crypto_walkforward import reduction_metrics
    f=market();w=np.zeros(len(f));data=prepare(f,{})
    sleeves=[replay_sized_plan(data,w,np.zeros(len(f)),('2024-01-01','2024-01-02'),capital=c,leverage=2,symbol='X',specs=dict(multiplier='.1',price_tick='.001')) for c in (3000,9000)]
    stats,_=reduction_metrics(sleeves,int(f.index[-1].timestamp()*1000)+59999,capital=12000)
    assert stats['pnl']==0;assert stats['ending_equity']==12000
