"""Registered default ADX direction extension; train first, retain frozen controls."""
from pathlib import Path
from itertools import product
import hashlib,json,shutil,sys
import numpy as np
import pandas as pd
from autoresearch.crypto_signed_adx import signed_plan,fresh_signed_plan
from autoresearch.crypto_signed_replay import replay_signed
from autoresearch.crypto_guarded_replay import replay_guarded
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_universe_study import load_asset
from autoresearch.crypto_fast_breakout import prepare

OUT=Path('outputs/crypto-signed-adx-2026-10-22-v2')
PARENT=Path('outputs/crypto-adx-guard-window-leverage-2026-10-20-v2')
TRAIN=('2024-01-01','2025-06-30')
PERIODS=dict(validation=('2025-07-01','2025-12-31'),test=('2026-01-01','2026-09-30'),continuous=('2025-07-01','2026-09-30'))
SYMBOLS=('KXDOGEPERP','KXSOLPERP')


def eligible(r):
    return r.get('capacity_supported',False) and r['pnl']>0 and r['entries']>=6 and not r['guard_triggered'] and not r['liquidations'] and not r['margin_rejections']


def main():
    OUT.mkdir(exist_ok=False)
    names=['crypto_signed_adx.py','crypto_signed_replay.py','crypto_signed_study.py','crypto_guarded_replay.py','crypto_breakout_windows.py','crypto_adx_switch.py','crypto_volatility_switch.py','crypto_doge_sol.py','crypto_partial_exits.py','crypto_adx_leverage_study.py','crypto_fast_breakout.py','crypto_short_window_study.py','crypto_universe_study.py']
    source=[Path(__file__).with_name(n) for n in names]+[Path('../kalshi-perps/src/kalshi_perps')/n for n in ('paper.py','leverage.py','domain.py')]
    (OUT/'sources').mkdir();hashes={}
    for p in source:shutil.copy2(p,OUT/'sources'/p.name);hashes[str(p.resolve())]=hashlib.sha256(p.read_bytes()).hexdigest()
    protocol=dict(training=TRAIN,periods=PERIODS,direction_grid=list(product(('long','short','both'),repeat=2)),leverages=['1','2','max'],capital=10000,
        frozen='Default four-hour 20-day entry/10-day channel; DOGE0.5ATR full TP; SOL ADX14>=20 entry-locked partial ladder otherwise2ATR original;72h cap;25/75 allocation normalized among active;2% margin reserve; no buyback after partial.',
        signed='Shorts use20day low breakdown, upper10day channel, mirrored favorable-price ATR targets and trailing minimum; actual observed prices, symmetric ADX. One net position/asset; ignore opposite onsets while held; no samebar reversal. Fresh accounts skip carried positions of either sign.',
        accounting='Same native signed PaperBroker fill/equity/margin helpers; actual historical funding payment=-signed_contracts*mark*rate. Shorts receive positive funding/pay negative. Bid costs to open shorts, ask costs to cover. Maintenance checked at minute lows for longs/highs for shorts; pooled simultaneous adverse marks conservative. Exact paper35% initial loss order-halt preserved; exits also halt, no forced flatten until liquidation or research terminal.',
        selection='27 median-cost training configurations; maximum net P&L among positive,>=6 entries,zero guard/liquidation/rejection,supported cost depth. Freeze both overall winner and best non-long-only extension before later replay. No eligible extension means no promotion; retain raw training leader diagnostic only.',
        later='Evaluate unique current LL/max, BB/max, overall winner and best eligible extension (or diagnostic) under median,p95,double-p95-slippage. Four configs maximum. Separate fresh validation/test/continuous accounts; preserve same splits as prior study.',
        limitations='Historical Binance trade/mark/funding proxies and Oct20 Kalshi native margin/cost calibration. Historical periods already inspected: chronological retrospective comparison, not untouched OOS. No new backtest capability enables paper/live deployment; no signing credentials used.',source_sha256=hashes)
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2))
    for n in ['cost-curves.json','venue-snapshot.json']:shutil.copy2(PARENT/n,OUT/n)
    sys.path.insert(0,str(Path('../kalshi-perps/src').resolve()));from kalshi_perps.domain import registry
    instruments=registry(json.loads(Path('../kalshi-perps/configs/instruments.json').read_text()));instruments={s:instruments[s] for s in SYMBOLS}
    venue=json.loads((OUT/'venue-snapshot.json').read_text());risk=venue['risk_parameters'];metadata={s:dict(long=venue['markets'][s]['long_leverage_estimates'],short=venue['markets'][s]['short_leverage_estimates'],initial_margin_multiplier=risk['initial_margin_multiplier'][s]) for s in SYMBOLS};curves=json.loads((OUT/'cost-curves.json').read_text())
    frames,audit=load_minutes(Path('data/research/doge-sol-minute-2024-2026-09'));(OUT/'data-audit.json').write_text(json.dumps(audit,indent=2));assets={};plans={}
    roots={'DOGEUSDT':Path('data/research/kalshi-universe-proxies-2021-2026-09'),'SOLUSDT':Path('data/research/kalshi-universe-gap-supplement-2021-2026-09')}
    for name,frame in frames.items():
        s='KX'+name.removesuffix('USDT')+'PERP';_,funding,_=load_asset(roots[name],name);assets[s]=prepare(frame,funding[name])
        for mode in ('long','short','both'):plans[s,mode]=signed_plan(frame,name.removesuffix('USDT'),mode)
    replays=0
    def run(config,dates,case,stem=None,control=False):
        nonlocal replays
        active={s:fresh_signed_plan(*plans[s,config[k]],assets[s]['index'],dates) for s,k in zip(SYMBOLS,('doge','sol'))}
        try:
            fn=replay_guarded if control else replay_signed
            stats,path,ledger,reductions,episodes=fn(assets,active,instruments,metadata,curves,dates,config['leverage'],case)
        except ValueError as exc:
            if 'outside observed depth calibration' not in str(exc):raise
            return dict(**config,cost_case=case,capacity_supported=False,error=str(exc))
        counts={s:0. for s in SYMBOLS};entries=[]
        for r in ledger:
            s=r['symbol'];delta=float(r['count'])
            if counts[s]==0 and delta!=0:entries.append(dict(symbol=s,direction='long' if delta>0 else 'short'))
            counts[s]+=delta
        stats.update(**config,cost_case=case,capacity_supported=True,entries=len(entries),long_entries=sum(r['direction']=='long' for r in entries),short_entries=sum(r['direction']=='short' for r in entries))
        replays+=1
        if stem:
            path.resample('1h').last().to_csv(OUT/(stem+'.csv'),index_label='timestamp')
            for label,rows in [('ledger',ledger),('reductions',reductions),('episodes',episodes)]: (OUT/(stem+'-'+label+'.json')).write_text(json.dumps(rows,indent=2))
        return stats
    training=[]
    for doge,sol,lev in product(('long','short','both'),('long','short','both'),('1','2','max')):
        cfg=dict(doge=doge,sol=sol,leverage=lev);r=run(cfg,TRAIN,'median');training.append(r)
        print('TRAIN',json.dumps({k:r.get(k) for k in ('doge','sol','leverage','pnl','max_drawdown','guard_triggered','liquidations','capacity_supported')}),flush=True)
    rank=lambda r:(r['pnl'],r['max_drawdown'])
    overall=max([r for r in training if eligible(r)],key=rank,default=None)
    extensions=[r for r in training if (r['doge'],r['sol'])!=('long','long')]
    extension=max([r for r in extensions if eligible(r)],key=rank,default=None)
    diagnostic=max([r for r in extensions if r.get('capacity_supported')],key=rank,default=None)
    selection=dict(overall=overall,eligible_extension=extension,unconstrained_extension=diagnostic,extension_promotion_eligible=extension is not None)
    (OUT/'training.json').write_text(json.dumps(training,indent=2));(OUT/'selection.json').write_text(json.dumps(selection,indent=2));print('LOCKED',json.dumps(selection),flush=True)
    configs=[dict(doge='long',sol='long',leverage='max'),dict(doge='both',sol='both',leverage='max')]
    for r in (overall,extension or diagnostic):
        if r:
            cfg={k:r[k] for k in ('doge','sol','leverage')}
            if cfg not in configs:configs.append(cfg)
    results=[]
    for cfg in configs:
        tag='-'.join(cfg.values())
        for period,dates in PERIODS.items():
            for case in ('median','p95','double_p95_slippage'):
                r=run(cfg,dates,case,f'{period}-{case}-{tag}');r['period']=period;results.append(r)
                print('LATER',period,case,tag,r.get('pnl'),flush=True)
    parity=[]
    for lev in ('1','2','max'):
        cfg=dict(doge='long',sol='long',leverage=lev)
        before=run(cfg,PERIODS['continuous'],'median',control=True)
        after=next((r for r in results if r['doge']=='long' and r['sol']=='long' and r['leverage']==lev and r['period']=='continuous' and r['cost_case']=='median'),None) or run(cfg,PERIODS['continuous'],'median')
        for key in before:
            assert before[key]==after[key],(lev,key,before[key],after[key])
        parity.append(dict(leverage=lev,exact=True))
    for name,expected in hashes.items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected
    (OUT/'summary.json').write_text(json.dumps(dict(selection=selection,training=training,results=results,parity=parity,replays=replays),indent=2));print('DONE',replays,flush=True)

if __name__=='__main__':main()
