"""Signed extension of the default four-hour DOGE/SOL ADX exit engine.

Raw market candles remain positive observed prices. Only the internal comparison
coordinates are sign-reflected for short symmetry; ADX and ATR use original OHLC.
Long-only delegates to the frozen default plan and is parity tested.
"""
import numpy as np
import pandas as pd
from autoresearch.crypto_breakout_windows import window_plan
from autoresearch.crypto_adx_switch import market_context
from autoresearch.crypto_volatility_switch import regime_plan
from autoresearch.crypto_adx_leverage_study import full_tp_reasons


def short_plan(frame,asset):
    ctx=market_context(frame)
    original=ctx['prices'];p=original.copy()
    p['open']=-original.open;p['close']=-original.close
    p['high']=-original.low;p['low']=-original.high
    ctx['prices']=p
    entry=p.high.rolling(120).max().shift(1).to_numpy()
    channel=p.low.rolling(60).min().shift(1).to_numpy()
    ctx['channel']=channel
    level=np.zeros(len(p));holding=False;reference=atr=0.;entered=0
    tp=.5 if asset=='DOGE' else 2.
    for i,price in enumerate(p.close.to_numpy()):
        if not np.isfinite(price) or not np.isfinite(entry[i]):level[i]=np.nan;continue
        exited=False
        if holding and (price<channel[i] or price-reference>=tp*atr or i-entered>=18):
            holding=False;exited=True
        if not holding and not exited and price>entry[i]:
            holding=True;reference=price;atr=ctx['atr'][i];entered=i
        level[i]=.98 if holding else 0.
    onset=np.zeros(len(p),dtype=bool);previous=0.
    for i,w in enumerate(level):
        onset[i]=np.isfinite(w) and w>0 and previous==0
        if np.isfinite(w):previous=w
    ctx['baseline']=level;ctx['onset']=onset
    if asset=='DOGE':
        w=pd.Series(level,index=ctx['action_index']).reindex(frame.index,method='ffill').to_numpy()
        why=full_tp_reasons(ctx,w,.5)
    else:
        w,why,_,_=regime_plan(ctx,dict(family='switch',feature='adx14',threshold=20,ladder_when='high'))
        why=why.copy();full=full_tp_reasons(ctx,w,2.);why[why==6]=full[why==6]
    return -w,why


def signed_plan(frame,asset,mode):
    if mode not in ('long','short','both'):raise ValueError('unknown direction mode')
    if mode=='long':return window_plan(frame,20,asset)
    short,short_why=short_plan(frame,asset)
    if mode=='short':return short,short_why
    long,long_why=window_plan(frame,20,asset)
    # One net position per asset. Ignore opposite onsets while held; no same-bar reversal.
    w=np.zeros(len(long));why=np.zeros(len(long),dtype=np.int8);held=0
    pl=ps=0.
    for i,(l,s) in enumerate(zip(long,short,strict=True)):
        if not np.isfinite(l) or not np.isfinite(s):w[i]=np.nan;continue
        if held:
            value=l if held>0 else s
            w[i]=value;why[i]=long_why[i] if held>0 else short_why[i]
            if value==0:held=0
        elif l>0 and pl==0:held=1;w[i]=l;why[i]=long_why[i]
        elif s<0 and ps==0:held=-1;w[i]=s;why[i]=short_why[i]
        pl=l;ps=s
    return w,why


def fresh_signed_plan(weights,reasons,index,dates):
    w=weights.copy();why=reasons.copy()
    first=index.searchsorted(pd.Timestamp(dates[0],tz='UTC'))
    if first and np.isfinite(w[first]) and w[first]!=0:
        prior=np.nan_to_num(w[:first],nan=0.)
        if np.sign(prior[-1])==np.sign(w[first]):
            direction=np.sign(w[first]);after=np.flatnonzero(np.sign(np.nan_to_num(w[first:],nan=direction))!=direction)
            stop=first+int(after[0]) if len(after) else len(w)
            w[first:stop]=0;why[first:stop]=0
    return w,why
