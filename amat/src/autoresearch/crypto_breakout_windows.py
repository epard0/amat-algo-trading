"""Entry-lookback variations with otherwise frozen ADX DOGE/SOL exits."""
import numpy as np
import pandas as pd
from autoresearch.crypto_adx_switch import market_context
from autoresearch.crypto_volatility_switch import regime_plan
from autoresearch.crypto_adx_leverage_study import full_tp_reasons

WINDOWS=(5,10,15,20)


def window_context(frame,days,tp):
    if days not in WINDOWS:raise ValueError('unregistered breakout window')
    context=market_context(frame)
    p=context['prices'];entry=p.high.rolling(days*6).max().shift(1).to_numpy()
    level=np.zeros(len(p));holding=False;ref=atr=0.;entered=0
    for i,price in enumerate(p.close.to_numpy()):
        if not np.isfinite(price) or not np.isfinite(entry[i]):
            level[i]=np.nan
            continue
        exited=False
        if holding and (price<context['channel'][i] or price-ref>=tp*atr or i-entered>=18):
            holding=False;exited=True
        if not holding and not exited and price>entry[i]:
            holding=True;ref=price;atr=context['atr'][i];entered=i
        level[i]=.98 if holding else 0.
    onset=np.zeros(len(p),dtype=bool);previous=0.
    for i,w in enumerate(level):
        onset[i]=np.isfinite(w) and w>0 and previous==0
        if np.isfinite(w):previous=w
    context['baseline']=level;context['onset']=onset
    context['entry_threshold']=entry
    return context


def window_plan(frame,days,asset):
    if asset not in ('DOGE','SOL'):raise ValueError('unsupported asset')
    ctx=window_context(frame,days,.5 if asset=='DOGE' else 2.)
    if asset=='DOGE':
        w=pd.Series(ctx['baseline'],index=ctx['action_index']).reindex(frame.index,method='ffill').to_numpy()
        why=full_tp_reasons(ctx,w,.5)
    else:
        w,why,_,_=regime_plan(ctx,dict(family='switch',feature='adx14',threshold=20,ladder_when='high'))
        why=why.copy();full=full_tp_reasons(ctx,w,2.);why[why==6]=full[why==6]
    return w,why


def entry_metrics(ledger,dates):
    count={};entries=[]
    for fill in ledger:
        s=fill['symbol'];old=count.get(s,0.);delta=float(fill['count'])
        if delta>0 and old==0:entries.append(dict(symbol=s,timestamp_ms=fill['timestamp_ms']))
        count[s]=old+delta
    days=(pd.Timestamp(dates[1])+pd.Timedelta(days=1)-pd.Timestamp(dates[0])).days;months=days/30.4375
    return dict(entries=len(entries),entries_per_month=len(entries)/months,entry_days=len({e['timestamp_ms']//86400000 for e in entries}),entries_by_asset={s:sum(e['symbol']==s for e in entries) for s in ('KXDOGEPERP','KXSOLPERP')})


def select_window(rows):
    eligible=[r for r in rows if r.get('capacity_supported',True) and r['pnl']>0 and r['liquidations']==0 and r['margin_rejections']==0 and r['entries']>=6]
    if not eligible:return dict(window=None,reason='No eligible positive liquidation-free candidate')
    winner=max(eligible,key=lambda r:(r['pnl'],r['max_drawdown'],r['window']))
    return dict(window=winner['window'],training=winner,eligible_windows=[r['window'] for r in eligible],criterion='Maximum training net P&L; ties lower drawdown then longer window. Require positive P&L,zero liquidation/rejection,>=6 actual entries and supported empirical depth.')
