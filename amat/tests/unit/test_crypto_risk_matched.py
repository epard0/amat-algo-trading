import numpy as np
import pandas as pd
import pytest
from autoresearch.crypto_risk_matched import paired_intervals, volatility


def test_paired_bootstrap_preserves_identical_series():
    r=pd.Series(np.random.default_rng(9).normal(.001,.01,120),index=pd.date_range('2020-01-01',periods=120))
    result=paired_intervals(r,r,samples=100)
    assert result['annualized_mean_excess']==0
    assert result['annualized_mean_excess_95ci']==[0,0]
    assert result['sharpe_difference_95ci']==[0,0]


def test_paired_bootstrap_refuses_misaligned_calendars():
    a=pd.Series([.01,.02,.03]); b=a.copy(); b.index=[1,2,3]
    with pytest.raises(ValueError,match='calendars'): paired_intervals(a,b,samples=10)


def test_volatility_scales_linearly():
    r=pd.Series([-.01,.01,-.02,.02])
    assert volatility(0.3*r)==pytest.approx(.3*volatility(r))


def test_training_calibration_is_unchanged_by_later_prices():
    from autoresearch.crypto_risk_matched import calibrate
    from autoresearch.crypto_spot_study import replay
    train=pd.date_range('2018-01-01',periods=24*45,freq='1h',tz='UTC')
    later=pd.date_range('2022-01-01',periods=24*5,freq='1h',tz='UTC')
    idx=train.append(later)
    price=100*np.exp(.0001*np.arange(len(idx))+.02*np.sin(np.arange(len(idx))/10))
    f=pd.DataFrame(dict(open=price,high=price*1.01,low=price*.99,close=price),index=idx)
    fs={'BTCUSDT':f.copy(),'ETHUSDT':f.copy()}; base=np.ones((len(idx),2))
    _,_,ret=replay(fs,base*.3,'2018-01-01','2021-12-31',15)
    wanted=volatility(ret); first=calibrate(fs,base,wanted)
    for frame in fs.values(): frame.loc[later]*=20
    second=calibrate(fs,base,wanted)
    assert first==pytest.approx(.3,abs=1e-5)
    assert second==first
