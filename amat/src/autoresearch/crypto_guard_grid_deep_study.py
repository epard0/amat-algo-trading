"""Training-selected window/leverage grid with the paper initial-equity halt."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import numpy as np
import pandas as pd
from autoresearch.crypto_guarded_replay import replay_guarded
from autoresearch.crypto_breakout_windows import WINDOWS,window_plan,entry_metrics
from autoresearch.crypto_walkforward import fresh_plan
from autoresearch.crypto_adx_leverage_study import PERIODS
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_universe_study import load_asset
from autoresearch.crypto_fast_breakout import prepare

OUT=Path('outputs/crypto-adx-guard-window-leverage-2026-10-20-v2')
TRAIN=('2024-01-01','2025-06-30')
PARENT=Path('outputs/crypto-adx-leverage-costs-2026-10-20-v2')
WINDOW_PARENT=Path('outputs/crypto-adx-breakout-windows-2026-10-20')
LEVERAGES=('1','2','max')


def select_training(rows):
    eligible=[r for r in rows if r.get('capacity_supported',True) and r['pnl']>0 and not r['guard_triggered'] and r['liquidations']==0 and r['margin_rejections']==0 and r['entries']>=6]
    raw=[r for r in rows if r.get('capacity_supported',True)]
    winner=max(eligible,key=lambda r:(r['pnl'],r['max_drawdown'],r['window'])) if eligible else None
    return dict(winner=winner,eligible_configs=[dict(window=r['window'],leverage=r['leverage']) for r in eligible],unconstrained_pnl_leader=max(raw,key=lambda r:r['pnl']) if raw else None,criterion='Highest median-cost training net P&L among positive,>=6 entries,depth-supported,zero guard halt/liquidation/margin rejection. Tie smaller DD then longer window.')


def main():
    out=OUT;out.mkdir(exist_ok=False)
    root=Path('data/research/doge-sol-minute-2024-2026-09');perps=Path('../kalshi-perps')
    names=('crypto_guarded_replay.py','crypto_breakout_windows.py','crypto_adx_leverage.py','crypto_adx_leverage_study.py','crypto_adx_switch.py','crypto_volatility_switch.py','crypto_partial_exits.py','crypto_walkforward.py','crypto_doge_sol.py','crypto_directional.py','crypto_fast_breakout.py','crypto_short_window_study.py','crypto_universe_study.py')
    sources=[Path(__file__)]+[Path(__file__).with_name(n) for n in names]+[perps/'src/kalshi_perps'/n for n in ('paper.py','leverage.py','domain.py','runtime.py')]
    protocol=dict(windows=WINDOWS,leverages=LEVERAGES,training=TRAIN,periods=PERIODS,capital=10000,guard_fraction=.35,
        guard='Exact order-halt semantics: after mark/funding, equity<=65% initial collateral permanently halts ALL strategy orders, including reductions/exits. No forced close and no auto-resume. Existing positions keep mark P&L, funding and maintenance-liquidation risk. Period-end flatten is research-only and costed. It is not a35% max-drawdown or loss cap.',
        sampling='Historical minute opens and closes approximate the30second REST marks; thresholds rechecked after funding and before orders at event boundaries. Intraminute lows check maintenance only, not the loss guard. Funding before guard matches paper loop.',
        data_quality='Mandatory missing-mark training blackout force-closes prior to unavailable marks even if already guard-halted; counted separately. No open holding priced using missing data.',
        selection='Save maximum median-cost training net P&L choice before any later replay. Eligible: positive P&L,>=6 actual entries,zero guard-trigger/liquidation/rejection and supported empirical order sizes. Guard-halted candidates are ineligible even if passive held positions later recover. Also retain unconstrained training P&L leader as diagnostic only.',
        frozen='Only jointly shared DOGE/SOL entry lookback5/10/15/20days and1x/2x/max sizing vary. Four-hour signals,ADX14>=20entry-locked SOL ladder,original exits otherwise,DOGE0.5ATRfull TP,10day channel,72h cap remain fixed. Pooled collateral25/75 active allocation normalized;2% reserve,daily and active-set resize,no buyback after partial.',
        evaluation='Separate validation,test and continuous accounts each start10,000USD,flat,skip carried signal. Do not resume halted training account into holdout. Later results are hypothetical fresh accounts for comparison. Report all12 configurations, not retrospectively chosen later winners.',
        costs='Keep original675sample median/p95 cost buckets through200k unchanged. Above200k,extend from30new native depth100 books at225k/250k/300k/400k/500k/600k/750k/1m,require>=95%full observed depth coverage,nondecreasing size costs.4bps fee and double-p95-slippage stress unchanged. Do not extrapolate beyond supported depth. Current venue curves and historical Binance funding unchanged.',
        evidence='Retrospective chronological follow-up with previously inspected assets/dates and later Oct20cost/margin calibration. Not pristine unseen OOS or a native historical max-leverage backtest.',
        controls='Disabled-guard replay must reproduce54 distinct prior window/leverage/period/cost controls. Guarded results with no trigger must match controls too.',
        source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},parent_inputs_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (PARENT/'venue-snapshot.json',PARENT/'cost-curves.json',PARENT/'summary.json',WINDOW_PARENT/'summary.json')},minute_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())
    deep=Path('data/research/kalshi-deep-books-2026-10-20')
    protocol['deep_cost_inputs_sha256']={n:hashlib.sha256((deep/n).read_bytes()).hexdigest() for n in ('quotes.jsonl','manifest.json','extended-cost-curves.json')}
    protocol['prior_run']='Initial200k-domain run preserved; two2x training candidates were unsupported. This rerun changes only large-order cost coverage using newly collected quotes, before new training selection; no criterion/strategy tuning.'
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2));(out/'source').mkdir()
    for p in sources:shutil.copy2(p,out/'source'/p.name)
    for name in ('venue-snapshot.json','cost-curves.json'):shutil.copy2(PARENT/name,out/name)
    shutil.copy2(deep/'extended-cost-curves.json',out/'cost-curves.json');shutil.copy2(deep/'manifest.json',out/'deep-cost-manifest.json')
    sys.path.insert(0,str((perps/'src').resolve()));from kalshi_perps.domain import registry
    instruments=registry(json.loads((perps/'configs/instruments.json').read_text()));instruments={s:instruments[s] for s in ('KXDOGEPERP','KXSOLPERP')}
    venue=json.loads((out/'venue-snapshot.json').read_text());risk=venue['risk_parameters'];meta={s:dict(long=m['long_leverage_estimates'],short=m['short_leverage_estimates'],initial_margin_multiplier=risk['initial_margin_multiplier'][s]) for s,m in venue['markets'].items()};curves=json.loads((out/'cost-curves.json').read_text())
    frames,audit=load_minutes(root);(out/'data-audit.json').write_text(json.dumps(audit,indent=2));assets={};plans={}
    roots=dict(DOGEUSDT=Path('data/research/kalshi-universe-proxies-2021-2026-09'),SOLUSDT=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09'))
    for symbol,frame in frames.items():
        s='KX'+symbol.removesuffix('USDT')+'PERP';_,funding,_=load_asset(roots[symbol],symbol);assets[s]=prepare(frame,funding[symbol])
        for window in WINDOWS:plans[window,s]=window_plan(frame,window,symbol.removesuffix('USDT'))
    counter=0
    def run(window,lev,dates,case,stem,guard=.35,save=True):
        nonlocal counter
        active={s:fresh_plan(*plans[window,s],assets[s]['index'],dates) for s in assets}
        try:stats,path,ledger,reductions,closed=replay_guarded(assets,active,instruments,meta,curves,dates,lev,case,guard_fraction=guard)
        except ValueError as exc:
            if 'outside observed depth calibration' not in str(exc):raise
            return dict(window=window,leverage=lev,cost_case=case,capacity_supported=False,error=str(exc))
        counter+=1;stats.update(window=window,leverage=lev,cost_case=case,capacity_supported=True,**entry_metrics(ledger,dates))
        if save:
            path.resample('1h').last().to_csv(out/(stem+'.csv'),index_label='timestamp')
            for label,rows in [('ledger',ledger),('reductions',reductions),('episodes',closed)]:
                (out/(stem+'-'+label+'.json')).write_text(json.dumps(rows,indent=2))
        return stats
    training=[]
    for window in WINDOWS:
        for lev in LEVERAGES:
            r=run(window,lev,TRAIN,'median',f'train-{window}-{lev}');training.append(r)
            print('TRAIN',json.dumps({k:r.get(k) for k in ('window','leverage','pnl','guard_triggered','liquidations','max_drawdown','capacity_supported')}),flush=True)
    (out/'training.json').write_text(json.dumps(training,indent=2));selection=select_training(training);(out/'selection.json').write_text(json.dumps(selection,indent=2));print('LOCKED',json.dumps(selection),flush=True)
    results=[]
    for window in WINDOWS:
        for lev in LEVERAGES:
            for period,dates in PERIODS.items():
                for case in ('median','p95','double_p95_slippage'):
                    r=run(window,lev,dates,case,f'{period}-{case}-{window}-{lev}');r['period']=period;results.append(r)
            print('evaluated',window,lev,flush=True)
    # All previous max-window controls plus1x/2x20day controls:54 unique scenarios.
    old_windows=json.loads((WINDOW_PARENT/'summary.json').read_text())['results'];old_lev=json.loads((PARENT/'summary.json').read_text())['results'];parity=[]
    keys=('pnl','max_drawdown','fees','funding_pnl','liquidations','margin_rejections','take_profit_events','profitable_realizations_per_month')
    for expected in old_windows+[dict(r,window=20) for r in old_lev if r['leverage']!='max']:
        w=expected['window'];lev=expected.get('leverage','max');period=expected['period'];case=expected['cost_case']
        actual=run(w,lev,PERIODS[period],case,'control',guard=None,save=False)
        for key in keys:
            if not np.isclose(actual[key],expected[key],atol=1e-6,rtol=0):raise AssertionError(f'control changed:{w}/{lev}/{period}/{case}/{key}')
        guarded=next(r for r in results if r['window']==w and r['leverage']==lev and r['period']==period and r['cost_case']==case)
        if guarded['capacity_supported'] and not guarded['guard_triggered']:
            for key in keys:
                if not np.isclose(actual[key],guarded[key],atol=1e-6,rtol=0):raise AssertionError('untriggered guard changed path:'+key)
        parity.append(dict(window=w,leverage=lev,period=period,cost_case=case,matched=True))
    (out/'control-parity.json').write_text(json.dumps(parity,indent=2));(out/'summary.json').write_text(json.dumps(dict(selection=selection,training=training,results=results,replays=counter),indent=2))
    for p in sources:assert hashlib.sha256(p.read_bytes()).hexdigest()==protocol['source_sha256'][str(p)]
    print('COMPLETE',counter,'replays',flush=True)

if __name__=='__main__':main()
