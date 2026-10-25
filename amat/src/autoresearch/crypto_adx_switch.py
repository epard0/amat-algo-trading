"""Completed-candle Wilder ADX for the frozen entry-locked SOL exit switch."""
import numpy as np
import pandas as pd
from autoresearch.crypto_volatility_switch import market_context as base_context

LOOKBACKS=(7,14,28)
THRESHOLDS=(15,20,25,30,35,40)
FEATURES=tuple(f'adx{n}' for n in LOOKBACKS)


def wilder_adx(prices,n):
    """Seed TR/DM with n intervals, ADX with n DX; reset at missing bars."""
    if n<2:raise ValueError('ADX lookback must be at least two')
    out=np.full(len(prices),np.nan)
    previous=None
    count=0
    smooth=np.zeros(3)
    dx_seed=[]
    adx=None
    for i,(high,low,close) in enumerate(prices[['high','low','close']].to_numpy(dtype=float)):
        if not np.isfinite([high,low,close]).all():
            previous=None;count=0;smooth[:]=0;dx_seed=[];adx=None
            continue
        if previous is not None:
            ph,pl,pc=previous
            up=high-ph;down=pl-low
            raw=np.array([max(high-low,abs(high-pc),abs(low-pc)),up if up>down and up>0 else 0,down if down>up and down>0 else 0])
            count+=1
            if count<=n:smooth+=raw
            else:smooth=smooth-smooth/n+raw
            if count>=n:
                # The common TR divisor cancels from DX; flat DM means DX=0.
                denom=smooth[1]+smooth[2]
                dx=100*abs(smooth[1]-smooth[2])/denom if denom>0 else 0.
                if adx is None:
                    dx_seed.append(dx)
                    if len(dx_seed)==n:adx=float(np.mean(dx_seed))
                else:adx=(adx*(n-1)+dx)/n
                if adx is not None:out[i]=adx
        previous=(high,low,close)
    return out


def market_context(frame):
    context=base_context(frame)
    context['features']=pd.DataFrame({f'adx{n}':wilder_adx(context['prices'],n) for n in LOOKBACKS},index=context['action_index'])
    return context


def fit_rules(context,train):
    idx=context['action_index']
    mask=(idx>=pd.Timestamp(train[0],tz='UTC'))&(idx<pd.Timestamp(train[1],tz='UTC')+pd.Timedelta(days=1))&context['onset']
    rules=[dict(id=0,family='original'),dict(id=1,family='ladder')]
    audit={}
    for n in LOOKBACKS:
        feature=f'adx{n}'
        values=context['features'].loc[mask,feature].dropna()
        if len(values)<12:raise ValueError('too few valid training entries')
        audit[feature]=dict(valid_training_entries=len(values),minimum=float(values.min()),maximum=float(values.max()))
        for threshold in THRESHOLDS:
            for direction in ('high','low'):
                rules.append(dict(id=len(rules),family='switch',feature=feature,lookback=n,threshold=threshold,ladder_when=direction,valid_training_entries=len(values)))
    return rules,audit
