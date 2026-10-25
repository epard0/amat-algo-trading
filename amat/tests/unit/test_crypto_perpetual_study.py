import numpy as np
import pandas as pd
import pytest
from autoresearch.crypto_perpetual_study import run_perpetual, strategy_weights


def market(hours=72):
    idx=pd.date_range('2020-01-01',periods=hours,freq='1h',tz='UTC')
    f=pd.DataFrame({k:100. for k in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close')},index=idx)
    f['tradable']=1.
    return {'BTCUSDT':f.copy(),'ETHUSDT':f.copy()},{'BTCUSDT':{},'ETHUSDT':{}}


def test_realized_equity_includes_every_cost_and_funding_event():
    frames,funding=market(24); w=np.ones((24,2))*.5
    when=int(next(iter(frames.values())).index[8].timestamp()*1000)
    for symbol in funding: funding[symbol][when//3600000]=(when,'.001')
    stats,path,_,ledgers=run_perpetual(frames,funding,w,('2020-01-01','2020-01-01'),capture=True)
    assert stats['pnl']==pytest.approx(stats['realized_price_pnl']+stats['funding_pnl']-stats['fees'])
    assert stats['funding_pnl']<0 and stats['fees']>0 and stats['liquidations']==0
    assert len(path)==24
    assert sum(e['event']=='funding' for l in ledgers for e in l['ledger'])==2


def test_funding_charged_to_old_position_before_same_hour_reversal():
    frames,funding=market(24); w=np.ones((24,2))*.5; w[8:]=-.5
    when=int(next(iter(frames.values())).index[8].timestamp()*1000)
    for symbol in funding: funding[symbol][when//3600000]=(when+2,'.001')
    stats,_,_,ledgers=run_perpetual(frames,funding,w,('2020-01-01','2020-01-01'),capture=True)
    assert stats['funding_pnl']<0
    events=ledgers[0]['ledger']; fe=next(i for i,e in enumerate(events) if e['event']=='funding')
    assert events[fe]['payment'].startswith('-')
    assert events[fe+1]['event']=='fill'
    assert int(events[fe]['timestamp_ms'])<int(events[fe+1]['timestamp_ms'])


def test_hourly_adverse_mark_liquidates_and_halts_sleeves():
    frames,funding=market(24); w=np.ones((24,2))*1.96
    for f in frames.values(): f.loc[f.index[5],'mark_low']=1
    stats,_,_,_=run_perpetual(frames,funding,w,('2020-01-01','2020-01-01'))
    assert stats['liquidations']==2 and stats['pnl']<0
    assert all(a['halted'] for a in stats['per_asset'].values())


@pytest.mark.parametrize('h',[1,4])
def test_breakout_weights_do_not_use_future_prices(h):
    frames,_=market(24*70)
    for f in frames.values():
        p=100+np.arange(len(f))*.01+np.sin(np.arange(len(f))/10)
        for name in ('open','high','low','close'): f[name]=p
    cfg=dict(timeframe=h,lookback=5,exit_ratio=2,sizing='vol20',leverage=2)
    before=strategy_weights(frames,cfg)
    for f in frames.values(): f.loc[f.index[1200]:,['open','high','low','close']]*=10
    after=strategy_weights(frames,cfg)
    np.testing.assert_allclose(before[:1201],after[:1201],equal_nan=True)


def test_zero_volume_hour_cannot_fill_reversal():
    frames,funding=market(24); w=np.ones((24,2))*.5;w[8:]=-.5
    for f in frames.values():f.loc[f.index[8],'tradable']=0
    _,_,_,ledgers=run_perpetual(frames,funding,w,('2020-01-01','2020-01-01'),capture=True)
    timestamp=int(next(iter(frames.values())).index[8].timestamp()*1000)
    assert not any(e['event']=='fill' and e['timestamp_ms']==timestamp+60001 for l in ledgers for e in l['ledger'])
