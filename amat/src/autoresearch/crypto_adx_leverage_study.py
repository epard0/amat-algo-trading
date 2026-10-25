"""Registered ADX1x/2x/current-max scenario with corrected empirical depth costs."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import numpy as np
import pandas as pd
from autoresearch.crypto_adx_leverage import replay
from autoresearch.crypto_adx_switch import market_context
from autoresearch.crypto_volatility_switch import regime_plan
from autoresearch.crypto_partial_exits import exit_plan,policies
from autoresearch.crypto_walkforward import fresh_plan
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_universe_study import load_asset
from autoresearch.crypto_fast_breakout import prepare

OUT=Path('outputs/crypto-adx-leverage-costs-2026-10-20-v2')
PERIODS=dict(validation=('2025-07-01','2025-12-31'),test=('2026-01-01','2026-09-30'),continuous=('2025-07-01','2026-09-30'))
SIZES=(100,500,1000,2500,5000,10000,25000,50000,100000,200000)


def build_curves(rows):
    curves={}
    for ticker in ('KXDOGEPERP','KXSOLPERP'):
        curves[ticker]={}
        for side in ('bids','asks'):
            table=[]
            for notional in SIZES:
                samples=[];covered=0
                for row in rows:
                    levels={s:sorted([(float(p),float(q)) for p,q in row['quotes'][ticker][s] if float(q)>0],reverse=s=='bids') for s in ('bids','asks')}
                    mid=(levels['bids'][0][0]+levels['asks'][0][0])/2
                    quantity=notional/mid;remaining=quantity;value=0
                    for p,q in levels[side]:
                        take=min(q,remaining);value+=take*p;remaining-=take
                        if remaining<1e-10:break
                    if remaining>1e-8:continue
                    covered+=1;vwap=value/quantity
                    samples.append(max(0.,(vwap/mid-1)*10000*(1 if side=='asks' else -1)))
                table.append(dict(notional=notional,full_coverage_fraction=covered/len(rows),median_bps=float(np.median(samples)) if samples else None,p95_bps=float(np.quantile(samples,.95)) if samples else None))
            curves[ticker][side]=table
    return curves


def full_tp_reasons(context,weights,tp):
    p=context['prices'];action=context['action_index'];hourly=pd.Series(weights,index=context['index']).reindex(action).to_numpy()
    reason=np.full(len(p),6,dtype=np.int8);ref=atr=None;previous=0
    for i,w in enumerate(hourly):
        if not np.isfinite(w):continue
        if w>0 and previous==0:ref=p.close.iloc[i];atr=context['atr'][i]
        if w==0 and previous>0 and ref is not None:
            if p.close.iloc[i]<context['channel'][i]:reason[i]=4
            elif p.close.iloc[i]-ref>=tp*atr:reason[i]=7
            else:reason[i]=5
        previous=w
    return pd.Series(reason,index=action).reindex(context['index'],method='ffill').fillna(0).to_numpy(dtype=np.int8)


def main():
    out=OUT
    if (out/'protocol.json').exists():raise ValueError('immutable run already exists')
    quote=Path('outputs/crypto-doge-sol-walkforward-2026-10-19/quote-repair-backups/20261020T145523Z/forward-observations.jsonl')
    root=Path('data/research/doge-sol-minute-2024-2026-09');perps=Path('../kalshi-perps')
    sources=[Path(__file__)]+[Path(__file__).with_name(n) for n in ('crypto_adx_leverage.py','crypto_adx_switch.py','crypto_volatility_switch.py','crypto_partial_exits.py','crypto_walkforward.py','crypto_doge_sol.py','crypto_directional.py','crypto_fast_breakout.py','crypto_short_window_study.py','crypto_universe_study.py')]+[perps/'src/kalshi_perps'/n for n in ('paper.py','leverage.py','domain.py')]
    protocol=dict(capital=10000,periods=PERIODS,leverage=['1','2','max'],cost_cases=['median','p95','double_p95_slippage'],frozen_rule='ADX14>=20 ladder otherwise original, latched atSOLentry. DOGE0.5ATRfull target; allocation25/75 among active legs.',
        primary='Continuous July2025-September2026 median empirical execution costs, compare all3 leverage modes; 2026test secondary; no parameter selection.',
        sizing='Pooled collateral like default maximum-leverage overlay. Active-leg25/75 allocations renormalized over active signals in ALL modes,2% budget reserve. Full positions size on entry/exit changes and dailyUTC rebalance; partials reduce actual contracts proportionally and are never bought back. Daily replay approximates continuously polled forward sizing.',
        costs='Fit median and95th-percentile one-way midpoint slippage by asset,side and order notional from675 corrected native books; add4bps fee. Next larger measured size bucket, >=95% depth coverage required, no unsupported extrapolation. Double-p95 stress doubles slippage only.',
        margin='Apply current published side/size maintenance leverage and1.3 initial-margin multipliers in every mode. Max targets98% available initial margin. Full pooled liquidation at modeled adverse execution +50bps liquidation fee; halt after liquidation, no capital reset. Minute lows assumed simultaneous across assets, conservative margin path.',
        funding='Historical Binance perpetual funding at actual timestamps, net long/short convention unchanged; no native historical funding substituted.',
        evidence='Retrospective sensitivity scenario, not native historical max-leverage backtest. No historical Kalshi margin curves retrieved. Current curves and October20 quote sample applied to2025/2026 prices, so execution calibration uses later information. No native historical orderbook path, latency or actual fills.',
        timestamps='Signals use completed4h bars; orders next minute open+6seconds. Start flat; skip carried signal until next entry. Historical funding before orders. Integer native contract lots and ticks.',
        source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},quotes_sha256=hashlib.sha256(quote.read_bytes()).hexdigest(),venue_snapshot_sha256=hashlib.sha256((out/'venue-snapshot.json').read_bytes()).hexdigest(),minute_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2));(out/'source').mkdir()
    for p in sources:shutil.copy2(p,out/'source'/p.name)
    sys.path.insert(0,str((perps/'src').resolve()));from kalshi_perps.domain import registry
    instruments=registry(json.loads((perps/'configs/instruments.json').read_text()));instruments={s:instruments[s] for s in ('KXDOGEPERP','KXSOLPERP')}
    venue=json.loads((out/'venue-snapshot.json').read_text());risk=venue['risk_parameters'];meta={s:dict(long=m['long_leverage_estimates'],short=m['short_leverage_estimates'],initial_margin_multiplier=risk['initial_margin_multiplier'][s]) for s,m in venue['markets'].items()}
    rows=[json.loads(x) for x in quote.read_text().splitlines()];curves=build_curves(rows);(out/'cost-curves.json').write_text(json.dumps(curves,indent=2))
    frames,audit=load_minutes(root);(out/'data-audit.json').write_text(json.dumps(audit,indent=2));assets={};plans={}
    roots=dict(DOGEUSDT=Path('data/research/kalshi-universe-proxies-2021-2026-09'),SOLUSDT=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09'))
    for symbol,frame in frames.items():
        s='KX'+symbol.removesuffix('USDT')+'PERP';_,funding,_=load_asset(roots[symbol],symbol);assets[s]=prepare(frame,funding[symbol])
        context=market_context(frame)
        if symbol=='DOGEUSDT':
            w,why=exit_plan(frame,policies()[0],.5);why=full_tp_reasons(context,w,.5)
        else:
            w,why,_,_=regime_plan(context,dict(family='switch',feature='adx14',threshold=20,ladder_when='high'))
            full=full_tp_reasons(context,w,2.);why=why.copy();why[why==6]=full[why==6]
        plans[s]=(w,why)
    results=[]
    for period,dates in PERIODS.items():
        active_plans={s:fresh_plan(w,why,assets[s]['index'],dates) for s,(w,why) in plans.items()}
        for case in ('median','p95','double_p95_slippage'):
            for lev in ('1','2','max'):
                stats,path,ledger,reductions,closed=replay(assets,active_plans,instruments,meta,curves,dates,lev,case)
                results.append(dict(period=period,cost_case=case,leverage=lev,**stats))
                stem=f'{period}-{case}-{lev}'
                path.resample('1h').last().to_csv(out/(stem+'.csv'),index_label='timestamp')
                (out/(stem+'-ledger.json')).write_text(json.dumps(ledger,indent=2));(out/(stem+'-reductions.json')).write_text(json.dumps(reductions,indent=2));(out/(stem+'-episodes.json')).write_text(json.dumps(closed,indent=2))
                print(json.dumps(results[-1]),flush=True)
    (out/'summary.json').write_text(json.dumps(dict(results=results,protocol=protocol),indent=2))
    for p in sources:assert hashlib.sha256(p.read_bytes()).hexdigest()==protocol['source_sha256'][str(p)]

if __name__=='__main__':main()
