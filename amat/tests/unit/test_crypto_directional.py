import numpy as np
import pandas as pd
import pytest
from autoresearch.crypto_directional import directional_weights,run_directional
from autoresearch.crypto_perpetual_study import run_perpetual


def frames(n=24):
    idx=pd.date_range('2020-01-01',periods=n,freq='1h',tz='UTC')
    f=pd.DataFrame({k:100. for k in ('open','high','low','close','mark_open','mark_high','mark_low','mark_close')},index=idx)
    f['tradable']=1.
    return {'BTCUSDT':f.copy(),'ETHUSDT':f.copy()},{'BTCUSDT':{},'ETHUSDT':{}}


def test_generic_replay_preserves_existing_benchmark_accounting():
    f,fu=frames(72);w=np.ones((72,2))*.7;w[24:48]=-.5
    a=run_perpetual(f,fu,w,('2020-01-01','2020-01-03'),capture=True)
    b=run_directional(f,fu,w,('2020-01-01','2020-01-03'),capture=True,fee_bps=5)
    assert a[0]['pnl']==b[0]['pnl']
    np.testing.assert_allclose(a[1].equity,b[1].equity)
    assert b[0]['episode_net_pnl']==pytest.approx(b[0]['pnl'])
    assert b[0]['round_trips']==6


def test_single_asset_gets_whole_capital_and_flat_second_sleeve_is_not_needed():
    f,fu=frames();f={'BTCUSDT':f['BTCUSDT']};fu={'BTCUSDT':{}}
    a=run_directional(f,fu,np.ones((24,1))*.5,('2020-01-01','2020-01-01'),fee_bps=5)
    assert set(a[0]['per_asset'])=={'BTCUSDT'}
    assert a[0]['per_asset']['BTCUSDT']['pnl']==pytest.approx(a[0]['pnl'])


@pytest.mark.parametrize('tp',[None,1.,2.])
def test_profit_rules_are_causal_and_respect_requested_direction(tp):
    f,_=frames(24*70)
    for frame in f.values():
        p=100+np.arange(len(frame))*.01+np.sin(np.arange(len(frame))/20)
        for name in ('open','high','low','close'):frame[name]=p
    cfg=dict(sizing='vol20',leverage=2,tp_atr=tp)
    before=directional_weights(f,cfg,{'BTCUSDT':1,'ETHUSDT':-1})
    assert np.nanmin(before[:,0])>=0 and np.nanmax(before[:,1])<=0
    for frame in f.values():frame.loc[frame.index[1200]:,['open','high','low','close']]*=2
    after=directional_weights(f,cfg,{'BTCUSDT':1,'ETHUSDT':-1})
    np.testing.assert_allclose(before[:1201],after[:1201],equal_nan=True)
