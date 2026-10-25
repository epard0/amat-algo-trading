"""Registered broad ADX/falling-EMA short gate screen plus minute DOGE/SOL confirmation."""
import json,hashlib,shutil,sys
from pathlib import Path
from itertools import product
import numpy as np
import pandas as pd
from autoresearch.crypto_trend_short import context,short_from_context,combine
from autoresearch.crypto_trend_replay import replay_trend
from autoresearch.crypto_breakout_windows import window_plan
from autoresearch.crypto_signed_adx import fresh_signed_plan
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_fast_breakout import prepare
OUT=Path('outputs/crypto-trend-short-supplement-2026-10-22')
DATA=Path('data/research/trend-supplement-prepared-2026-10-22');NATIVE=Path('data/research/trend-supplement-native-2026-10-22')
TRAIN=('2024-01-01','2025-06-30');PERIODS=dict(validation=('2025-07-01','2025-12-31'),test=('2026-01-01','2026-09-30'),continuous=('2025-07-01','2026-09-30'))
GATES=list(product((25,30,35),(50,100,200)));LEVS=('1','2','max')


def prep_hourly(f,raw):
    ms=f.index.as_unit('ms').asi8;events={}
    for _,(when,rate) in raw.items():
        pos=int(np.searchsorted(ms,when,side='right')-1)
        if 0<=pos<len(ms) and ms[pos]<=when<ms[pos]+3600000:
            if when-ms[pos]>=6000:raise ValueError('funding follows execution offset')
            events[pos]=(when,rate)
    return dict(index=f.index,ms=ms,a={c:f[c].to_numpy() for c in f},funding=events,blackout=np.zeros(len(f),bool),unavailable=np.zeros(len(f),bool))


def stats_entries(stats,ledger,episodes):
    counts={};directions={};entries=[]
    for row in ledger:
        s=row['symbol'];old=counts.get(s,0.);delta=float(row['count'])
        if old==0 and delta:
            direction=1 if delta>0 else -1;entries.append(direction);directions[s,row['timestamp_ms']]=direction
        counts[s]=old+delta
    stats.update(entries=len(entries),short_entries=entries.count(-1),long_entries=entries.count(1),short_net_pnl=sum(e['net_pnl'] for e in episodes if directions[e['symbol'],e['entry_ms']]==-1))
    return stats


def eligible(r):
    return r.get('supported',False) and r['pnl']>0 and r['short_entries']>=3 and r['entries']>=6 and r['short_net_pnl']>0 and not r['guard_triggered'] and not r['liquidations'] and not r['margin_rejections']


def main():
    OUT.mkdir(exist_ok=False);(OUT/'sources').mkdir()
    paths=[Path(__file__)]+[Path(__file__).with_name(n) for n in ['crypto_trend_short.py','crypto_trend_replay.py','crypto_signed_adx.py','crypto_signed_replay.py','crypto_breakout_windows.py','crypto_adx_switch.py','crypto_volatility_switch.py','crypto_doge_sol.py','crypto_partial_exits.py']]+[Path('../kalshi-perps/src/kalshi_perps')/n for n in ['paper.py','leverage.py','domain.py']]
    hashes={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    for p in paths:shutil.copy2(p,OUT/'sources'/p.name)
    audit=json.loads((DATA/'audit.json').read_text());assets=[s for s in sorted(audit) if not audit[s].get('excluded')]
    protocol=dict(assets=assets,training=TRAIN,periods=PERIODS,gates=GATES,leverages=LEVS,capital=10000,
        gate='Short entries only: completed4h ADX14>=25/30/35, close<EMA50/100/200, EMA below its value3bars ago. Gate latched at original short onset; rejected onset cannot turn into a late entry. Exits remain original default. Long entries never gated.',
        engine='20day breakout/10day channel,72h cap,DOGE0.5ATR full target; all other assets use unchanged SOL ADX14>=20 entry-locked ladder/original2ATR exit template. No per-asset exit-template optimization. Signed funding,side-specific native margin/integer contracts,35%initial-equity order-halt preserved.',
        selection='Per asset: 27 median-cost training candidates. Highest training net P&L among positive total and short-episode net P&L,>=6total and>=3short entries,zero guard/liquidation/rejection and supported costs. Freeze all choices and top3assets by training P&L before any later data. If no eligible candidate, retain highest supported P&L as explicitly ineligible diagnostic.',
        comparisons='Same-leverage long-only and ungated both-direction controls; selected gated both under median/p95/doubled-p95slippage; isolated selected gated shorts; current max long-only diagnostic. Full universe retained,not ranked by later returns.',
        costs='Fresh30native depth100book snapshots per asset; median/p95side/size VWAP slippage,95%complete depth required,no extrapolation; verified4bpstaker fee. Candidate requiring >30bps total entry/strategy exit cost is unsupported rather than assumed filled. Terminal close and forced liquidation costed even outside strategy cap. Cost sample is small and current,not historical fills.',
        sampling='Broad screen hourly execution/mark highs and lows with4h signals. Hourly guard observations miss intrahour crossings; adverse intrahour liquidation conservative. Separate DOGE/SOL minute confirmation uses identical hourly-derived causal signal paths and actual minute prices/funding,25/75activeallocation.',
        caveats='Current-universe survivorship. Historical prices/funding Binance proxies with2021+warmup; current native fees/margin/books applied historically. Later periods already inspected; chronological retrospective tests,not untouched OOS. No source/account/live deployment changes.',source_sha256=hashes)
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2));shutil.copy2(NATIVE/'cost-curves.json',OUT/'cost-curves.json');shutil.copy2(NATIVE/'venue.json',OUT/'venue.json')
    sys.path.insert(0,str(Path('../kalshi-perps/src').resolve()));from kalshi_perps.domain import registry
    venue=json.loads((NATIVE/'venue.json').read_text());instruments=registry(dict(markets=list(venue['markets'].values())));curves=json.loads((NATIVE/'cost-curves.json').read_text());risk=venue['risk_parameters']
    metadata={t:dict(long=m['long_leverage_estimates'],short=m['short_leverage_estimates'],initial_margin_multiplier=risk['initial_margin_multiplier'][t]) for t,m in venue['markets'].items()}
    assert all(abs(venue['fees']['taker_fee_rates'][t]-.0004)<1e-12 for t in metadata)
    prepared={};frame_map={};plan_cache={};training=[];selected={};replays=0
    def run(asset_data,plans,lev,dates,case='median',stem=None):
        nonlocal replays
        plan={t:fresh_signed_plan(*v,asset_data[t]['index'],dates) for t,v in plans.items()}
        try:r,path,ledger,reductions,episodes=replay_trend(asset_data,plan,{t:instruments[t] for t in plan},{t:metadata[t] for t in plan},curves,dates,lev,case)
        except ValueError as exc:
            if not any(s in str(exc) for s in ['outside observed depth calibration','outside execution cost cap','published curve','published leverage curve coverage']):raise
            return dict(supported=False,error=str(exc))
        r=stats_entries(r,ledger,episodes);r.update(supported=True);replays+=1
        if stem:
            path.resample('1h').last().to_csv(OUT/(stem+'.csv'),index_label='timestamp')
            (OUT/(stem+'-episodes.json')).write_text(json.dumps(episodes,indent=2))
        return r
    for asset in assets:
        t='KX'+asset+'PERP';frame=pd.read_pickle(DATA/(asset+'.pkl'));raw=json.loads((DATA/(asset+'-funding.json')).read_text());prepared[t]=prep_hourly(frame,raw);frame_map[t]=frame;template='DOGE' if asset=='DOGE' else 'SOL'
        long=window_plan(frame,20,template);ctx,prices=context(frame,template);short=short_from_context(ctx,prices,template)
        plan_cache[t,'long']=long;plan_cache[t,'ungated']=combine(long,short)
        for adx,ema in GATES:
            short=short_from_context(ctx,prices,template,adx,ema);plan_cache[t,adx,ema,'short']=short[:2];plan_cache[t,adx,ema]=combine(long,short)
            for lev in LEVS:
                r=run({t:prepared[t]},{t:plan_cache[t,adx,ema]},lev,TRAIN);r.update(asset=asset,adx=adx,ema=ema,leverage=lev);training.append(r)
        rows=[r for r in training if r['asset']==asset];valid=[r for r in rows if eligible(r)];supported=[r for r in rows if r['supported']]
        winner=max(valid or supported,key=lambda r:(r['pnl'],r['max_drawdown']),default=None)
        selected[asset]=dict(config={k:winner[k] for k in ['adx','ema','leverage']} if winner else None,training=winner,training_eligible=bool(valid))
        print('TRAIN ASSET',asset,json.dumps(selected[asset]),flush=True)
    pair_training=[];pair=dict(config=None,training=None,training_eligible=False)
    top=sorted([a for a in assets if selected[a]['training_eligible']],key=lambda a:selected[a]['training']['pnl'],reverse=True)[:3]
    (OUT/'training.json').write_text(json.dumps(dict(assets=training,pair=pair_training),indent=2));(OUT/'selection.json').write_text(json.dumps(dict(assets=selected,pair=pair,top_training_assets=top),indent=2));print('LOCKED',top,pair['config'],flush=True)
    results=[]
    for asset in assets:
        selection=selected[asset]
        if not selection['config']:continue
        cfg=selection['config'];t='KX'+asset+'PERP';adx,ema,lev=cfg['adx'],cfg['ema'],cfg['leverage']
        cases=[('gated',lev,'median'),('gated',lev,'p95'),('gated',lev,'double_p95_slippage'),('long',lev,'median'),('ungated',lev,'median'),('short',lev,'median')]
        if lev!='max':cases.append(('long_max','max','median'))
        for variant,leverage,cost in cases:
            v=plan_cache[t,adx,ema] if variant=='gated' else plan_cache[t,adx,ema,'short'] if variant=='short' else plan_cache[t,'ungated' if variant=='ungated' else 'long']
            for period,dates in PERIODS.items():
                r=run({t:prepared[t]},{t:v},leverage,dates,cost,stem=f'{asset}-{variant}-{period}-{cost}' if cost=='median' else None);r.update(asset=asset,variant=variant,period=period,cost_case=cost,leverage=leverage,adx=adx,ema=ema);results.append(r)
        print('LATER ASSET',asset,flush=True)
    pair_results=[]
    if pair['config']:
        cfg=pair['config'];lev=cfg['leverage'];adx,ema=cfg['adx'],cfg['ema']
        for variant,leverage in [('gated',lev),('long',lev),('ungated',lev),('long_max','max')]:
            for cost in ['median','p95','double_p95_slippage']:
                for period,dates in PERIODS.items():
                    plans={t:minute_plans[t,adx,ema] if variant=='gated' else minute_plans[t,'ungated' if variant=='ungated' else 'long'] for t in minute_data}
                    r=run(minute_data,plans,leverage,dates,cost,f'PAIR-{variant}-{period}-{cost}');r.update(variant=variant,leverage=leverage,period=period,cost_case=cost,adx=adx,ema=ema);pair_results.append(r)
                    print('LATER PAIR',variant,period,cost,r.get('pnl'),flush=True)
    for name,h in hashes.items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==h
    (OUT/'summary.json').write_text(json.dumps(dict(selection=dict(assets=selected,pair=pair,top_training_assets=top),training=training,pair_training=pair_training,results=results,pair_results=pair_results,replays=replays),indent=2));print('DONE',replays,flush=True)

if __name__=='__main__':main()
