"""Controlled 1/5/15/30 minute resolution experiment plus same-period 4h control."""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import time
import numpy as np
import pandas as pd
import polars as pl
from autoresearch.crypto_intraday import minute_weights,run_intraday
from autoresearch.crypto_perpetual_study import load_inputs

TRAIN=('2026-01-01','2026-03-31')
PERIODS={'validation':('2026-04-01','2026-06-30'),'test':('2026-07-01','2026-09-30'),
         'post_training':('2026-04-01','2026-09-30')}


def load_minute_inputs(root):
    manifest=json.loads((root/'manifest.json').read_text());frames={};audit={}
    grid=pd.date_range('2026-01-01','2026-10-01',freq='1min',inclusive='left',tz='UTC')
    for symbol in ('BTCUSDT','ETHUSDT'):
        combined=None
        for kind in ('klines','markPriceKlines'):
            r=next(r for r in manifest['datasets'] if r['symbol']==symbol and r['kind']==kind)
            path=root/r['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=r['sha256']:raise ValueError('minute input hash mismatch')
            data=pl.read_parquet(path);f=pd.DataFrame({name:data[name].to_numpy() for name in data.columns})
            t=f.open_time_ms.to_numpy(dtype='int64')
            if len(f)!=r['rows'] or len(set(t))!=len(t):raise ValueError('minute count/uniqueness failed')
            if not (t%60000==0).all() or not (f.close_time_ms==f.open_time_ms+59999).all():raise ValueError('off-grid or irregular minute')
            f.index=pd.to_datetime(t,unit='ms',utc=True);f=f.reindex(grid)
            prices=f[['open','high','low','close']]
            if not np.isfinite(prices.to_numpy()).all():raise ValueError('missing/nonfinite minute prices')
            if not ((f.low>0)&(f.low<=f[['open','close']].min(axis=1))&(f.high>=f[['open','close']].max(axis=1))).all():raise ValueError('invalid minute OHLC')
            if kind=='klines':combined=prices.copy();combined['tradable']=(f.volume>0).astype(float)
            else:
                for column in prices:combined['mark_'+column]=prices[column]
        frames[symbol]=combined;audit[symbol]=dict(minutes=len(combined),zero_volume_minutes=int((combined.tradable==0).sum()),missing_prices=0)
    return frames,audit


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    out=args.output;out.mkdir(parents=True,exist_ok=False);source=Path('data/research/crypto-minute-2026-q1-q3')
    cfgs=[dict(minutes=m,leverage=l,sizing=s,tp_atr=tp) for m,l,s,tp in itertools.product((1,5,15,30,240),(1,2),('full','vol20'),(None,2.))]
    protocol=dict(training=TRAIN,periods=PERIODS,candidates=cfgs,portfolios=['BTC-long','ETH-short','BTC-long-ETH-short'],
        registered_candidates=120,execution='canonical 1m trade and mark candles at every signal resolution, no fabricated finer candles',
        hypotheses='preserve 20day entry/10day exit horizons; ATR14 uses native signal bars; profit triggers act on completed signal close then next open; TP2 includes72h max hold',
        sizing='training P&L selects full or20% asset-vol sizing for each interval/portfolio/leverage; .98x/1.96x policy multiplier',
        primary_cost='4bps current Kalshi taker fee +4bps modeled spread/slippage; historical prices/funding remain Binance proxies',
        selection='max training net P&L with >=1 completed episode and no liquidation; frequent profile also requires positive training P&L and >=2 profitable closes/month',
        budgets='120 training recipes; all selected P&L recipes evaluated on Q2/Q3 at8bps; same-period4h control; best frequent primary at8bps; global training P&L/frequency winners also stress at15/30bps',
        funding='actual Binance historical funding rates at actual events, charged only once to pre-order position',
        caveats=['short Q1 training and Q2/Q3 tests cannot establish robust multiyear behavior','periods already inspected at coarser resolution','ATR14 on1m can be much smaller than roundtrip costs','positive close count is not a withdrawal schedule','minute high/low liquidation remains conservative, not exact venue execution'],
        data_manifest_sha256=hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),
        source_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in [Path(__file__),Path(__file__).with_name('crypto_intraday.py'),Path(__file__).with_name('perpetual_sim.py')]})
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    all_frames,audit=load_minute_inputs(source);_,all_funding,_=load_inputs(Path('data/research/crypto-perpetual-2020-2026-09'))
    (out/'data-audit.json').write_text(json.dumps(audit,indent=2))
    portfolios={'BTC-long':{'BTCUSDT':1},'ETH-short':{'ETHUSDT':-1},'BTC-long-ETH-short':{'BTCUSDT':1,'ETHUSDT':-1}}
    training=[];cache={};selected={};missing=[]
    for name,directions in portfolios.items():
        frames={s:all_frames[s] for s in directions};funding={s:all_funding[s] for s in directions}
        for i,cfg in enumerate(cfgs):
            # Leverage changes only size, not signal generation. Cache the base recipe.
            key=name,cfg['minutes'],cfg['sizing'],cfg['tp_atr']
            if key not in cache:cache[key]=minute_weights(frames,cfg|{'leverage':1},directions)
            weights=cache[key]*cfg['leverage']
            started=time.monotonic()
            stats,*_=run_intraday(frames,funding,weights,TRAIN,leverage_limit=cfg['leverage'])
            stats.pop('closed_episodes');training.append(dict(portfolio=name,id=i,config=cfg,**stats))
            (out/'training-progress.json').write_text(json.dumps(training,indent=2))
            print(f'train {len(training)}/120 {name} {cfg["minutes"]}m {cfg["leverage"]}x pnl={stats["pnl"]:.2f} elapsed={time.monotonic()-started:.1f}s',flush=True)
        for minutes,lev in itertools.product((1,5,15,30,240),(1,2)):
            eligible=[r for r in training if r['portfolio']==name and r['config']['minutes']==minutes and r['config']['leverage']==lev and r['round_trips']>=1 and r['liquidations']==0]
            label=f'{name}-{minutes}m-{lev}x'
            if not eligible:missing.append(dict(group=label,reason='no active liquidation-free training candidate'));continue
            selected[label+'-pnl']=max(eligible,key=lambda r:r['pnl'])
            frequent=[r for r in eligible if r['pnl']>0 and r['profitable_closes_per_month']>=2]
            if frequent:selected[label+'-frequent']=max(frequent,key=lambda r:r['pnl'])
            else:missing.append(dict(group=label,reason='no positive-net candidate with >=2 profitable closes/month'))
    pnl_winner=max((k for k in selected if k.endswith('-pnl')),key=lambda k:selected[k]['pnl'])
    frequency_labels=[k for k in selected if k.endswith('-frequent')]
    freq_winner=max(frequency_labels,key=lambda k:selected[k]['pnl']) if frequency_labels else None
    (out/'selection.json').write_text(json.dumps(dict(selected=selected,global_training_pnl=pnl_winner,global_training_frequent=freq_winner,missing=missing),indent=2))
    results=[];replays={}
    for label,row in selected.items():
        name=row['portfolio'];cfg=row['config'];directions=portfolios[name]
        frames={s:all_frames[s] for s in directions};funding={s:all_funding[s] for s in directions}
        weights=cache[name,cfg['minutes'],cfg['sizing'],cfg['tp_atr']]*cfg['leverage']
        for period,dates in PERIODS.items():
            costs=(8,15,30) if label in (pnl_winner,freq_winner) else (8,)
            for cost in costs:
                key=name,row['id'],period,cost
                if key not in replays:replays[key]=run_intraday(frames,funding,weights,dates,cost=cost,leverage_limit=cfg['leverage'])
                stats,path,_,_=replays[key];stats={k:v for k,v in stats.items() if k!='closed_episodes'}
                results.append(dict(strategy=label,portfolio=name,config=cfg,period=period,cost_bps=cost,**stats))
                if cost==8:path.resample('1h').last().to_csv(out/f'{label}-{period}.csv',index_label='timestamp')
        print('evaluated',label,flush=True)
    summary=dict(results=results,global_training_pnl=pnl_winner,global_training_frequent=freq_winner,missing=missing)
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    lines=['# Candle resolution and profit realization experiment','',f'Global training P&L winner: {pnl_winner}; frequent-profit winner: {freq_winner}.','',
           '| Strategy | Period | Cost/side | P&L | Max DD | Vol | Profitable closes/month | Median holding hours | Positive weeks |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in results:lines.append(f"| {r['strategy']} | {r['period']} | {r['cost_bps']}bps | {r['pnl']:.2f} | {r['max_drawdown']:.2%} | {r['annualized_volatility']:.2%} | {r['profitable_closes_per_month']:.2f} | {r['median_holding_hours']} | {r['positive_week_fraction']:.1%} |")
    lines+=['','## Limits','']+protocol['caveats'];(out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    snap=out/'source';snap.mkdir()
    for f in [Path(__file__),Path(__file__).with_name('crypto_intraday.py')]:shutil.copy2(f,snap/f.name)
    print(json.dumps(dict(pnl_winner=pnl_winner,frequent_winner=freq_winner),indent=2))

if __name__=='__main__':main()
