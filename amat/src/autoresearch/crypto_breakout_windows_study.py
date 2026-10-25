"""Registered four-window comparison; training choice locked before evaluation."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import numpy as np
import pandas as pd
from autoresearch.crypto_adx_leverage import replay
from autoresearch.crypto_breakout_windows import WINDOWS,window_plan,entry_metrics,select_window
from autoresearch.crypto_walkforward import fresh_plan
from autoresearch.crypto_adx_leverage_study import PERIODS
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_universe_study import load_asset
from autoresearch.crypto_fast_breakout import prepare

OUT=Path('outputs/crypto-adx-breakout-windows-2026-10-20')
TRAIN=('2024-01-01','2025-06-30')
PARENT=Path('outputs/crypto-adx-leverage-costs-2026-10-20-v2')


def main():
    out=OUT;out.mkdir(exist_ok=False)
    root=Path('data/research/doge-sol-minute-2024-2026-09');perps=Path('../kalshi-perps')
    names=('crypto_breakout_windows.py','crypto_adx_leverage.py','crypto_adx_leverage_study.py','crypto_adx_switch.py','crypto_volatility_switch.py','crypto_partial_exits.py','crypto_walkforward.py','crypto_doge_sol.py','crypto_directional.py','crypto_fast_breakout.py','crypto_short_window_study.py','crypto_universe_study.py')
    sources=[Path(__file__)]+[Path(__file__).with_name(n) for n in names]+[perps/'src/kalshi_perps'/n for n in ('paper.py','leverage.py','domain.py')]
    protocol=dict(windows_days=WINDOWS,training=TRAIN,periods=PERIODS,capital=10000,selection='Joint same entry window for DOGE andSOL. Maximum median-cost training net P&L among positive,zero liquidation/margin rejection,>=6 actual entries and supported empirical order sizes. Tie smaller drawdown then longer window.20day control can win; save choice before evaluation.',
        frozen='Four-hour completed-close entries. DOGE0.5ATR full TP. SOL ADX14>=20 entry-locked fixed ladder(25% at1ATR,next25% at2ATR,3ATRclose trail); original2ATRfull exits otherwise.10day channel exits and72h cap unchanged for all entry windows. No same-bar reentry; ladder blocks overlapping original entries.',
        sizing='Identical previous max scenario: pooled collateral,25/75 active allocations normalized,2% initial-margin reserve, current frozen venue side/size curves; daily and active-set resizing; no buying back partial sales. Integer native contracts/ticks; historical funding at actual timestamps.',
        costs='Reuse frozen corrected675quote size-sensitive median/p95 costs and double-p95-slippage stress;4bps fee unchanged. No unsupported extrapolation beyond measured200k order notional. A capacity failure marks a candidate unsupported/ineligible, not zero-return or fictitiously filled.',
        evidence='Retrospective chronological follow-up on already-inspected assets/dates; Oct20native margin/quote snapshot applied to historical Binance trade/mark/funding proxies. Not historical native maximum leverage or pristine unseen OOS.',
        control='20day max scenario must reproduce prior study across3 later periods x3 cost cases, including P&L/drawdown/fees/funding/TP counts. Do not alter live/paper default based on this study.',
        source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},parent_inputs_sha256={n:hashlib.sha256((PARENT/n).read_bytes()).hexdigest() for n in ('venue-snapshot.json','cost-curves.json','summary.json')},minute_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2));(out/'source').mkdir()
    for p in sources:shutil.copy2(p,out/'source'/p.name)
    for n in ('venue-snapshot.json','cost-curves.json'):shutil.copy2(PARENT/n,out/n)
    sys.path.insert(0,str((perps/'src').resolve()));from kalshi_perps.domain import registry
    instruments=registry(json.loads((perps/'configs/instruments.json').read_text()));instruments={s:instruments[s] for s in ('KXDOGEPERP','KXSOLPERP')}
    venue=json.loads((out/'venue-snapshot.json').read_text());risk=venue['risk_parameters'];meta={s:dict(long=m['long_leverage_estimates'],short=m['short_leverage_estimates'],initial_margin_multiplier=risk['initial_margin_multiplier'][s]) for s,m in venue['markets'].items()};curves=json.loads((out/'cost-curves.json').read_text())
    frames,audit=load_minutes(root);(out/'data-audit.json').write_text(json.dumps(audit,indent=2));assets={};plans={}
    roots=dict(DOGEUSDT=Path('data/research/kalshi-universe-proxies-2021-2026-09'),SOLUSDT=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09'))
    for symbol,frame in frames.items():
        s='KX'+symbol.removesuffix('USDT')+'PERP';_,funding,_=load_asset(roots[symbol],symbol);assets[s]=prepare(frame,funding[symbol])
        for window in WINDOWS:plans[window,s]=window_plan(frame,window,symbol.removesuffix('USDT'))
    def run(window,dates,case,stem):
        active={s:fresh_plan(*plans[window,s],assets[s]['index'],dates) for s in assets}
        try:stats,path,ledger,reductions,closed=replay(assets,active,instruments,meta,curves,dates,'max',case)
        except ValueError as exc:
            if 'outside observed depth calibration' not in str(exc):raise
            return dict(window=window,cost_case=case,capacity_supported=False,error=str(exc))
        stats.update(window=window,cost_case=case,capacity_supported=True,**entry_metrics(ledger,dates))
        path.resample('1h').last().to_csv(out/(stem+'.csv'),index_label='timestamp')
        for label,rows in [('ledger',ledger),('reductions',reductions),('episodes',closed)]:
            (out/(stem+'-'+label+'.json')).write_text(json.dumps(rows,indent=2))
        return stats
    training=[]
    for window in WINDOWS:
        row=run(window,TRAIN,'median',f'train-{window}');training.append(row)
        print('TRAIN',json.dumps(row),flush=True)
    (out/'training.json').write_text(json.dumps(training,indent=2));selection=select_window(training);(out/'selection.json').write_text(json.dumps(selection,indent=2));print('LOCKED',json.dumps(selection),flush=True)
    results=[]
    for window in WINDOWS:
        for period,dates in PERIODS.items():
            for case in ('median','p95','double_p95_slippage'):
                row=run(window,dates,case,f'{period}-{case}-{window}');row['period']=period;results.append(row)
                print('EVAL',json.dumps(row),flush=True)
    previous=json.loads((PARENT/'summary.json').read_text())['results'];parity=[]
    for r in results:
        if r['window']!=20:continue
        expected=next(p for p in previous if p['period']==r['period'] and p['cost_case']==r['cost_case'] and p['leverage']=='max')
        for key in ('pnl','max_drawdown','fees','funding_pnl','liquidations','margin_rejections','take_profit_events','profitable_realizations_per_month'):
            if not np.isclose(r[key],expected[key],atol=1e-6,rtol=0):raise AssertionError('20day control changed:'+key)
        parity.append(dict(period=r['period'],cost_case=r['cost_case'],matched=True))
    (out/'control-parity.json').write_text(json.dumps(parity,indent=2));(out/'summary.json').write_text(json.dumps(dict(selection=selection,training=training,results=results),indent=2))
    for p in sources:assert hashlib.sha256(p.read_bytes()).hexdigest()==protocol['source_sha256'][str(p)]
    print('COMPLETE',flush=True)

if __name__=='__main__':main()
