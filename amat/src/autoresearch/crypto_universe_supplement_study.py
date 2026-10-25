"""Training-selected directional proxy screens for authenticated Kalshi crypto universe."""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
from autoresearch.crypto_directional import directional_weights,run_directional
HOUR=3600000

def load_asset(root,symbol):
    manifest=json.loads((root/'manifest.json').read_text())
    index=pd.date_range('2021-01-01','2026-10-01',freq='1h',inclusive='left',tz='UTC')
    frames={}; funding={}; quality={}
    for symbol in (symbol,):
        pieces={}
        for kind in ('klines','markPriceKlines','fundingRate'):
            record=next(r for r in manifest['datasets'] if r['symbol']==symbol and r['kind']==kind)
            path=root/record['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:
                raise ValueError('input checksum mismatch')
            frame=pd.read_csv(path)
            if len(frame)!=record['rows']: raise ValueError('input row count mismatch')
            if kind=='fundingRate':
                times=frame.calc_time.to_numpy(dtype='int64')
                hours=times//HOUR
                offsets=times%HOUR
                if (offsets>60000).any(): raise ValueError('funding time not within first minute of hour')
                if len(set(hours))!=len(hours): raise ValueError('multiple funding records per hour')
                intervals=frame.funding_interval_hours.to_numpy(dtype='float64')
                if not np.isfinite(intervals).all() or (intervals<=0).any(): raise ValueError('invalid funding intervals')
                gaps=np.diff(times)/HOUR
                if (gaps>np.maximum(intervals[:-1],intervals[1:])+.017).any():
                    raise ValueError('funding gap exceeds reported interval; cannot assume zero funding')
                rates=frame.last_funding_rate.to_numpy(dtype='float64')
                if not np.isfinite(rates).all() or (abs(rates)>=1).any(): raise ValueError('invalid funding rate')
                first=int(index[0].timestamp()*1000)//HOUR; stop=int((index[-1]+pd.Timedelta(hours=1)).timestamp()*1000)//HOUR
                if hours[0]!=first or stop-hours[-1]>intervals[-1]: raise ValueError('funding boundary incomplete')
                funding[symbol]={int(t//HOUR): (int(t),str(r)) for t,r in zip(times,frame.last_funding_rate,strict=True)}
                pieces['funding_quality']=dict(events=len(frame),max_timestamp_offset_ms=int(offsets.max()),
                    max_interval_hours=float(intervals.max()),min_rate=float(rates.min()),max_rate=float(rates.max()),
                    total_rate_sum=float(rates.sum()),missing_assumed_zero=False)
                continue
            times=frame.open_time_ms.to_numpy(dtype='int64')
            if len(set(times))!=len(times): raise ValueError('duplicate candle')
            valid=(times%HOUR==0)&(frame.close_time_ms.to_numpy()==times+HOUR-1)
            o,hi,lo,c=[frame[k].to_numpy(dtype='float64') for k in ('open','high','low','close')]
            valid &= np.isfinite(np.column_stack([o,hi,lo,c])).all(axis=1)&(lo>0)&(lo<=np.minimum(o,c))&(hi>=np.maximum(o,c))
            f=frame.loc[valid,['open','high','low','close']].copy()
            f.index=pd.to_datetime(times[valid],unit='ms',utc=True); f=f.reindex(index)
            if kind=='klines':
                tradable=pd.Series(frame.loc[valid,'volume'].to_numpy()>0,index=pd.to_datetime(times[valid],unit='ms',utc=True))
                pieces['tradable']=tradable.reindex(index).fillna(False).astype(float)
            pieces[kind]=f
        combined=pieces['klines'].copy()
        for name in ('open','high','low','close'): combined['mark_'+name]=pieces['markPriceKlines'][name]
        missing=combined.isna().any(axis=1)
        # No missing trade/mark hours allowed in an authoritative funding replay.
        if missing.any(): raise ValueError(f'{symbol}: {int(missing.sum())} unavailable trade/mark hours')
        combined['tradable']=pieces['tradable']
        frames[symbol]=combined
        quality[symbol]=dict(hours=len(combined),missing_hours=int(missing.sum()),funding=pieces['funding_quality'],zero_volume_untradable_hours=int((combined.tradable==0).sum()))
    return frames,funding,quality


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    source=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09')
    inv=Path('outputs/crypto-directional-2026-10-19/kalshi-inventory')
    coverage=json.loads((source/'universe-coverage.json').read_text());markets=json.loads((inv/'markets.json').read_text())['markets']
    fees=json.loads((inv/'fees.json').read_text())['taker_fee_rates']
    cfgs=[dict(leverage=l,sizing=s,tp_atr=tp,direction=d) for l,s,tp,d in itertools.product((1,2),('full','vol20','vol40'),(None,1.,2.),(1,-1))]
    protocol=dict(training=['2021-01-01','2021-12-31'],periods={'external':['2024-01-01','2026-09-30'],'post_training':['2022-01-01','2026-09-30']},
        universe='active Crypto markets returned by authenticated Kalshi inventory; BTC/ETH covered separately; require January2021 funding archive and complete verified trade/mark/funding to end',
        candidates=cfgs,selection='maximum training net P&L per asset with >=3 completed episodes and zero liquidations; also best positive-net frequency-qualified profile if >=2 profitable closes/month',
        capital='10000 USDT per single-asset comparison',
        costs='current authenticated Kalshi taker fee per ticker +4bps modeled spread/slippage primary; stress total15/30bps',
        contract_units='proxy input prices in base-asset units; multiplier=Kalshi contract_size; proxy tick=current Kalshi contract-value tick / contract_size; exclude1000SHIB insufficient2021 history rather than guess conversion',
        caveats=['current-universe survivorship conditioning','one-year2021 training is bull-heavy','prices/funding are Binance proxies, not Kalshi histories',
        'modern fee/contract/tick/margin scenarios do not reconstruct historical Kalshi','only a historical proxy screen; no live submission or account changes'],
        source_manifest_sha256=hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),
        inventory_sha256=hashlib.sha256((inv/'markets.json').read_bytes()).hexdigest(),
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    training=[];selected={};assets={};failures=[];cache={}
    for item in coverage:
        if item['status']!='eligible':continue
        symbol=item['symbol'];ticker=item['ticker'];market=next(m for m in markets if m['ticker']==ticker)
        try:frames,funding,audit=load_asset(source,symbol)
        except Exception as exc:failures.append(dict(symbol=symbol,reason=str(exc)));print('excluded data quality',symbol,str(exc),flush=True);continue
        # Contract-value quotes in Kalshi must not be confused with underlying proxy prices.
        specs={symbol:dict(multiplier=market['contract_size'],price_tick=str(float(market['tick_size'])/float(market['contract_size'])))}
        fee_bps=fees[ticker]*10000;primary=fee_bps+4
        assets[symbol]=frames,funding,specs,fee_bps,primary
        eligible=[]
        for i,cfg in enumerate(cfgs):
            directions={symbol:cfg['direction']};key=symbol,cfg['sizing'],cfg['tp_atr'],cfg['direction']
            if key not in cache:cache[key]=directional_weights(frames,cfg|{'leverage':1},directions)
            stats,*_=run_directional(frames,funding,cache[key]*cfg['leverage'],('2021-01-01','2021-12-31'),cost=primary,fee_bps=fee_bps,contract_specs=specs,leverage_limit=cfg['leverage'])
            stats.pop('closed_episodes');row=dict(symbol=symbol,ticker=ticker,id=i,config=cfg,**stats);training.append(row)
            if stats['round_trips']>=3 and stats['liquidations']==0:eligible.append(row)
        if not eligible:failures.append(dict(symbol=symbol,reason='no active liquidation-free training recipe'));continue
        selected[symbol+'-pnl']=max(eligible,key=lambda r:r['pnl'])
        frequent=[r for r in eligible if r['pnl']>0 and r['profitable_closes_per_month']>=2]
        if frequent:selected[symbol+'-frequent']=max(frequent,key=lambda r:r['pnl'])
        print('trained universe',symbol,'best P&L',selected[symbol+'-pnl']['pnl'],flush=True)
        (out/'training-progress.json').write_text(json.dumps(training,indent=2))
    if not selected:raise ValueError('no eligible proxy assets')
    winner=max((k for k in selected if k.endswith('-pnl')),key=lambda k:selected[k]['pnl'])
    (out/'selection.json').write_text(json.dumps(dict(selected=selected,global_training_winner=winner,failures=failures),indent=2))
    results=[];done={}
    for label,row in selected.items():
        symbol=row['symbol'];cfg=row['config'];frames,funding,specs,fee_bps,primary=assets[symbol]
        weights=cache[symbol,cfg['sizing'],cfg['tp_atr'],cfg['direction']]*cfg['leverage']
        for period,dates in protocol['periods'].items():
            for cost in (primary,15,30):
                key=symbol,row['id'],period,cost
                if key not in done:done[key]=run_directional(frames,funding,weights,dates,cost=cost,fee_bps=fee_bps,contract_specs=specs,leverage_limit=cfg['leverage'])
                stats,path,_,_=done[key];stats={k:v for k,v in stats.items() if k!='closed_episodes'}
                results.append(dict(strategy=label,symbol=symbol,ticker=row['ticker'],config=cfg,period=period,cost_bps=cost,**stats))
                if cost==primary:path.to_csv(out/f'{label}-{period}.csv',index_label='timestamp')
        print('evaluated universe',label,flush=True)
    (out/'summary.json').write_text(json.dumps(dict(results=results,failures=failures,global_training_winner=winner),indent=2))
    lines=['# Kalshi-universe multi-year proxy screen','',f'Global training P&L winner: {winner}. Native Kalshi markets are confirmed by authenticated read-only API; returns use Binance historical proxies.','',
           '| Strategy | Period | Cost/side | Direction | Leverage | Sizing | TP ATR | P&L | DD | Profitable closes/month |',
           '|---|---|---:|---|---:|---|---:|---:|---:|---:|']
    for r in results:
        cfg=r['config'];lines.append(f"| {r['strategy']} | {r['period']} | {r['cost_bps']}bps | {'long' if cfg['direction']>0 else 'short'} | {cfg['leverage']} | {cfg['sizing']} | {cfg['tp_atr']} | {r['pnl']:.2f} | {r['max_drawdown']:.2%} | {r['profitable_closes_per_month']:.2f} |")
    lines+=['','Data/training exclusions: '+json.dumps(failures),'','## Limits','']+protocol['caveats'];(out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    snap=out/'source';snap.mkdir();shutil.copy2(__file__,snap/Path(__file__).name)
    print(json.dumps(dict(global_training_winner=winner,failures=failures),indent=2))

if __name__=='__main__':main()
