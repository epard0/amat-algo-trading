import pytest
import numpy as np
import pandas as pd
from autoresearch.crypto_spot_study import replay, targets, aggregate
from autoresearch.genetic.data import CandleDataset


def frames(n=1200):
    idx=pd.date_range('2020-01-01',periods=n,freq='1h',tz='UTC')
    c=100+np.arange(n)*.03+np.sin(np.arange(n)/13)
    f=pd.DataFrame(dict(open=c,high=c+1,low=c-1,close=c),index=idx)
    return {'BTCUSDT':f.copy(),'ETHUSDT':f.copy()}


def test_buyhold_costs_are_self_financing():
    fs=frames(24)
    for f in fs.values(): f.loc[:,:]=100
    stats,path,_=replay(fs,np.ones((24,2)),'2020-01-01','2020-01-01',15,buyhold=True)
    assert np.isclose(path.equity.iloc[-1],10000*(1-.0015)/(1+.0015))
    assert np.isclose(stats['costs'],10000-path.equity.iloc[-1])
    assert stats['orders']==4


@pytest.mark.parametrize("family", ["trend", "breakout", "reversion"])
def test_future_prices_do_not_change_prior_targets(family):
    fs=frames(); cfg=dict(timeframe=1,family=family,lookback=5,parameter=2)
    before=targets(fs,cfg)
    for f in fs.values(): f.iloc[1000:]*=2
    after=targets(fs,cfg)
    np.testing.assert_allclose(before[:1001],after[:1001],equal_nan=True)


def test_gap_does_not_sell_or_invent_a_fill():
    fs=frames(24)
    for f in fs.values():
        f.loc[:,:]=100
        f.iloc[4:8]=np.nan
    _,path,_=replay(fs,np.ones((24,2)),'2020-01-01','2020-01-01',15,buyhold=True)
    assert path.turnover.iloc[4:8].sum()==0
    assert path.equity.iloc[4:8].nunique()==1


def test_four_hour_bar_requires_every_hour():
    fs=frames(8); f=next(iter(fs.values())).copy(); f.iloc[2]=np.nan
    data=CandleDataset(tuple(x.isoformat() for x in f.index),('BTCUSDT','ETHUSDT'),
        {k:np.column_stack([f[k],f[k]]) for k in ('open','high','low','close')},
        np.column_stack([f.open,f.open]),'1h','spot_raw',{})
    agg=aggregate(data,4)
    assert agg['BTCUSDT'].iloc[0][['open','high','low','close']].isna().all()
    assert np.isfinite(agg['BTCUSDT'].execution_open.iloc[0])
    assert agg['BTCUSDT'].iloc[1].notna().all()
