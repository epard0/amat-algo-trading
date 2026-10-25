"""Frozen-entry DOGE/SOL partial-sale and trailing-runner study."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from autoresearch.crypto_fast_breakout import prepare
from autoresearch.crypto_partial_exits import exit_plan, policies, replay_exit_plan, reduction_metrics, select_partial_training
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_universe_study import load_asset

TRAIN=('2024-01-01','2025-06-30')
PERIODS=dict(validation=('2025-07-01','2025-12-31'),test=('2026-01-01','2026-09-30'),post_training=('2025-07-01','2026-09-30'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    out=args.output
    out.mkdir(parents=True,exist_ok=False)
    minute_root=Path('data/research/doge-sol-minute-2024-2026-09')
    roots=dict(DOGEUSDT=Path('data/research/kalshi-universe-proxies-2021-2026-09'),SOLUSDT=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09'))
    inv=Path('outputs/crypto-directional-2026-10-19/kalshi-inventory/markets.json')
    cfgs=policies()
    sources=[Path(__file__)]+[Path(__file__).with_name(n) for n in ('crypto_partial_exits.py','crypto_fast_breakout.py','crypto_doge_sol.py','crypto_directional.py','crypto_short_window_study.py','crypto_universe_study.py','perpetual_sim.py')]
    protocol=dict(training=TRAIN,periods=PERIODS,starting_equity=10000,allocation=dict(DOGE=.25,SOL=.75),leverage_per_sleeve=1.5,
        candidates=cfgs,policies_per_asset=len(cfgs),paired_candidates=len(cfgs)**2,
        frozen_entries='Original4h20day breakout/10day channel calendar with DOGE0.5ATR/SOL2ATR full target and72h hold defines entry opportunities. Entry calendar, allocation and leverage never optimized. A still-open runner skips overlapping original entry opportunities; no early re-entry after a trail.',
        exits='Preserve original final exit while taking25/50% earlier at25/50% of original target; or partial25/50% at50/100% of target with1/2/3 entry-ATR trailing runner and72/168h cap; or25%+25% ladder at50/100% of target with trailing remainder; or trailing-only. Trail high-water and triggers use completed4h closes and frozen entry ATR14, next-minute-open execution.',
        partial_accounting='Real integer contract reductions; each partial proportional to current contracts/remaining plan fraction; no buyback of a trimmed runner at daily resizing. Whole position uses normal daily sizing before first trim. No assumed intrabar target fills.',
        selection='Training only: max net P&L, plus max net P&L among positive liquidation-free portfolios with P&L>=baseline, daily DD no worse than baseline, and profitable realization days/month>=1.25x baseline. No relaxation if no candidate qualifies. Baseline included as pair0/0.',
        profit_metric='Actual closing reductions including partials; net realized price P&L minus closing fee plus proportional carried entry fees and funding. Rebalance reductions included in net daily realization amounts but excluded from event frequency; forced terminal/blackout exits excluded from frequency. No withdrawals.',
        costs='8bps per side primary:4bps taker fee+4bps modeled spread/slippage; selected portfolios and baseline stressed15/30bps',
        funding='Historical Binance rates and millisecond funding events, once before orders; Binance trade/mark/funding proxies, not native Kalshi executions/funding',
        execution='Canonical minute marks, exact Decimal wallet, integer lots, current Kalshi multiplier/tick snapshot, fixed0.5% maintenance and0.5% liquidation fee',
        data_quality='Previously verified minute data;2 unavailable mark minutes per asset in training require forced-flat blackout and costs. Zero-volume orders retried at tradability resumes.',
        caveats=['Training and later dates/assets already inspected; retrospective chronological follow-up, not pristine OOS',
            'Training no-sacrifice criterion is not a future guarantee; partial profit sales can reduce final P&L',
            'Profit realization events/days are not scheduled withdrawals; capital stays in collateral sleeves',
            'Independent initial collateral sleeves compound separately, without transfers or reallocation',
            'No measured historical order-book depth, market impact or dynamic Kalshi risk tiers',
            'Period replays restart with10,000 and known signal state; continuous post-training also reported; minute-close DD excludes intraminute troughs'],
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        minute_manifest_sha256=hashlib.sha256((minute_root/'manifest.json').read_bytes()).hexdigest(),
        funding_manifest_sha256={s:hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest() for s,p in roots.items()},
        inventory_sha256=hashlib.sha256(inv.read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    snap=out/'source'
    snap.mkdir()
    for p in sources:shutil.copy2(p,snap/p.name)
    frames,audit=load_minutes(minute_root)
    (out/'data-audit.json').write_text(json.dumps(audit,indent=2))
    markets=json.loads(inv.read_text())['markets']
    assets={}
    for symbol in frames:
        _,funding,_=load_asset(roots[symbol],symbol)
        m=next(m for m in markets if m['ticker']==f'KX{symbol.removesuffix("USDT")}PERP')
        spec=dict(multiplier=m['contract_size'],price_tick=str(float(m['tick_size'])/float(m['contract_size'])))
        assets[symbol]=prepare(frames[symbol],funding[symbol]),spec
    plans=out/'plans'
    plans.mkdir()
    def get_plan(symbol,ei):
        path=plans/f'{symbol}-{ei}.npz'
        if path.exists():
            f=np.load(path)
            return f['weights'],f['reasons']
        w,why=exit_plan(frames[symbol],cfgs[ei],.5 if symbol=='DOGEUSDT' else 2.)
        np.savez_compressed(path,weights=w,reasons=why)
        return w,why
    def replay(symbol,ei,dates,cost=8,full=False):
        w,why=get_plan(symbol,ei)
        data,spec=assets[symbol]
        return replay_exit_plan(data,w,why,dates,capital=2500 if symbol=='DOGEUSDT' else 7500,
            leverage=1.5,symbol=symbol,specs=spec,cost=cost,capture=full,full_path=full)
    bank={}
    for symbol,ei in itertools.product(frames,range(len(cfgs))):
        bank[symbol,ei]=replay(symbol,ei,TRAIN)
        if (ei+1)%7==0:print(f'trained {symbol} exits {ei+1}/{len(cfgs)}',flush=True)
    end_ms=int((pd.Timestamp(TRAIN[1],tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)-1
    rows=[]
    for di,si in itertools.product(range(len(cfgs)),repeat=2):
        stats,_=reduction_metrics([bank['DOGEUSDT',di],bank['SOLUSDT',si]],end_ms)
        rows.append(dict(id=len(rows),doge_policy_id=di,sol_policy_id=si,doge_policy=cfgs[di],sol_policy=cfgs[si],**stats))
    (out/'training.json').write_text(json.dumps(rows,indent=2))
    selection=select_partial_training(rows)
    (out/'selection.json').write_text(json.dumps(selection,indent=2))
    print('LOCKED SELECTION',json.dumps(selection),flush=True)
    del bank
    results=[]
    for label in ('baseline','max_training_pnl','no_sacrifice'):
        chosen=selection[label]
        if chosen is None:continue
        for period,dates in PERIODS.items():
            end_ms=int((pd.Timestamp(dates[1],tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)-1
            for cost in (8,15,30):
                sleeves=[replay('DOGEUSDT',chosen['doge_policy_id'],dates,cost,True),replay('SOLUSDT',chosen['sol_policy_id'],dates,cost,True)]
                stats,path=reduction_metrics(sleeves,end_ms)
                results.append(dict(strategy=label,period=period,cost_bps=cost,id=chosen['id'],doge_policy=chosen['doge_policy'],sol_policy=chosen['sol_policy'],**stats))
                if cost==8:
                    path.resample('1h').last().to_csv(out/f'{label}-{period}.csv',index_label='timestamp')
                    (out/f'{label}-{period}-reductions.json').write_text(json.dumps([r for s in sleeves for r in s[0]['reductions']],indent=2))
                    (out/f'{label}-{period}-ledgers.json').write_text(json.dumps([s[2] for s in sleeves],indent=2))
        print('evaluated',label,flush=True)
    # A changed exit model must reproduce the preceding fixed4h controls exactly.
    old=json.loads(Path('outputs/crypto-doge-sol-short-windows-2026-10-19-v2/summary.json').read_text())
    parity=[]
    for period in PERIODS:
        actual=next(r for r in results if r['strategy']=='baseline' and r['period']==period and r['cost_bps']==8)
        expected=next(r for r in old['results'] if r['strategy']=='previous-4h-1.5x' and r['period']==period and r['cost_bps']==8)
        for metric in ('pnl','fees','funding_pnl','max_drawdown','max_minute_close_drawdown'):
            if not np.isclose(actual[metric],expected[metric],atol=1e-6,rtol=0):raise AssertionError('baseline changed: '+period+'/'+metric)
        parity.append(dict(period=period,matched=['pnl','fees','funding_pnl','max_drawdown','max_minute_close_drawdown']))
    (out/'baseline-parity.json').write_text(json.dumps(parity,indent=2))
    (out/'summary.json').write_text(json.dumps(dict(selection=selection,results=results),indent=2))
    print('COMPLETE',json.dumps([r for r in results if r['period']=='test' and r['cost_bps']==8]),flush=True)


if __name__=='__main__':main()
