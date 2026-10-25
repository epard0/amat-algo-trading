import numpy as np
import pandas as pd
from autoresearch.crypto_trend_short import context, short_from_context, combine
from autoresearch.crypto_signed_adx import signed_plan
from autoresearch.crypto_signed_replay import replay_signed
from autoresearch.crypto_trend_replay import replay_trend
from autoresearch.crypto_fast_breakout import prepare
from test_crypto_adx_leverage import fixture


def frame():
    idx=pd.date_range('2025-01-01',periods=24*90,freq='1h',tz='UTC');x=np.arange(len(idx));c=100+np.sin(x/30)*5-x*.02
    return pd.DataFrame(dict(open=c,high=c+.01,low=c-.01,close=c,tradable=1),index=idx)


def test_no_gate_exact_default_signed_short_parity():
    f=frame()
    for template in ['DOGE','SOL']:
        ctx,p=context(f,template);w,why,_=short_from_context(ctx,p,template)
        ew,ey=signed_plan(f,template,'short')
        np.testing.assert_allclose(w,ew,equal_nan=True);np.testing.assert_equal(why,ey)


def test_gate_is_entry_only_and_uses_completed_data():
    f=frame();ctx,p=context(f,'SOL');w,why,audit=short_from_context(ctx,p,'SOL',35,100)
    ema=p.close.ewm(span=100,adjust=False,min_periods=100).mean()
    gate=(ctx['features']['adx14'].to_numpy()>=35)&(p.close.to_numpy()<ema.to_numpy())&(ema.to_numpy()<ema.shift(3).to_numpy())
    assert audit['admitted_onsets']==int((ctx['onset']&gate).sum())
    # Future candles cannot change previously completed decisions.
    changed=f.copy();changed.iloc[-100:,changed.columns.get_loc('close')]*=.5;changed.iloc[-100:,changed.columns.get_loc('low')]*=.5
    other,prices=context(changed,'SOL');nw,ny,_=short_from_context(other,prices,'SOL',35,100)
    np.testing.assert_allclose(w[:-104],nw[:-104],equal_nan=True)
    # An impossible ADX threshold rejects every short for both exit templates.
    for template in ['DOGE','SOL']:
        cc,pp=context(f,template);blocked,_,_=short_from_context(cc,pp,template,101,100)
        assert not np.any(np.nan_to_num(blocked)<0)


def test_minute_replay_matches_signed_kernel_within_cost_cap():
    f,inst,meta,costs,plans=fixture();data={s:prepare(v,{}) for s,v in f.items()}
    for lev in ['1','2','max']:
        a,ap,al,ar,ac=replay_signed(data,plans,inst,meta,costs,('2026-01-01','2026-01-02'),lev)
        b,bp,bl,br,bc=replay_trend(data,plans,inst,meta,costs,('2026-01-01','2026-01-02'),lev)
        assert a==b and al==bl and ar==br and ac==bc
        np.testing.assert_equal(ap.equity.to_numpy(),bp.equity.to_numpy())


def test_hourly_frequency_normalizes_actual_calendar_time():
    f,inst,meta,costs,_=fixture();data={};plans={}
    for s,v in f.items():
        h=v.resample('1h').first();ms=h.index.as_unit('ms').asi8
        data[s]=dict(index=h.index,ms=ms,a={k:h[k].to_numpy() for k in h},funding={},blackout=np.zeros(len(h),bool),unavailable=np.zeros(len(h),bool))
        w=np.zeros(len(h));w[:30]=.98;why=np.zeros(len(h),dtype=int);why[30]=7;plans[s]=(w,why)
    stats,*_=replay_trend(data,plans,inst,meta,costs,('2026-01-01','2026-01-02'),'1')
    assert stats['take_profit_events']==2
    assert stats['take_profit_events_per_month']==2/(2/30.4375)


def test_adx_gate_is_latched_at_entry_and_does_not_force_an_exit():
    f=frame();ctx,p=context(f,'SOL');ctx=dict(ctx,features=ctx['features'].copy())
    ctx['features']['adx14']=0.
    first=int(np.flatnonzero(ctx['onset'])[0]);ctx['features'].iloc[first,ctx['features'].columns.get_loc('adx14')]=40.
    w,_,audit=short_from_context(ctx,p,'SOL',35,50)
    assert audit['admitted_onsets']==1
    active=np.flatnonzero(w<0);assert len(active)>4
    assert w[active[0]+4]<0  # Four hourly observations later, ADX is already below the gate.


def test_orders_above_the_default_cost_cap_are_not_assumed_filled():
    import pytest
    f,inst,meta,costs,plans=fixture();data={s:prepare(v,{}) for s,v in f.items()}
    for table in costs.values():
        for side in table.values():
            for row in side:row['median_bps']=27.
    with pytest.raises(ValueError,match='outside execution cost cap'):
        replay_trend(data,plans,inst,meta,costs,('2026-01-01','2026-01-02'),'1')
