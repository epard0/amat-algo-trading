import numpy as np
import pandas as pd
import pytest
from autoresearch.crypto_intraday import run_intraday,minute_weights
from autoresearch.crypto_directional import run_directional


def market(n=120,freq='1min'):
    idx=pd.date_range('2026-01-01',periods=n,freq=freq,tz='UTC')
    f=pd.DataFrame({k:100. for k in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close')},index=idx)
    f['tradable']=1.
    return {'BTCUSDT':f.copy(),'ETHUSDT':f.copy()},{'BTCUSDT':{},'ETHUSDT':{}}


def test_minute_funding_occurs_once_and_timestamps_are_monotonic():
    f,fu=market();idx=f['BTCUSDT'].index;when=int(idx[8].timestamp()*1000)
    for symbol in fu:fu[symbol][when//3600000]=(when+2,'.001')
    w=np.ones((120,2))*.5;w[9:]=-.5
    stats,_,_,ledgers=run_intraday(f,fu,w,('2026-01-01','2026-01-01'),capture=True)
    assert stats['funding_pnl']<0
    for ledger in ledgers:
        assert sum(e['event']=='funding' for e in ledger['ledger'])==1
        times=[e['timestamp_ms'] for e in ledger['ledger']]
        assert times==sorted(times)
    assert stats['episode_net_pnl']==pytest.approx(stats['pnl'])


def test_hourly_parity_with_existing_wrapper_engine():
    f,fu=market(48,'1h');w=np.ones((48,2))*.5;w[24:]=0
    a=run_intraday(f,fu,w,('2026-01-01','2026-01-02'))
    b=run_directional(f,fu,w,('2026-01-01','2026-01-02'))
    np.testing.assert_allclose(a[1].equity,b[1].equity)


@pytest.mark.parametrize('minutes',[1,5,15,30,240])
def test_minute_aggregation_and_profit_decisions_are_causal(minutes):
    f,_=market(1440*45)
    for frame in f.values():
        price=100+np.arange(len(frame))*.0001+np.sin(np.arange(len(frame))/100)
        for c in ('open','high','low','close'):frame[c]=price
    cfg=dict(minutes=minutes,leverage=2,sizing='vol20',tp_atr=2.)
    before=minute_weights(f,cfg,{'BTCUSDT':1,'ETHUSDT':-1})
    for frame in f.values():frame.loc[frame.index[50000]:,['open','high','low','close']]*=3
    after=minute_weights(f,cfg,{'BTCUSDT':1,'ETHUSDT':-1})
    np.testing.assert_allclose(before[:50001],after[:50001],equal_nan=True)
