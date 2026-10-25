"""Compact, relevant P&L leaders across directional, universe and interval studies."""
from pathlib import Path
import json
import hashlib
import shutil
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

ROOT=Path('outputs/crypto-directional-2026-10-19')


def main():
    core=json.loads((ROOT/'four-hour/summary.json').read_text())
    broad=json.loads((ROOT/'universe/summary.json').read_text())
    extra=json.loads((ROOT/'universe-supplement/summary.json').read_text())
    intra=json.loads((ROOT/'intraday/summary.json').read_text())
    current=json.loads((ROOT/'intraday-current-winner/summary.json').read_text())
    core_rows=[r for r in core['results'] if r['period']=='post_training' and r['cost_bps']==8]
    univ_rows=[r for r in broad['results']+extra['results'] if r['period']=='post_training' and r['cost_bps']==8]
    expanded_training={}
    for directory in ('universe','universe-supplement'):
        selection=json.loads((ROOT/directory/'selection.json').read_text())
        expanded_training.update(selection['selected'])
    training_winner=max((k for k in expanded_training if k.endswith('-pnl')),key=lambda k:expanded_training[k]['pnl'])
    observed_universe=max(univ_rows,key=lambda r:r['pnl'])
    core_winner=next(r for r in core_rows if r['strategy']==core['global_training_winner'])
    best_frequency=max((r for r in univ_rows if r['strategy'].endswith('-frequent')),key=lambda r:r['pnl'],default=None)
    get_core=lambda name:next(r for r in core_rows if r['strategy']==name)
    interval=[]
    for minutes in (1,5,15,30,240):
        label=f'BTC-long-{minutes}m-2x-pnl'
        row=next(r for r in intra['results'] if r['strategy']==label and r['period']=='test' and r['cost_bps']==8)
        interval.append(row)
    intraday_training_winner=intra['global_training_pnl']
    intra_selected_result=next(r for r in intra['results'] if r['strategy']==intraday_training_winner and r['period']=='test' and r['cost_bps']==8)
    relevant=dict(core_training_winner=core['global_training_winner'],core_winner=core_winner,
        expanded_global_training_winner=training_winner,expanded_training_winner_later=next(r for r in univ_rows if r['strategy']==training_winner),
        observed_universe_leader=observed_universe,frequent_profile_leader=best_frequency,
        interval_btc_long_2x_q3=interval,intraday_global_training_winner_q3=intra_selected_result,
        same_period_current_winner=current,
        caveat='Historical Binance perpetual proxies with current authenticated Kalshi fee scenario. Universe current, survivor-conditioned. Direction choices motivated by prior inspected results. Chronological holdout does not erase prior access. Not a native Kalshi execution backtest.')
    (ROOT/'relevant-results.json').write_text(json.dumps(relevant,indent=2))
    leaders=sorted(univ_rows,key=lambda r:r["pnl"],reverse=True)[:3]+[core_winner]
    if best_frequency:leaders.append(best_frequency)
    leaders += [get_core('BTC-long-1x-pnl'),get_core('BTC-long-ETH-short-2x-pnl'),get_core('current-4h-2x')]
    lines=['# Relevant directional and intraday P&L results','',
        'All long-history results below start with 10,000 USDT and run continuously from January 2022 through September 2026. Returns include historical funding and modeled execution costs.','',
        '| Strategy | Net P&L | Max daily drawdown | Profitable full closes/month | Median holding hours |',
        '|---|---:|---:|---:|---:|']
    for r in leaders:lines.append(f"| {r['strategy']} | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['profitable_closes_per_month']:.2f} | {r['median_holding_hours']} |")
    lines+=['',f'Expanded-universe maximum training-P&L selection: **{training_winner}**. Highest later observed asset result: **{observed_universe["strategy"]}**. Those are distinct labels: the later observed ranking is descriptive and must not be used to rewrite training selection.','',
        'BTC/ETH recipes were selected on 2020–2021; other eligible asset recipes on 2021. Sizing and profit exits were locked before evaluating later years. Later direction hypotheses were inspired by prior inspected results, so this remains retrospective follow-up research.','',
        '## BTC and ETH decisions','',
        f"BTC long-only 1x earns {get_core('BTC-long-1x-pnl')['pnl']:,.2f}; 2x earns {get_core('BTC-long-2x-pnl')['pnl']:,.2f}. The combination earns {get_core('BTC-long-ETH-short-1x-pnl')['pnl']:,.2f} at1x and {get_core('BTC-long-ETH-short-2x-pnl')['pnl']:,.2f} at2x. ETH short-only earns {get_core('ETH-short-1x-pnl')['pnl']:,.2f} / {get_core('ETH-short-2x-pnl')['pnl']:,.2f} at1x/2x. The combination does not beat BTC long-only on absolute P&L.",
        '', '## Profit-taking frequency','',
        'The BTC P&L winner keeps the channel exit and realizes large winners infrequently. The expanded universe tests +1/+2 ATR completed-close profit triggers with a72h holding cap, alongside the channel exit. All fills happen at the next bar open with fees and slippage; triggered targets are not assumed to be profitable fills. Net completed episodes include partial-rebalance P&L, fees, and funding. Counts are averages, not payout guarantees. Withdrawals are not simulated.',
        '', 'No BTC/ETH four-hour training candidate met the frequent profile requirement of positive training net P&L and at least two profitable full closes per month. The intraday matrix also found no qualifying frequent profile in Q1 training. BNB and SOL supplied qualifying training frequency profiles in the expanded universe; the better later P&L profile is shown above. This comparison of those two profiles is descriptive after evaluation.',
        '', '## Candle resolution: same Q3 2026 test dates','',
        'These BTC-long-only2x recipes select sizing/profit exit on Q1; Q2 validates and Q3 tests. The 20day entry/10day exit horizons are preserved. Minute execution/mark data are used for every resolution.','',
        '| Signal candles | Q3 net P&L | Q3 max DD | Profitable closes/month | Training-selected sizing / TP |',
        '|---|---:|---:|---:|---|']
    for r in interval:
        cfg=r['config'];lines.append(f"| {cfg['minutes']}m | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['profitable_closes_per_month']:.2f} | {cfg['sizing']} / {cfg['tp_atr']} ATR |")
    benchmark=next(r for r in current if r['period']=='test')
    lines+=['',f"The fixed original symmetric four-hour2x winner earns {benchmark['pnl']:,.2f} on the same Q3 minute data. The intraday global training-P&L choice ({intraday_training_winner}) earns {intra_selected_result['pnl']:,.2f} in Q3; do not silently replace it using Q3 outcomes.",
        '', '## Kalshi availability and source limits','',
        'Authenticated read-only API requests confirmed perpetuals enabled,19 active crypto markets, and4bps current taker fees. The universe screen covers12 additional assets with sufficient pre-2022 history, beyond BTC/ETH. HYPE, SUI, WLD, VVV and kSHIB are excluded from the multi-year training screen because January2021 funding history is unavailable. Inactive kPEPE is excluded from the active universe.',
        '', 'Kalshi native funding coverage begins in June2026 for BTC/ETH and later for some assets, so multi-year prices/funding come from explicitly labeled Binance perpetual proxies. Current Kalshi fee and contract-size/tick snapshots inform scenarios; the fixed maintenance-margin model does not reproduce Kalshi portfolio risk tiers or historical liquidation rules. API leverage estimates are informational; a modeled2x result is not proof the account can open that exposure in a live market.',
        '', 'Primary costs are8bps per side (4bps current taker fee plus4bps assumed spread/slippage), with15/30bps stress. No measured historical quote depth, market impact, taxes, collateral yield, or withdrawal policy is modeled. The studies are not deployment qualifications.','',
        'Source protocols, training selections, costs, equity paths and episode records are retained in the subdirectories. Keys were used for GET-only requests and were not written to study artifacts.']
    (ROOT/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    # A compact chart of the P&L leaders, on matching long-history dates.
    fig,ax=plt.subplots(figsize=(11,5))
    paths=[(ROOT/'four-hour/BTC-long-2x-pnl-post_training.csv','BTC long 2x'),
           (ROOT/'four-hour/current-4h-2x-post_training.csv','Previous 4h 2x winner')]
    for directory in ('universe','universe-supplement'):
        path=ROOT/directory/f'{observed_universe["strategy"]}-post_training.csv'
        if path.exists():paths.append((path,observed_universe['strategy']))
    if best_frequency:
        for directory in ('universe','universe-supplement'):
            path=ROOT/directory/f'{best_frequency["strategy"]}-post_training.csv'
            if path.exists():paths.append((path,best_frequency['strategy']))
    for path,label in paths:
        f=pd.read_csv(path,index_col='timestamp',parse_dates=True);ax.plot(f.index,f.equity,label=label)
    ax.set_title('Continuous 2022–September 2026: P&L leaders, funding and costs included');ax.set_ylabel('USDT equity');ax.legend();fig.tight_layout();fig.savefig(ROOT/'leaders.png',dpi=150)
    shutil.copy2(__file__,ROOT/Path(__file__).name)
    print(json.dumps(dict(observed_leader=observed_universe['strategy'],training_leader=training_winner,interval=[(r['config']['minutes'],r['pnl']) for r in interval]),indent=2))

if __name__=='__main__':main()
