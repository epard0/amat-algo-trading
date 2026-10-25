"""Render compact P&L findings from the frozen historical perpetual study."""
from pathlib import Path
import json
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=Path('outputs/crypto-perpetual-long-short-2026-10-19')
    summary=json.loads((p/'summary.json').read_text())
    results=summary['results']
    def get(strategy,mode='native',cost=None,period='post_training'):
        cost=cost if cost is not None else (15 if strategy.startswith('spot') else 8)
        return next(r for r in results if r['strategy']==strategy and r['mode']==mode and r['cost_bps']==cost and r['period']==period)
    names=['4h-2x','1h-2x','spot-4h','spot-1h','4h-1x','1h-1x']
    lines=['# Historical BTC/ETH perpetual P&L findings','',
        'Completed October 19, 2026. The maximum training P&L selection and maximum later continuous P&L among tested selections are both the four-hour, volatility-sized 2x long/short breakout. This is a research result, not a deployment qualification.','',
        '## Native P&L: continuous January 2022–September 2026','',
        'Every portfolio starts with 10,000 USDT; no annual resets. Perpetuals include actual funding rates and 8bps/side modeled costs; spot uses 15bps/side. The 1x and 2x rows use different training-selected sizing rules, so this is not an isolated test of leverage alone.','',
        '| Strategy | Net P&L | Total return | Annualized return | Realized volatility | Max daily drawdown | Sharpe |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name in names:
        r=get(name)
        lines.append(f"| {name} | {r['pnl']:,.2f} | {r['total_return']:.2%} | {r['cagr']:.2%} | {r['annualized_volatility']:.2%} | {r['max_drawdown']:.2%} | {r['sharpe']:.2f} |")
    winner=get('4h-2x')
    lines+=['','## Selected policy','',
        'Training used 2020–2021, with 48 predefined candidates ranked by net training P&L. Four-hour/2x/vol20 won, using a 20-day high entry for longs, symmetric low entry for shorts, and a 10-day exit. No later-period retuning occurred. One winner was locked for each timeframe/leverage group.','',
        'Each asset starts with 5,000 USDT in an independent wallet. Native target gross asset exposure is `1.96 * min(1, 0.20 / trailing_30day_asset_volatility)` when a signal is active. The 2x label is a multiplier/cap, not constant 2x investment. The winner averaged approximately 0.57x gross portfolio exposure on later data, including cash periods.','',
        f"The winner's actual funding cash flow was {winner['funding_pnl']:,.2f} USDT and commission fees were {winner['fees']:,.2f} USDT. Slippage is already included in price P&L. All selected later-period replays had zero simulated liquidations and zero margin rejections; this does not establish intrabar survival under the exact venue engine.",
        '', '## Cost sensitivity: winner','',
        '| Perpetual cost per side | Continuous native net P&L | Max daily drawdown |','|---|---:|---:|']
    for cost in (8,15,30):
        r=get('4h-2x',cost=cost)
        lines.append(f"| {cost}bps | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} |")
    lines+=['','At equal 15bps/side costs, four-hour perpetual P&L is 4,188.09 USDT versus 4,331.78 USDT for four-hour spot. Its primary-cost P&L lead is not robust to that execution-cost scenario.','',
        '## Training-risk-calibrated comparison','',
        'All strategies were separately scaled down toward 12% annualized daily portfolio volatility using 2020–2021 only, then frozen. Later realized volatility diverges substantially; these paths must not be described as equal realized risk.','',
        '| Strategy | Net P&L after training scaling | Later realized volatility | Max daily drawdown | Sharpe |',
        '|---|---:|---:|---:|---:|']
    for name in ('4h-2x','1h-2x','spot-4h','spot-1h'):
        r=get(name,'matched')
        lines.append(f"| {name} | {r['pnl']:,.2f} | {r['annualized_volatility']:.2%} | {r['max_drawdown']:.2%} | {r['sharpe']:.2f} |")
    lines+=['',
        'Four-hour perpetuals make 369.81 USDT more than their scaled spot control, but take about 73% more realized volatility. Their paired annualized arithmetic excess-mean estimate is +1.07 percentage points, with 95% interval [-6.92, +9.09] points. No statistically demonstrated edge follows.','',
        'A separate hindsight statistical normalization sets both native return series to exactly 10% sample realized volatility: annual arithmetic mean is 4.39% for four-hour perpetuals versus 6.74% for four-hour spot. Hourly figures are 4.14% versus 6.46%. This is a return-per-unit-risk diagnostic using the full sample, not an executable allocation or hypothetical backtested trading P&L. Spot has the better Sharpe in both comparisons.','',
        '## Where the winner made money','',
        'Net cash-flow attribution from the continuous native replay, including allocated opening/closing fees and actual funding. Removing a leg changes later sizing and equity, so these are contributions to the tested strategy, not standalone strategy backtests.','',
        '| Asset | Long net P&L | Short net P&L | Total |','|---|---:|---:|---:|']
    attribution=json.loads((p/'direction-attribution.json').read_text())
    for record in attribution:
        if record['path']=='4h-2x-post_training-8bps-native-ledger.json':
            a=record['attribution']; lo=a['long']['net_pnl']; sh=a['short']['net_pnl']
            lines.append(f"| {record['symbol']} | {lo:,.2f} | {sh:,.2f} | {lo+sh:,.2f} |")
    lines+=['','BTC longs were the largest positive contribution; BTC shorts were a material drag. ETH shorts helped. These are descriptive later-period findings, not a newly selected asymmetric strategy.','',
        '## Evidence and limits','',
        '486 monthly archives plus 12 daily mark-price supplements were verified against published checksums. There are 59,160 hourly observations per asset and 7,395 actual funding events per asset. Missing funding is fatal rather than filled with zeros. One zero-volume trade hour per asset blocks fills and signal-candle validity.','',
        'Funding is applied before same-hour orders, using hourly boundary marks as event-price proxies. Hourly adverse mark extremes drive conservative liquidation checks. Fixed fee/margin/tick assumptions do not reconstruct historical venue risk tiers, quotes, market impact, ADL, or insurance-fund behavior. Balances are USDT and cash earns zero.','',
        'The training period is short and bull-heavy. Later spot-market regimes were previously inspected in this workspace. Temporal holdout and paired fixed-policy intervals do not eliminate prior access or strategy-search multiplicity.','',
        'Decision: retain four-hour, volatility-sized 2x as the highest-P&L research candidate among tested selections. It has not demonstrated superior performance at equal risk or justified promotion.']
    (p/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    fig,axes=plt.subplots(2,1,figsize=(12,8))
    labels={'4h-2x':'4h perpetual, 2x volatility-sized','1h-2x':'1h perpetual, 2x volatility-sized','spot-4h':'4h spot','spot-1h':'1h spot'}
    for ax,mode in zip(axes,('native','matched')):
        for name in labels:
            cost=15 if name.startswith('spot') else 8
            frame=pd.read_csv(p/f'{name}-post_training-{cost}bps-{mode}.csv',index_col='timestamp',parse_dates=True)
            ax.plot(frame.index,frame.equity,label=labels[name])
        ax.set_title('Continuous January 2022–September 2026\n'+('Native exposure' if mode=='native' else '12% training-volatility target; later realized risk differs'), fontsize=12)
        ax.set_ylabel('USDT equity');ax.legend(ncol=2)
    fig.tight_layout();fig.savefig(p/'pnl-comparison.png',dpi=150)

if __name__=='__main__':main()
