"""Causal, entry-locked choice between original SOL exits and its fixed ladder."""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.crypto_doge_sol import profit_weights

FEATURES=('rv3d','rv7d','rv14d','rv30d','atr14','atr42')
QUANTILES=(.25,.5,.75)


def market_context(frame):
    hourly=frame.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','tradable':'max'})
    base=pd.Series(profit_weights(hourly,2.,72)[:,0],index=hourly.index)
    grouped=hourly[['open','high','low','close']].where(hourly.tradable>0,axis=0).resample('4h')
    p=grouped.agg({'open':'first','high':'max','low':'min','close':'last'})
    p[grouped.close.count()!=4]=np.nan
    action_index=p.index+pd.Timedelta(hours=4)
    baseline=base.reindex(action_index).to_numpy()
    tr=pd.concat([p.high-p.low,(p.high-p.close.shift()).abs(),(p.low-p.close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14).mean().to_numpy()
    returns=np.log(p.close).diff()
    feature=pd.DataFrame(index=action_index)
    for days in (3,7,14,30):
        feature[f'rv{days}d']=(returns.rolling(days*6).std(ddof=1)*np.sqrt(365*6)).to_numpy()
    for bars in (14,42):
        feature[f'atr{bars}']=(tr.rolling(bars).mean()/p.close).to_numpy()
    onset=np.zeros(len(p),dtype=bool)
    previous=0.
    for i,b in enumerate(baseline):
        onset[i]=np.isfinite(b) and b>0 and previous==0
        if np.isfinite(b):previous=b
    return dict(index=frame.index,action_index=action_index,prices=p,base=base,baseline=baseline,
        atr=atr,channel=p.low.rolling(60).min().shift(1).to_numpy(),features=feature,onset=onset)


def fit_rules(context,train):
    """Fit cutoffs solely to training original-entry volatility observations."""
    idx=context['action_index']
    mask=(idx>=pd.Timestamp(train[0],tz='UTC'))&(idx<pd.Timestamp(train[1],tz='UTC')+pd.Timedelta(days=1))&context['onset']
    rules=[dict(id=0,family='original'),dict(id=1,family='ladder')]
    audit={}
    for name in FEATURES:
        values=context['features'].loc[mask,name].dropna()
        if len(values)<12:raise ValueError('too few valid training entry observations: '+name)
        audit[name]=dict(valid_training_entries=len(values),minimum=float(values.min()),maximum=float(values.max()))
        for quantile in QUANTILES:
            threshold=float(values.quantile(quantile))
            for ladder_when in ('high','low'):
                rules.append(dict(id=len(rules),family='switch',feature=name,quantile=quantile,
                    threshold=threshold,ladder_when=ladder_when,valid_training_entries=len(values)))
    return rules,audit


def choose_ladder(context,rule):
    if rule['family']=='original':return np.zeros(len(context['action_index']),dtype=bool)
    if rule['family']=='ladder':return np.ones(len(context['action_index']),dtype=bool)
    x=context['features'][rule['feature']].to_numpy()
    above=x>=rule['threshold'] if rule['ladder_when']=='high' else x<=rule['threshold']
    return np.isfinite(x)&above  # Missing feature means original exits.


def regime_plan(context,rule,entry_choices=None):
    """Latch one mode at each original entry; never switch an open position."""
    choices=choose_ladder(context,rule) if entry_choices is None else np.asarray(entry_choices,dtype=bool)
    p=context['prices']
    levels=np.zeros(len(p))
    reasons=np.zeros(len(p),dtype=np.int8)
    modes=np.zeros(len(p),dtype=np.int8)
    remaining=0.
    mode=stage=entered=0
    reference=entry_atr=high=0.
    armed=False
    decisions=[]
    for i,price in enumerate(p.close.to_numpy()):
        b=context['baseline'][i]
        if not np.isfinite(price) or not np.isfinite(context['atr'][i]):
            levels[i]=np.nan
            modes[i]=mode if remaining else 0
            continue
        exited=False
        if remaining:
            if mode==1:
                if not np.isfinite(b):
                    levels[i]=np.nan
                    modes[i]=mode
                    continue
                if b==0:
                    remaining=0.
                    reasons[i]=6
                    exited=True
            else:
                high=max(high,price)
                reason=0
                if price<context['channel'][i]:reason=4
                elif i-entered>=18:reason=5  #72h.
                elif armed and price<=high-3*entry_atr:reason=3
                if reason:
                    remaining=0.
                    reasons[i]=reason
                    exited=True
                elif stage<2 and price-reference>=(1.,2.)[stage]*entry_atr:
                    remaining-=.25
                    stage+=1
                    armed=True
                    reasons[i]=2
        if not remaining and not exited and context['onset'][i]:
            remaining=1.
            mode=2 if choices[i] else 1
            stage=0
            entered=i
            reference=high=price
            entry_atr=context['atr'][i]
            armed=False
            reasons[i]=1
            value=context['features'][rule['feature']].iloc[i] if rule['family']=='switch' else np.nan
            decisions.append(dict(timestamp_ms=int(context['action_index'][i].timestamp()*1000),
                mode='ladder' if mode==2 else 'original',feature_value=float(value) if np.isfinite(value) else None))
        levels[i]=remaining*.98
        modes[i]=mode if remaining else 0
    idx=context['action_index']
    w=pd.Series(levels,index=idx).reindex(context['index'],method='ffill').to_numpy()
    why=pd.Series(reasons,index=idx).reindex(context['index'],method='ffill').fillna(0).to_numpy(dtype=np.int8)
    minute_modes=pd.Series(modes,index=idx).reindex(context['index'],method='ffill').fillna(0).to_numpy(dtype=np.int8)
    return w,why,minute_modes,decisions


def decision_counts(decisions,dates):
    first=int(pd.Timestamp(dates[0],tz='UTC').timestamp()*1000)
    stop=int((pd.Timestamp(dates[1],tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)
    active=[d for d in decisions if first<=d['timestamp_ms']<stop]
    return dict(planned_original_entries=sum(d['mode']=='original' for d in active),
        planned_ladder_entries=sum(d['mode']=='ladder' for d in active))


def select_training(rows):
    eligible=[r for r in rows if r['pnl']>0 and r['liquidations']==0 and r['natural_closes']>=6]
    switches=[r for r in eligible if r['rule']['family']=='switch' and r['actual_original_entries']>=2 and r['actual_ladder_entries']>=2]
    constants=[r for r in eligible if r['rule']['family']!='switch']
    if not constants:raise ValueError('no eligible fixed control')
    baseline=next(r for r in rows if r['id']==0)
    constrained=[r for r in switches if r['pnl']>=baseline['pnl'] and r['max_drawdown']>=baseline['max_drawdown']
        and r['profitable_realization_days_per_month']>=1.25*baseline['profitable_realization_days_per_month']]
    return dict(primary=max(constants+switches,key=lambda r:r['pnl']),
        best_switch=max(switches,key=lambda r:r['pnl']) if switches else None,
        frequency_switch=max(constrained,key=lambda r:r['pnl']) if constrained else None,
        eligible_switches=len(switches),frequency_eligible=len(constrained))
