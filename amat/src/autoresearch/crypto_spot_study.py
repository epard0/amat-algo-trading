"""Frozen BTC/ETH spot research; no execution authority.

Run: python -m autoresearch.crypto_spot_study --output outputs/crypto-spot-study
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from autoresearch.genetic.data import load_crypto

SYMBOLS = ('BTCUSDT', 'ETHUSDT')
PERIODS = {'train': ('2018-01-01', '2021-12-31'), 'validation': ('2022-01-01', '2022-12-31'), 'test': ('2023-01-01', '2023-12-31')}

def candidates():
    return [dict(timeframe=h, family=f, lookback=d, parameter=p)
            for h in (1, 4) for f, ps in [('trend', (2, 4)), ('breakout', (2, 4)), ('reversion', (1.5, 2.0))]
            for d in (5, 10, 20) for p in ps]

def aggregate(data, hours):
    idx = pd.DatetimeIndex(data.timestamps)
    result = {}
    for j, symbol in enumerate(SYMBOLS):
        frame = pd.DataFrame({k: v[:, j] for k, v in data.fields.items()}, index=idx)
        grouped = frame.resample(f'{hours}h', origin='start_day')
        out = grouped.agg({'open':'first', 'high':'max', 'low':'min', 'close':'last'})
        out[grouped['close'].count() != hours] = np.nan
        # Fills use the actual boundary hour, independent of later missing
        # constituents. Never use future bucket completeness to permit an order.
        out['execution_open'] = frame.open.iloc[::hours].to_numpy()
        out['execution_close'] = frame.close.iloc[hours-1::hours].to_numpy()
        result[symbol] = out
    return result

def targets(frames, cfg):
    h = cfg['timeframe']; n = cfg['lookback'] * 24 // h
    result = []
    for frame in frames.values():
        c = frame.close
        avg = c.rolling(n).mean()
        if cfg['family'] == 'trend':
            signal = avg > c.rolling(n * int(cfg['parameter'])).mean()
        elif cfg['family'] == 'breakout':
            enter = c > frame.high.rolling(n).max().shift(1)
            leave = c < frame.low.rolling(max(2, n // int(cfg['parameter']))).min().shift(1)
            state = np.zeros(len(c)); active = False
            for i in range(len(c)):
                if enter.iloc[i]: active = True
                if leave.iloc[i]: active = False
                state[i] = active
            signal = pd.Series(state, index=c.index)
        else:
            z = (c - avg) / c.rolling(n).std()
            state = np.zeros(len(c)); active = False
            for i in range(len(c)):
                if z.iloc[i] < -cfg['parameter']: active = True
                if z.iloc[i] >= 0: active = False
                state[i] = active
            signal = pd.Series(state, index=c.index)
        # 30 complete days of returns; no smoothing across missing candles.
        vol = c.pct_change(fill_method=None).rolling(30*24//h).std() * np.sqrt(365*24/h)
        ready = c.rolling(n * (int(cfg['parameter']) if cfg['family']=='trend' else 1)).count().eq(n * (int(cfg['parameter']) if cfg['family']=='trend' else 1))
        weight = (0.20 / vol).clip(0, 1) * signal * ready
        weight = weight.where(frame.notna().all(axis=1) & vol.notna(), np.nan)
        # Completed-bar decision at t, execution at open t+1.
        result.append(weight.shift(1).to_numpy())
    return np.column_stack(result)

def replay(frames, target, start, end, bps, buyhold=False, delay=0):
    idx = next(iter(frames.values())).index
    opens = np.column_stack([f.get('execution_open', f.open) for f in frames.values()])
    closes = np.column_stack([f.get('execution_close', f.close) for f in frames.values()])
    mask = (idx >= pd.Timestamp(start, tz='UTC')) & (idx < pd.Timestamp(end, tz='UTC') + pd.Timedelta(days=1))
    rows = np.flatnonzero(mask)
    cash = np.array([5000., 5000.]); units = np.zeros(2); last = np.full(2, np.nan)
    values=[]; turnovers=[]; fees=[]; orders=0
    previous = np.full(2, np.nan); cost = bps / 10000
    for i in rows:
        turnover=0.; fee=0.
        for j in range(2):
            op=opens[i,j]; cl=closes[i,j]
            desired = 1. if buyhold else target[max(0,i-delay),j]
            if np.isfinite(op):
                last[j]=op
                # Rebalance on signal changes or at 00:00 UTC for volatility sizing.
                change = np.isfinite(desired) and (not np.isfinite(previous[j]) or (desired>0)!=(previous[j]>0))
                rebalance = (units[j]==0 and previous[j]!=1) if buyhold else (change or idx[i].hour==0)
                if np.isfinite(desired) and rebalance:
                    equity=cash[j]+units[j]*op
                    delta=desired*equity-units[j]*op
                    if delta>0: delta=min(delta,cash[j]/(1+cost))
                    delta=max(delta,-units[j]*op)
                    units[j]+=delta/op; cash[j]-=delta+abs(delta)*cost
                    turnover+=abs(delta); fee+=abs(delta)*cost
                    orders+=abs(delta)>1e-8
                if np.isfinite(desired): previous[j]=desired
            if np.isfinite(cl): last[j]=cl
        values.append(float(cash.sum()+np.nansum(units*last)))
        turnovers.append(turnover); fees.append(fee)
    # Liquidation at final available mark includes the same cost; disclose stale marks.
    exit_notional=float(np.nansum(units*last)); values[-1]-=exit_notional*cost
    turnovers[-1]+=exit_notional; fees[-1]+=exit_notional*cost
    orders+=int((units>0).sum())
    path=pd.DataFrame({'equity':values,'turnover':turnovers,'cost':fees},index=idx[rows])
    daily=path.equity.resample('1D').last()
    returns=daily.pct_change(); returns.iloc[0]=daily.iloc[0]/10000-1
    eq=np.r_[10000,daily.to_numpy()]
    dd=eq/np.maximum.accumulate(eq)-1
    std=returns.std(ddof=1)
    stats=dict(total_return=float(eq[-1]/10000-1), cagr=float((eq[-1]/10000)**(365/len(daily))-1),
        max_drawdown=float(dd.min()), sharpe=float(returns.mean()/std*np.sqrt(365)) if std>0 else 0.,
        orders=int(orders), turnover=float(sum(turnovers)/10000), costs=float(sum(fees)),
        missing_asset_bars=int((~np.isfinite(opens[rows])).sum()))
    return stats,path,returns

def bootstrap(a,b):
    # Paired 30-calendar-day moving-block bootstrap; fixed winner only.
    x=np.column_stack([a.to_numpy(),b.to_numpy()]); rng=np.random.default_rng(1701)
    samples=[]; n=len(x)
    for _ in range(2000):
        starts=rng.integers(0,n,size=(n+29)//30)
        ids=np.concatenate([(s+np.arange(30))%n for s in starts])[:n]
        samples.append((np.prod(1+x[ids,0])-1)-(np.prod(1+x[ids,1])-1))
    return [float(v) for v in np.quantile(samples,[.025,.975])]

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--data',type=Path,default=Path('data/research/genetic-patterns-2026-09-27'))
    args=parser.parse_args(); out=args.output; out.mkdir(parents=True,exist_ok=False)
    protocol=dict(version=1, periods=PERIODS,candidates=candidates(), symbols=SYMBOLS,
        selection='highest training daily Sharpe with >=20 orders and positive net return; lock one per timeframe and one global winner; validation never retunes',
        costs_bps_per_side=[15,30,50],primary_cost='10bps commission + assumed 5bps spread/slippage',
        capital=10000,allocation='two independent equal initial capital sleeves; 20% annual volatility target per asset capped 100%; rebalance daily or signal change; no leverage; zero cash yield',
        execution='completed bar -> next open; final liquidation at last close; missing candle prevents trading, retains units and last observed valuation; no synthetic signal candles',
        gate='both validation and test positive at 15/30/50bps, test Sharpe >=0.5, test drawdown >=-25%, paired bootstrap lower CI of test excess return over buyhold >0',
        limitations=['historical held-out periods, prior access cannot be excluded','Binance USDT prices; cash assumed stable USDT, not USD','OHLC proxy, no quotes or capacity model, fixed fees not historical fee reconstruction','fixed-winner bootstrap does not correct full search multiplicity','no prospective evidence or execution authorization'],
        fee_source='https://www.binance.com/en/fee/trading',
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        manifest_sha256=hashlib.sha256((args.data/'manifest.json').read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    data=load_crypto(args.data,SYMBOLS,start=dt.date(2018,1,1),end=dt.date(2023,12,31))
    (out/'data-audit.json').write_text(json.dumps(data.manifest,indent=2))
    frames={h:aggregate(data,h) for h in (1,4)}
    training=[]; cache={}
    for k,cfg in enumerate(candidates()):
        cache[k]=targets(frames[cfg['timeframe']],cfg)
        stats,_,_=replay(frames[cfg['timeframe']],cache[k],*PERIODS['train'],15)
        training.append(dict(id=k,config=cfg,**stats))
    (out/'training.json').write_text(json.dumps(training,indent=2))
    selected={}
    for h in (1,4):
        eligible=[r for r in training if r['config']['timeframe']==h and r['orders']>=20 and r['total_return']>0]
        if eligible: selected[str(h)]=max(eligible,key=lambda x:(x['sharpe'],-x['id']))
    winner=max(selected.values(),key=lambda x:x['sharpe']) if selected else None
    (out/'selection.json').write_text(json.dumps(dict(by_timeframe=selected,global_winner=winner),indent=2))
    results=[]; daily_store={}
    for h in (1,4):
        if str(h) not in selected: continue
        chosen=selected[str(h)]; target=cache[chosen['id']]
        for period,(start,end) in PERIODS.items():
            for bps in (15,30,50):
                for name,is_bh in [('selected',False),('buyhold',True)]:
                    stats,path,daily=replay(frames[h],target,start,end,bps,buyhold=is_bh)
                    label=f'{h}h-{period}-{bps}bps-{name}'
                    path.to_csv(out/f'{label}.csv',index_label='timestamp')
                    daily_store[(h,period,bps,name)]=daily
                    results.append(dict(timeframe=h,period=period,cost_bps=bps,policy=name,**stats))
        stats,_,_=replay(frames[h],target,*PERIODS['test'],15,delay=1)
        results.append(dict(timeframe=h,period='test',cost_bps=15,policy='selected-extra-bar-delay',**stats))
    cis={str(h):bootstrap(daily_store[h,'test',15,'selected'],daily_store[h,'test',15,'buyhold']) for h in (1,4) if str(h) in selected}
    gates={}
    for h in (1,4):
        relevant=[r for r in results if r['timeframe']==h and r['policy']=='selected']
        test=next((r for r in relevant if r['period']=='test' and r['cost_bps']==15),None)
        gates[str(h)]=bool(test and all(r['total_return']>0 for r in relevant if r['period']!='train') and test['sharpe']>=.5 and test['max_drawdown']>=-.25 and cis[str(h)][0]>0)
    summary=dict(results=results,test_excess_return_bootstrap_95ci=cis,gates=gates,global_winner_id=winner['id'] if winner else None)
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    lines=['# BTC/ETH spot study','', 'Frozen training selection; validation and final test do not retune parameters.','',
        '| Bars | Period | Cost/side | Policy | Net return | Max DD | Daily Sharpe | Orders |',
        '|---|---|---:|---|---:|---:|---:|---:|']
    for r in results:
        lines.append(f"| {r['timeframe']}h | {r['period']} | {r['cost_bps']} bps | {r['policy']} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['sharpe']:.2f} | {r['orders']} |")
    lines+=['',f'Selected configurations: `{json.dumps(selected)}`','',f'Paired test excess-return 95% intervals: `{cis}`',f'Predeclared gates: `{gates}`','', '## Limits','']+protocol['limitations']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(12,7))
    for row,h in enumerate((1,4)):
        for col,period in enumerate(('validation','test')):
            for name in ('selected','buyhold'):
                if (h,period,15,name) in daily_store:
                    daily=daily_store[h,period,15,name]
                    axes[row,col].plot(daily.index,10000*(1+daily).cumprod(),label=name)
            axes[row,col].set_title(f'{h}h {period}: 15bps/side'); axes[row,col].legend(); axes[row,col].tick_params(axis='x',rotation=25)
    fig.tight_layout(); fig.savefig(out/'equity.png',dpi=150)
    print(json.dumps(dict(selected=selected,gates=gates,ci=cis),indent=2))

if __name__=='__main__': main()
