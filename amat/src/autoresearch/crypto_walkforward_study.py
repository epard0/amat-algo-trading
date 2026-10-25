"""Three frozen DOGE/SOL exit candidates, rolling training-only sizing selection."""
import hashlib
import json
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
from autoresearch.crypto_adx_switch import market_context
from autoresearch.crypto_volatility_switch import regime_plan
from autoresearch.crypto_partial_exits import exit_plan,policies
from autoresearch.crypto_walkforward import SIZE_GRID,sizing_path,fresh_plan,replay_sized_plan,reduction_metrics
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_universe_study import load_asset
from autoresearch.crypto_fast_breakout import prepare

OUT=Path('outputs/crypto-doge-sol-walkforward-2026-10-19')
CANDIDATES=[dict(id=0,name='Original',rule=dict(family='original')),dict(id=1,name='SOL ladder',rule=dict(family='ladder')),dict(id=2,name='ADX14 >=20 ladder',rule=dict(family='switch',feature='adx14',threshold=20,ladder_when='high'))]


def folds():
    return [dict(train=((t-pd.DateOffset(years=1)).strftime('%Y-%m-%d'),(t-pd.Timedelta(days=1)).strftime('%Y-%m-%d')),test=(t.strftime('%Y-%m-%d'),(t+pd.DateOffset(months=3)-pd.Timedelta(days=1)).strftime('%Y-%m-%d'))) for t in pd.date_range('2025-01-01','2026-07-01',freq='QS',tz='UTC')]


def select_size(rows):
    eligible=[r for r in rows if r['pnl']>0 and r['max_drawdown']>=-.25 and r['liquidations']==0 and r['margin_rejections']==0 and r['natural_closes']>=6]
    if eligible:return dict(size=max(eligible,key=lambda r:r['pnl'])['size'],eligible=True)
    return dict(size='fixed-0.5',eligible=False)


def robust(stats):
    return stats['net_pnl_15']>0 and stats['net_pnl_30']>0 and stats['positive_quarters_15']>=4 and stats['worst_quarter_return_15']>-.15 and stats['max_drawdown_15']>-.30 and stats['liquidations']==0 and stats['margin_rejections']==0 and stats['all_training_eligible']


def main():
    out=OUT;out.mkdir(parents=True,exist_ok=False)
    root=Path('data/research/doge-sol-minute-2024-2026-09')
    inv=Path('outputs/crypto-directional-2026-10-19/kalshi-inventory/markets.json')
    sources=[Path(__file__)]+[Path(__file__).with_name(n) for n in ('crypto_walkforward.py','crypto_adx_switch.py','crypto_volatility_switch.py','crypto_partial_exits.py','crypto_fast_breakout.py','crypto_short_window_study.py','crypto_universe_study.py','crypto_doge_sol.py','crypto_directional.py','perpetual_sim.py')]
    protocol=dict(candidates=CANDIDATES,folds=folds(),sizing_grid=SIZE_GRID,costs=[8,15,30],selection_cost_bps=15,
        selection='Maximum trailing12month net P&L at15bps among sizes with positive P&L, daily DD<=25%, no liquidation/margin rejection, >=6 natural closes. If none, fixed0.5 fallback and training gate fails.',
        robust_gate='Positive compounded P&L at15 and30bps; >=4/7 profitable quarters at15bps; worst quarter loss<15%; combined minute-close DD<30%; zero liquidations/rejections across all costs; every fold training gate passed.',
        ranking='Among robust candidates, maximum compounded15bps net P&L; tie smaller15bps drawdown. If none pass, no candidate promoted to paper trading; fresh quote/shadow observation only.',
        risk='Fixed per-sleeve leverage0.5/1/1.5/2 or causal daily30day hourly log-return volatility targeting20/30/40% annualized, clipped0.25..2. No allocation or exit optimization.',
        execution='25/75 DOGE/SOL independent sleeves within each quarter; rebalance collateral at each quarter boundary after costed flatten. Each test starts flat, skipping any preexisting carried signal. Train/test use same fresh-entry convention. No buyback after partial sale. No withdrawals.',
        validation='Existing 2024-2026minute/funding proxies; all dates and candidates previously examined. Retrospective robustness diagnostic, not fresh confirmation.',
        forward_gate='At least90calendar days and20 natural completed portfolio episodes with positive net P&L, no liquidation/accounting mismatch, DD<30%, >=95% submitted quantity filled within60s, median observed one-way execution cost<=15bps, p95<=30bps; native funding reconciliation complete. Withdrawals remain disabled until gate passes.',
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},minute_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),inventory_sha256=hashlib.sha256(inv.read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2));(out/'source').mkdir()
    for p in sources:shutil.copy2(p,out/'source'/p.name)
    frames,audit=load_minutes(root);(out/'data-audit.json').write_text(json.dumps(audit,indent=2))
    roots=dict(DOGEUSDT=Path('data/research/kalshi-universe-proxies-2021-2026-09'),SOLUSDT=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09'))
    markets=json.loads(inv.read_text())['markets'];assets={};sizes={}
    for s,frame in frames.items():
        _,funding,_=load_asset(roots[s],s);m=next(m for m in markets if m['ticker']=='KX'+s.removesuffix('USDT')+'PERP')
        assets[s]=(prepare(frame,funding[s]),dict(multiplier=m['contract_size'],price_tick=str(float(m['tick_size'])/float(m['contract_size']))))
        for cfg in SIZE_GRID:sizes[s,cfg['name']]=sizing_path(frame,cfg)
    context=market_context(frames['SOLUSDT']);doge=exit_plan(frames['DOGEUSDT'],policies()[0],.5)
    plans={c['id']:regime_plan(context,c['rule'])[:2] for c in CANDIDATES}
    cache={}
    def replay(cid,size,dates,cost,capital=10000,full=False):
        sleeves=[]
        for s,allocation in [('DOGEUSDT',.25),('SOLUSDT',.75)]:
            key=(s,0 if s=='DOGEUSDT' else cid,size,tuple(dates),cost,capital,full)
            if key not in cache:
                w,why=doge if s=='DOGEUSDT' else plans[cid]
                w,why=fresh_plan(w,why,frames[s].index,dates)
                data,spec=assets[s]
                cache[key]=replay_sized_plan(data,w,why,dates,capital=capital*allocation,leverage=2,symbol=s,specs=spec,cost=cost,sizing=sizes[s,size],capture=full,full_path=full)
            sleeves.append(cache[key])
        end=int((pd.Timestamp(dates[1],tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)-1
        stats,path=reduction_metrics(sleeves,end,capital=capital)
        return stats,path,sleeves
    selections=[];training=[]
    # Lock all fold sizing choices before any evaluation replay.
    for fold,f in enumerate(folds()):
        for c in CANDIDATES:
            rows=[]
            for cfg in SIZE_GRID:
                stats,_,_=replay(c['id'],cfg['name'],f['train'],15)
                rows.append(dict(candidate=c['id'],fold=fold,size=cfg['name'],**stats))
            chosen=select_size(rows);training.extend(rows)
            selections.append(dict(candidate=c['id'],fold=fold,**f,**chosen))
            print('trained',fold,c['name'],chosen,flush=True)
    (out/'training.json').write_text(json.dumps(training,indent=2));(out/'selection.json').write_text(json.dumps(selections,indent=2));cache.clear()
    results=[];aggregate=[]
    for c in CANDIDATES:
        combined={};candidate_results=[]
        for cost in (8,15,30):
            capital=10000.;paths=[]
            for chosen in [r for r in selections if r['candidate']==c['id']]:
                stats,path,sleeves=replay(c['id'],chosen['size'],chosen['test'],cost,capital,True)
                r=dict(candidate=c['id'],fold=chosen['fold'],size=chosen['size'],cost=cost,starting_equity=capital,return_on_equity=stats['pnl']/capital,**stats)
                results.append(r);candidate_results.append(r);capital+=stats['pnl'];paths.append(path)
                (out/f"c{c['id']}-fold{chosen['fold']}-{cost}-reductions.json").write_text(json.dumps([x for sl in sleeves for x in sl[0]['reductions']],indent=2))
                cache.clear()
            path=pd.concat(paths);eq=path.equity
            dd=float((eq/eq.cummax().clip(lower=10000)-1).min())
            combined[cost]=dict(pnl=capital-10000,max_drawdown=dd)
            path.resample('1h').last().to_csv(out/f"c{c['id']}-{cost}-equity.csv",index_label='timestamp')
        r15=[r for r in candidate_results if r['cost']==15]
        sales=sum(r['profitable_realizations'] for r in r15);days=sum(r['profitable_realization_days'] for r in r15)
        months=(pd.Timestamp('2026-10-01')-pd.Timestamp('2025-01-01')).days/30.4375
        agg=dict(candidate=c['id'],name=c['name'],net_pnl_8=combined[8]['pnl'],net_pnl_15=combined[15]['pnl'],net_pnl_30=combined[30]['pnl'],max_drawdown_15=combined[15]['max_drawdown'],positive_quarters_15=sum(r['pnl']>0 for r in r15),worst_quarter_return_15=min(r['return_on_equity'] for r in r15),profitable_sales_per_month_15=sales/months,profitable_days_per_month_15=days/months,liquidations=sum(r['liquidations'] for r in candidate_results),margin_rejections=sum(r['margin_rejections'] for r in candidate_results),all_training_eligible=all(s['eligible'] for s in selections if s['candidate']==c['id']))
        agg['robust_pass']=robust(agg);aggregate.append(agg);print('evaluated',json.dumps(agg),flush=True)
    eligible=[a for a in aggregate if a['robust_pass']]
    winner=max(eligible,key=lambda a:(a['net_pnl_15'],a['max_drawdown_15'])) if eligible else None
    summary=dict(aggregate=aggregate,winner=winner,folds=results,selection=selections,withdrawals='disabled pending forward gate')
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    for p in sources:assert hashlib.sha256(p.read_bytes()).hexdigest()==protocol['source_sha256'][p.name]
    print('COMPLETE winner',winner,flush=True)

if __name__=='__main__':main()
