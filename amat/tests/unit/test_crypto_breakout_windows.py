import numpy as np
import pandas as pd
from autoresearch.crypto_breakout_windows import window_plan,window_context,select_window,entry_metrics
from autoresearch.crypto_adx_switch import market_context
from autoresearch.crypto_volatility_switch import regime_plan
from autoresearch.crypto_partial_exits import exit_plan,policies
from autoresearch.crypto_adx_leverage_study import full_tp_reasons


def market():
    idx=pd.date_range('2024-01-01',periods=90*24,freq='1h',tz='UTC');p=100+np.arange(len(idx))*.08+2*np.sin(np.arange(len(idx))/35)
    f=pd.DataFrame(dict(open=p,high=p+.1,low=p-.1,close=p,tradable=1.),index=idx)
    return f


def test_twenty_day_policy_matches_both_existing_controls():
    f=market();ctx=market_context(f)
    doge,_=exit_plan(f,policies()[0],.5)
    why=full_tp_reasons(ctx,doge,.5)
    actual,reasons=window_plan(f,20,'DOGE')
    np.testing.assert_allclose(np.nan_to_num(actual),np.nan_to_num(doge));np.testing.assert_equal(reasons,why)
    sol,reasons,_,_=regime_plan(ctx,dict(family='switch',feature='adx14',threshold=20,ladder_when='high'))
    full=full_tp_reasons(ctx,sol,2.);reasons[reasons==6]=full[reasons==6]
    actual,why=window_plan(f,20,'SOL');np.testing.assert_allclose(actual,sol,equal_nan=True);np.testing.assert_equal(why,reasons)


def test_all_windows_are_causal_and_exit_channel_remains_ten_days():
    f=market();cut=70*24+1;changed=f.copy();changed.iloc[cut:,:4]*=3
    for days in (5,10,15,20):
        before=window_context(f,days,2.);after=window_context(changed,days,2.)
        np.testing.assert_allclose(before['entry_threshold'][:cut//4],after['entry_threshold'][:cut//4],equal_nan=True)
        np.testing.assert_allclose(before['channel'],market_context(f)['channel'],equal_nan=True)
        for asset in ('DOGE','SOL'):
            w,why=window_plan(f,days,asset);a,b=window_plan(changed,days,asset)
            np.testing.assert_allclose(w[:cut],a[:cut],equal_nan=True);np.testing.assert_equal(why[:cut],b[:cut])


def test_shorter_window_can_enter_earlier():
    f=market();p=100+np.arange(len(f))*.1
    for col in ('open','high','low','close'):f[col]=p
    first={days:np.flatnonzero(window_plan(f,days,'DOGE')[0]>0)[0] for days in (5,10,15,20)}
    assert first[5]<first[10]<first[15]<first[20]


def test_training_selection_is_pnl_based_and_excludes_failed_candidates():
    b=dict(window=20,pnl=100,max_drawdown=-.2,liquidations=0,margin_rejections=0,entries=6,capacity_supported=True)
    rows=[b,dict(b,window=5,pnl=200),dict(b,window=10,pnl=300,liquidations=1),dict(window=15,capacity_supported=False)]
    assert select_window(rows)['window']==5
    assert select_window([dict(b,pnl=-1)])['window'] is None


def test_entry_counts_do_not_count_rebalances_as_new_entries():
    ledger=[dict(symbol='KXSOLPERP',timestamp_ms=i,count=q) for i,q in enumerate([10,5,-4,-11,8,-8])]
    result=entry_metrics(ledger,('2024-01-01','2024-01-31'))
    assert result['entries']==2
    assert result['entries_by_asset']['KXSOLPERP']==2
