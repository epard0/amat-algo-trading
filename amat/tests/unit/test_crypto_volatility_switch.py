import numpy as np
import pandas as pd

from autoresearch.crypto_partial_exits import exit_plan, policies
from autoresearch.crypto_volatility_switch import market_context, regime_plan, fit_rules, select_training, FEATURES


def market(n=110*1440):
    idx=pd.date_range('2024-01-01',periods=n,freq='1min',tz='UTC')
    p=100+np.arange(n)*.001+2*np.sin(np.arange(n)/1800)
    f=pd.DataFrame({c:p.copy() for c in ('open','high','low','close')},index=idx)
    f['tradable']=1.
    return f


def test_constant_modes_reproduce_both_existing_exit_plans():
    f=market()
    context=market_context(f)
    for rule,cfg in [(dict(family='original'),policies()[0]),(dict(family='ladder'),policies()[41])]:
        w,_,_,_=regime_plan(context,rule)
        expected,_=exit_plan(f,cfg,2.)
        # Warmup NaNs/zeros both mean no position; all tradable exposures match.
        np.testing.assert_allclose(np.nan_to_num(w),np.nan_to_num(expected),atol=1e-15,rtol=0)


def test_threshold_fit_cannot_see_holdout_observations():
    idx=pd.date_range('2024-01-01',periods=500,freq='4h',tz='UTC')
    features=pd.DataFrame({name:np.linspace(.2,2.,500) for name in FEATURES},index=idx)
    context=dict(action_index=idx,onset=np.ones(500,dtype=bool),features=features)
    before,_=fit_rules(context,('2024-01-01','2024-01-31'))
    context['features'].loc['2024-02-01':]*=100
    after,_=fit_rules(context,('2024-01-01','2024-01-31'))
    assert before==after
    assert len(before)==38


def test_feature_and_policy_causality():
    f=market()
    c=market_context(f)
    rule=dict(family='switch',feature='rv7d',threshold=.1,ladder_when='high')
    before,_,_,_=regime_plan(c,rule)
    cutoff=90*1440+1
    f.loc[f.index[cutoff]:,['open','high','low','close']]*=2
    changed=market_context(f)
    after,_,_,_=regime_plan(changed,rule)
    np.testing.assert_allclose(before[:cutoff+1],after[:cutoff+1],equal_nan=True)
    np.testing.assert_allclose(c['features'].iloc[:90*6],changed['features'].iloc[:90*6],equal_nan=True)


def test_mode_is_latched_even_if_volatility_choice_flips_while_position_open():
    c=market_context(market())
    all_ladder=np.ones(len(c['action_index']),dtype=bool)
    before,_,modes,decisions=regime_plan(c,dict(family='ladder'),all_ladder)
    assert decisions
    first=int(np.flatnonzero(modes==2)[0])
    end=int(np.flatnonzero((np.arange(len(modes))>first)&(modes==0))[0])
    bar=c['action_index'].get_loc(pd.Timestamp(decisions[0]['timestamp_ms'],unit='ms',tz='UTC'))
    changed=all_ladder.copy()
    changed[bar+1:]=False
    after,_,changed_modes,_=regime_plan(c,dict(family='ladder'),changed)
    np.testing.assert_allclose(before[:end+1],after[:end+1],equal_nan=True)
    np.testing.assert_equal(changed_modes[first:end],2)


def test_selection_checks_true_switching_and_separates_frequency_objective():
    base=dict(liquidations=0,natural_closes=20,max_drawdown=-.2,profitable_realization_days_per_month=1.,
        actual_original_entries=0,actual_ladder_entries=0)
    rows=[dict(base,id=0,rule=dict(family='original'),pnl=100.),
        dict(base,id=1,rule=dict(family='ladder'),pnl=110.),
        dict(base,id=2,rule=dict(family='switch'),pnl=200.,actual_original_entries=3,actual_ladder_entries=3),
        dict(base,id=3,rule=dict(family='switch'),pnl=150.,actual_original_entries=3,actual_ladder_entries=3,profitable_realization_days_per_month=1.3),
        dict(base,id=4,rule=dict(family='switch'),pnl=500.,actual_original_entries=0,actual_ladder_entries=6)]
    selected=select_training(rows)
    assert selected['primary']['id']==selected['best_switch']['id']==2
    assert selected['frequency_switch']['id']==3
