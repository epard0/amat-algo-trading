"""Train-selected BTC long / ETH short sizing and frequent-profit study."""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import shutil

from autoresearch.crypto_perpetual_study import load_inputs, strategy_weights, TRAIN, PERIODS
from autoresearch.crypto_directional import directional_weights, run_directional
from autoresearch.crypto_risk_matched import paired_intervals


def candidate_grid():
    return [dict(leverage=l,sizing=s,tp_atr=tp) for l,s,tp in itertools.product((1,2),('full','vol20','vol40'),(None,1.,2.))]


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    root=Path('data/research/crypto-perpetual-2020-2026-09')
    inventory=Path('outputs/crypto-directional-2026-10-19/kalshi-inventory')
    fees=json.loads((inventory/'fees.json').read_text())['taker_fee_rates']
    if fees['KXBTCPERP']!=fees['KXETHPERP']:raise ValueError('study expects equal BTC/ETH taker fee')
    fee_bps=fees['KXBTCPERP']*10000
    protocol=dict(training=TRAIN,periods=PERIODS,candidates=candidate_grid(),portfolios=['BTC-long','ETH-short','BTC-long-ETH-short'],
        capital='10000 USDT in every portfolio; single assets get all capital, combination gets 5000 per independent sleeve',
        entries='fixed four-hour20day entry/10day exit; direction fixed per asset; no entry/lookback search',
        profit_taking='optional completed-close +1/+2 ATR14 gain from signal-close reference with ATR frozen at entry; 72h maximum holding; exit next4h open; no same-bar re-entry',
        selection='max training net P&L among >=3 round trips and no liquidation, per portfolio/leverage; negative best training P&L retained as failure evidence',
        frequent_profile='also select highest training net P&L among positive-net candidates with >=2 net profitable full closes per month; report missing qualification',
        frequency='net completed-position episodes including all fees/funding/partial rebalance P&L; profitable-close rate is an average, not guaranteed payout schedule',
        fee_bps=fee_bps,costs_bps_per_side=[8,15,30],fee_source='authenticated Kalshi /margin/fee_tiers snapshot; current fees held fixed in scenario',
        funding_source='Binance historical perpetual rate archives, not Kalshi funding before its short native history',
        benchmark='frozen four-hour2x vol20 symmetric BTC/ETH winner; rerun with same current fee and same total costs as new variants',
        caveats=['directional choice motivated by previously inspected later-period results; this is retrospective follow-up research',
                 '2020-2021 is short and bull-heavy','profit triggers are not guaranteed fill profits; next open and all costs determine actual closed P&L',
                 'withdrawals not simulated; cash stays invested and compounds','fixed maintenance and hourly adverse marks remain approximate',
                 'current Kalshi asset universe is a survivor-conditioned snapshot; multi-year histories use labeled proxies'],
        data_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
        fee_snapshot_sha256=hashlib.sha256((inventory/'fees.json').read_bytes()).hexdigest(),
        source_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in [Path(__file__),Path(__file__).with_name('crypto_directional.py'),Path(__file__).with_name('perpetual_sim.py')]})
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    all_frames,all_funding,audit=load_inputs(root);(out/'data-audit.json').write_text(json.dumps(audit,indent=2))
    portfolios={'BTC-long':{'BTCUSDT':1},'ETH-short':{'ETHUSDT':-1},'BTC-long-ETH-short':{'BTCUSDT':1,'ETHUSDT':-1}}
    training=[];cache={};selected={};missing=[]
    for name,directions in portfolios.items():
        frames={s:all_frames[s] for s in directions};funding={s:all_funding[s] for s in directions}
        for i,cfg in enumerate(candidate_grid()):
            weights=directional_weights(frames,cfg,directions);cache[name,i]=weights
            stats,*_=run_directional(frames,funding,weights,TRAIN,fee_bps=fee_bps,leverage_limit=cfg['leverage'])
            stats.pop('closed_episodes')
            training.append(dict(portfolio=name,id=i,config=cfg,**stats))
        for lev in (1,2):
            eligible=[r for r in training if r['portfolio']==name and r['config']['leverage']==lev and r['round_trips']>=3 and r['liquidations']==0]
            if not eligible:raise ValueError('no active training selection')
            selected[f'{name}-{lev}x-pnl']=max(eligible,key=lambda r:r['pnl'])
            frequent=[r for r in eligible if r['pnl']>0 and r['profitable_closes_per_month']>=2]
            if frequent:selected[f'{name}-{lev}x-frequent']=max(frequent,key=lambda r:r['pnl'])
            else:missing.append(dict(portfolio=name,leverage=lev,reason='no positive-net training candidate met >=2 profitable closes/month'))
        print('trained',name,flush=True)
    winner=max((k for k in selected if k.endswith('-pnl')),key=lambda k:selected[k]['pnl'])
    (out/'training.json').write_text(json.dumps(training,indent=2))
    (out/'selection.json').write_text(json.dumps(dict(selected=selected,global_training_winner=winner,missing_frequent=missing),indent=2))
    results=[];daily={};replay_cache={}
    for label,row in selected.items():
        name=row['portfolio'];directions=portfolios[name];cfg=row['config']
        frames={s:all_frames[s] for s in directions};funding={s:all_funding[s] for s in directions}
        for period,dates in PERIODS.items():
            for cost in (8,15,30):
                key=name,row['id'],period,cost
                if key not in replay_cache:
                    replay_cache[key]=run_directional(frames,funding,cache[name,row['id']],dates,cost=cost,fee_bps=fee_bps,leverage_limit=cfg['leverage'],capture=cost==8)
                stats,path,ret,ledger=replay_cache[key]
                episodes=stats['closed_episodes'];brief={k:v for k,v in stats.items() if k!='closed_episodes'}
                results.append(dict(strategy=label,period=period,cost_bps=cost,**brief));daily[label,period,cost]=ret
                if cost==8:
                    path.to_csv(out/f'{label}-{period}.csv',index_label='timestamp')
                    (out/f'{label}-{period}-episodes.json').write_text(json.dumps(episodes))
                    (out/f'{label}-{period}-ledger.json').write_text(json.dumps(ledger))
        print('evaluated',label,flush=True)
    benchmark_cfg=dict(timeframe=4,lookback=20,exit_ratio=2,sizing='vol20',leverage=2)
    benchmark_weights=strategy_weights(all_frames,benchmark_cfg)
    for period,dates in PERIODS.items():
        for cost in (8,15,30):
            stats,path,ret,ledger=run_directional(all_frames,all_funding,benchmark_weights,dates,cost=cost,fee_bps=fee_bps,capture=cost==8)
            stats.pop('closed_episodes');results.append(dict(strategy='current-4h-2x',period=period,cost_bps=cost,**stats))
            daily['current-4h-2x',period,cost]=ret
            if cost==8:path.to_csv(out/f'current-4h-2x-{period}.csv',index_label='timestamp')
    comparisons=[]
    for label in selected:
        for period in ('external','post_training'):
            comparisons.append(dict(strategy=label,period=period,**paired_intervals(daily[label,period,8],daily['current-4h-2x',period,8],samples=2000)))
    (out/'summary.json').write_text(json.dumps(dict(results=results,comparisons=comparisons,global_training_winner=winner,missing_frequent=missing),indent=2))
    lines=['# Directional BTC/ETH profit-taking study','',f'Global training P&L selection: {winner}.','',
        '| Strategy | Period | Cost/side | P&L | Return | Max DD | Vol | Profitable closes/month | Median holding hours | Positive weeks |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in results:
        lines.append(f"| {r['strategy']} | {r['period']} | {r['cost_bps']}bps | {r['pnl']:.2f} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['annualized_volatility']:.2%} | {r['profitable_closes_per_month']:.2f} | {r['median_holding_hours']} | {r['positive_week_fraction']:.1%} |")
    lines+=['','Frequent-profile training failures: '+json.dumps(missing),'','## Limits','']+protocol['caveats']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    snap=out/'source';snap.mkdir()
    for f in [Path(__file__),Path(__file__).with_name('crypto_directional.py'),Path(__file__).with_name('perpetual_sim.py')]:shutil.copy2(f,snap/f.name)
    print(json.dumps(dict(winner=winner,selections={k:r['config'] for k,r in selected.items()},missing_frequent=missing),indent=2))

if __name__=='__main__':main()
