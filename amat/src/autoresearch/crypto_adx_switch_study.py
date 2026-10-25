"""Training-selected ADX choice between two frozen SOL exit policies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from autoresearch.crypto_fast_breakout import prepare
from autoresearch.crypto_partial_exits import exit_plan, policies, replay_exit_plan, reduction_metrics
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_universe_study import load_asset
from autoresearch.crypto_adx_switch import market_context, fit_rules, FEATURES, LOOKBACKS, THRESHOLDS
from autoresearch.crypto_volatility_switch import regime_plan, decision_counts, select_training

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
    inventory=Path('outputs/crypto-directional-2026-10-19/kalshi-inventory/markets.json')
    parent=Path('outputs/crypto-doge-sol-partial-exits-2026-10-19')
    sources=[Path(__file__)]+[Path(__file__).with_name(n) for n in ('crypto_adx_switch.py','crypto_volatility_switch.py','crypto_partial_exits.py','crypto_fast_breakout.py','crypto_short_window_study.py','crypto_universe_study.py','crypto_doge_sol.py','crypto_directional.py','perpetual_sim.py')]
    protocol=dict(training=TRAIN,periods=PERIODS,capital=10000,allocation=dict(DOGE=.25,SOL=.75),leverage_per_sleeve=1.5,
        candidates='36 active switch rules plus2 fixed controls',features=FEATURES,lookbacks=LOOKBACKS,thresholds=THRESHOLDS,directions=['ladder at high ADX','ladder at low ADX'],
        calibration='Fixed grid of ADX lookbacks7/14/28 four-hour bars and thresholds15/20/25/30/35/40, both orientations. Select on training P&L only, require12 finite training entry observations. Missing ADX defaults original.',
        adx='Wilder TR and directional movement seeded with n intervals; first ADX average of n DX then recursive Wilder smoothing. First valid index2n-1; missing candles reset all state. Computed only on completed4h candles. Formula: https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx',
        switching='Choose mode once at each original SOL entry, lock until position flat. Feature includes the just-completed signal candle; first execution is next-minute open. No mid-position switching or early reentry. A ladder still open skips overlapping original entries.',
        fixed_original='SOL original2ATR full target,10day channel exit,72h cap. DOGE unchanged0.5ATR full target/72h.',
        fixed_ladder='SOL sells25% at1entryATR and nominal next25% at2ATR, trails remainder3entryATR from highest completed4h close;72h cap and10day channel. Frozen before new search.',
        selection='Maximum training net P&L among positive liquidation-free candidates with>=6 completed positions; active rules must execute>=2 original and>=2 ladder SOL entry episodes. Fixed controls allowed to win. Also retain best active switch and best active switch meeting P&L>=original, daily DD no worse, profitable realization days>=1.25x original.',
        costs='8bps/side primary:4bps current-snapshot taker+4bps modeled spread/slippage. All retained rules and controls stressed15/30bps.',
        accounting='Existing exact Decimal wallets, minute mark extrema, integer lots/ticks, actual historical funding timestamps. No buyback after partial sale. Profitable realized reductions include proportional entry fees/funding; routine resizing/forced closes excluded from frequency.',
        data='Verified Binance perpetual minute prices/marks and funding;2 missing training mark minutes require forced-flat blackout, with costs. Not native historical Kalshi executions/funding.',
        caveats=['Assets and later dates already inspected; retrospective chronological follow-up, not pristine OOS',
            'Only a few dozen original SOL training entries; threshold selection can overfit',
            'Training frequency/risk gate is not a future guarantee; compare both holdout subdivisions and continuous account',
            'Independent collateral sleeves, no transfers; realized profits remain in account, no withdrawals',
            'Fixed maintenance/liquidation model; no measured quote depth/impact or dynamic venue risk tiers',
            'Minute-close DD can miss intraminute troughs; planned entry counts exclude carried signal state, actual episode counts include it'],
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        minute_manifest_sha256=hashlib.sha256((minute_root/'manifest.json').read_bytes()).hexdigest(),
        funding_manifest_sha256={s:hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest() for s,p in roots.items()},
        inventory_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest(),
        parent_selection_sha256=hashlib.sha256((parent/'selection.json').read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    snapshot=out/'source'
    snapshot.mkdir()
    for p in sources:shutil.copy2(p,snapshot/p.name)
    frames,audit=load_minutes(minute_root)
    (out/'data-audit.json').write_text(json.dumps(audit,indent=2))
    markets=json.loads(inventory.read_text())['markets']
    assets={}
    for symbol in frames:
        _,funding,_=load_asset(roots[symbol],symbol)
        m=next(m for m in markets if m['ticker']==f'KX{symbol.removesuffix("USDT")}PERP')
        spec=dict(multiplier=m['contract_size'],price_tick=str(float(m['tick_size'])/float(m['contract_size'])))
        assets[symbol]=prepare(frames[symbol],funding[symbol]),spec
    context=market_context(frames['SOLUSDT'])
    rules,fit_audit=fit_rules(context,TRAIN)
    (out/'thresholds.json').write_text(json.dumps(dict(rules=rules,feature_audit=fit_audit),indent=2))
    context['features'].to_csv(out/'features.csv',index_label='decision_timestamp')
    doge_w,doge_why=exit_plan(frames['DOGEUSDT'],policies()[0],.5)
    plans={}
    def plan(rule):
        if rule['id'] not in plans:
            plans[rule['id']]=regime_plan(context,rule)
        return plans[rule['id']]
    def replay(symbol,w,why,dates,cost=8,full=False):
        data,spec=assets[symbol]
        return replay_exit_plan(data,w,why,dates,capital=2500 if symbol=='DOGEUSDT' else 7500,
            leverage=1.5,symbol=symbol,specs=spec,cost=cost,capture=full,full_path=full)
    def episodes_by_mode(sol,modes):
        counts=dict(actual_original_entries=0,actual_ladder_entries=0)
        times=assets['SOLUSDT'][0]['ms']
        for episode in sol[0]['closed_episodes']:
            i=int(np.searchsorted(times,episode['entry_ms'],side='right')-1)
            mode=int(modes[i])
            if mode not in (1,2):raise AssertionError('entry without latched mode')
            counts['actual_original_entries' if mode==1 else 'actual_ladder_entries']+=1
        return counts
    doge=replay('DOGEUSDT',doge_w,doge_why,TRAIN)
    end_ms=int((pd.Timestamp(TRAIN[1],tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)-1
    rows=[]
    for rule in rules:
        w,why,modes,decisions=plan(rule)
        sol=replay('SOLUSDT',w,why,TRAIN)
        stats,_=reduction_metrics([doge,sol],end_ms)
        rows.append(dict(id=rule['id'],rule=rule,**stats,**decision_counts(decisions,TRAIN),**episodes_by_mode(sol,modes)))
        print(f'trained rule {rule["id"]+1}/38 pnl={stats["pnl"]:.2f}',flush=True)
    (out/'training.json').write_text(json.dumps(rows,indent=2))
    selection=select_training(rows)
    (out/'selection.json').write_text(json.dumps(selection,indent=2))
    print('LOCKED SELECTION',json.dumps(selection),flush=True)
    retained=[0,1]
    roles={}
    for role in ('primary','best_switch','frequency_switch'):
        chosen=selection[role]
        roles[role]=chosen['id'] if chosen else None
        if chosen is not None and chosen['id'] not in retained:retained.append(chosen['id'])
    (out/'roles.json').write_text(json.dumps(roles,indent=2))
    results=[]
    doge_cache={}
    for rid in retained:
        rule=rules[rid]
        w,why,modes,decisions=plan(rule)
        (out/f'rule-{rid}-decisions.json').write_text(json.dumps(decisions,indent=2))
        for period,dates in PERIODS.items():
            end_ms=int((pd.Timestamp(dates[1],tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)-1
            for cost in (8,15,30):
                if (period,cost) not in doge_cache:doge_cache[period,cost]=replay('DOGEUSDT',doge_w,doge_why,dates,cost,True)
                sol=replay('SOLUSDT',w,why,dates,cost,True)
                sleeves=[doge_cache[period,cost],sol]
                stats,path=reduction_metrics(sleeves,end_ms)
                results.append(dict(id=rid,rule=rule,period=period,cost_bps=cost,**stats,
                    **decision_counts(decisions,dates),**episodes_by_mode(sol,modes)))
                if cost==8:
                    path.resample('1h').last().to_csv(out/f'rule-{rid}-{period}.csv',index_label='timestamp')
                    (out/f'rule-{rid}-{period}-reductions.json').write_text(json.dumps([r for s in sleeves for r in s[0]['reductions']],indent=2))
                    (out/f'rule-{rid}-{period}-ledgers.json').write_text(json.dumps([s[2] for s in sleeves],indent=2))
        print('evaluated rule',rid,flush=True)
    previous=json.loads((parent/'summary.json').read_text())['results']
    parity=[]
    for rid,label in [(0,'baseline'),(1,'no_sacrifice')]:
        for period in PERIODS:
            for cost in (8,15,30):
                actual=next(r for r in results if r['id']==rid and r['period']==period and r['cost_bps']==cost)
                expected=next(r for r in previous if r['strategy']==label and r['period']==period and r['cost_bps']==cost)
                metrics=('pnl','fees','funding_pnl','max_drawdown','max_minute_close_drawdown','profitable_realizations_per_month','profitable_realization_days_per_month')
                for metric in metrics:
                    if not np.isclose(actual[metric],expected[metric],atol=1e-6,rtol=0):raise AssertionError('fixed control changed: '+label+'/'+period+'/'+metric)
                parity.append(dict(id=rid,period=period,cost_bps=cost,matched=list(metrics)))
    (out/'control-parity.json').write_text(json.dumps(parity,indent=2))
    (out/'summary.json').write_text(json.dumps(dict(selection=selection,roles=roles,results=results),indent=2))
    print('COMPLETE',json.dumps([r for r in results if r['period']=='test' and r['cost_bps']==8]),flush=True)


if __name__=='__main__':main()
