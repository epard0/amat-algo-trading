"""Entry-only strong-downtrend gate, preserving the default signed exit calendars."""
import numpy as np
import pandas as pd
from autoresearch.crypto_adx_switch import market_context
from autoresearch.crypto_volatility_switch import regime_plan
from autoresearch.crypto_adx_leverage_study import full_tp_reasons
from autoresearch.crypto_breakout_windows import window_plan


def context(frame,template):
    ctx=market_context(frame);original=ctx['prices'];p=original.copy()
    p['open']=-original.open;p['close']=-original.close;p['high']=-original.low;p['low']=-original.high
    ctx['prices']=p;entry=p.high.rolling(120).max().shift(1).to_numpy();channel=p.low.rolling(60).min().shift(1).to_numpy();ctx['channel']=channel
    level=np.zeros(len(p));holding=False;ref=atr=0.;entered=0;tp=.5 if template=='DOGE' else 2.
    for i,price in enumerate(p.close.to_numpy()):
        if not np.isfinite(price) or not np.isfinite(entry[i]):level[i]=np.nan;continue
        exited=False
        if holding and (price<channel[i] or price-ref>=tp*atr or i-entered>=18):holding=False;exited=True
        if not holding and not exited and price>entry[i]:holding=True;ref=price;atr=ctx['atr'][i];entered=i
        level[i]=.98 if holding else 0.
    ctx['baseline']=level;ctx['onset']=np.isfinite(level)&(level>0)&(np.r_[0,np.nan_to_num(level[:-1])]==0)
    return ctx,original


def short_from_context(ctx,original,template,threshold=None,ema_bars=None):
    onset=ctx['onset'].copy();gate=np.ones(len(original),dtype=bool)
    if threshold is not None:
        ema=original.close.ewm(span=ema_bars,adjust=False,min_periods=ema_bars).mean()
        gate=(ctx['features']['adx14'].to_numpy()>=threshold)&(original.close.to_numpy()<ema.to_numpy())&(ema.to_numpy()<ema.shift(3).to_numpy())
    ctx=dict(ctx,onset=onset&gate)
    if template=='DOGE':
        allowed=False;level=ctx['baseline'].copy()
        for i,value in enumerate(level):
            if onset[i]:allowed=bool(gate[i])
            if np.isfinite(value) and value==0:allowed=False
            if np.isfinite(value) and not allowed:level[i]=0.
        w=pd.Series(level,index=ctx['action_index']).reindex(ctx['index'],method='ffill').to_numpy()
        why=full_tp_reasons(ctx,w,.5)
    else:
        w,why,_,_=regime_plan(ctx,dict(family='switch',feature='adx14',threshold=20,ladder_when='high'))
        why=why.copy();full=full_tp_reasons(ctx,w,2.);why[why==6]=full[why==6]
    return -w,why,dict(raw_onsets=int(onset.sum()),admitted_onsets=int((onset&gate).sum()))


def combine(long_plan,short_plan):
    long,long_why=long_plan;short,short_why=short_plan[:2]
    w=np.zeros(len(long));why=np.zeros(len(long),dtype=np.int8);held=0;pl=ps=0.
    for i,(l,s) in enumerate(zip(long,short,strict=True)):
        if not np.isfinite(l) or not np.isfinite(s):w[i]=np.nan;continue
        if held:
            value=l if held>0 else s;w[i]=value;why[i]=long_why[i] if held>0 else short_why[i]
            if value==0:held=0
        elif l>0 and pl==0:held=1;w[i]=l;why[i]=long_why[i]
        elif s<0 and ps==0:held=-1;w[i]=s;why[i]=short_why[i]
        pl=l;ps=s
    return w,why
