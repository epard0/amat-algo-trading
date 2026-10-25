import numpy as np
import pandas as pd
from autoresearch.crypto_adx_switch import wilder_adx,fit_rules,FEATURES


def prices(x):
    x=np.asarray(x,dtype=float)
    return pd.DataFrame(dict(high=x+1,low=x-1,close=x))


def test_monotonic_and_flat_warmup():
    for n in (7,14,28):
        for x,target in [(np.arange(100),100),(np.zeros(100),0),(-np.arange(100),100)]:
            actual=wilder_adx(prices(x),n)
            assert np.isnan(actual[:2*n-1]).all()
            np.testing.assert_allclose(actual[2*n-1:],target)


def test_hand_calculation_and_tied_directional_movement():
    p=pd.DataFrame(dict(high=[10,12,11,14,13,15],low=[8,7,6,5,4,3],close=[9,10,8,12,9,10]))
    # n=2: seed DM=(2,1), DX=100/3; next sums=(4,0.5), DX=700/9.
    # Bar4: up=-1/down=1 -> sums=(2,1.25), DX=300/13.
    expected_seed=(100/3+700/9)/2
    a=wilder_adx(p,2)
    assert np.isnan(a[:3]).all()
    np.testing.assert_allclose(a[3],expected_seed)
    np.testing.assert_allclose(a[4],(expected_seed+300/13)/2)
    # Equal expansion up/down contributes neither directional movement.
    p=pd.DataFrame(dict(high=[10,11,12,13,14],low=[8,7,6,5,4],close=[9]*5))
    np.testing.assert_allclose(wilder_adx(p,2)[3:],0)


def test_missing_bar_resets_and_future_cannot_change_past():
    p=prices(np.arange(100)+np.sin(np.arange(100)))
    original=wilder_adx(p,7)
    changed=p.copy();changed.loc[60:,'high']+=1000
    np.testing.assert_allclose(wilder_adx(changed,7)[:60],original[:60],equal_nan=True)
    p.loc[40]=np.nan
    a=wilder_adx(p,7)
    assert np.isnan(a[40:54]).all()
    np.testing.assert_allclose(a[54:],100)


def test_rule_grid_and_audit_ignore_holdout():
    idx=pd.date_range('2024-01-01',periods=50,freq='4h',tz='UTC')
    ctx=dict(action_index=idx,onset=np.ones(50,dtype=bool),features=pd.DataFrame({f:np.arange(50) for f in FEATURES},index=idx))
    rules,audit=fit_rules(ctx,('2024-01-01','2024-01-04'))
    assert len(rules)==38
    ctx['features'].iloc[24:]=999
    assert (rules,audit)==fit_rules(ctx,('2024-01-01','2024-01-04'))
