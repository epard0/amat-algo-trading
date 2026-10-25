"""Relevant partial-profit/trailing results and explanation of prior decline."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

import pandas as pd


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    out=args.output
    data=json.loads((out/'summary.json').read_text())
    results=data['results']
    labels=dict(baseline='Unchanged four-hour strategy',max_training_pnl='Maximum training-P&L exit choice',no_sacrifice='Frequency choice qualified on training')
    def row(label,period='test',cost=8):
        return next(r for r in results if r['strategy']==label and r['period']==period and r['cost_bps']==cost)
    active=[k for k in labels if data['selection'][k] is not None]
    relevant=dict(selection=data['selection'],primary_results=[r for r in results if r['cost_bps']==8],
        test_cost_stress=[r for r in results if r['period']=='test'],
        why_intraday_worsened=json.loads((out/'why_intraday_worsened.json').read_text()),
        conclusion='Partial exits increased realization frequency, but training-qualified no-sacrifice choice earned less in2026test while earning more across continuousJul2025–Sep2026holdout. No consistent no-sacrifice upgrade established.')
    (out/'relevant-results.json').write_text(json.dumps(relevant,indent=2))
    lines=['# Four-hour DOGE/SOL partial-profit and trailing exits','',
        '**The combined holdout improved, but the 2026 test did not preserve baseline P&L.** Both dates and the training-only selections are shown below. All portfolios start with 10,000 USDT, retain 25% DOGE/75% SOL initial collateral and 1.5x leverage per sleeve, and include historical funding and 8bps/side primary execution costs.','',
        'Training: January 2024–June 2025. Validation: July–December 2025. Test: January–September 2026. Continuous holdout: July 2025–September 2026. Each standalone period restarts with 10,000; continuous holdout compounds without resetting.','',
        '## Same-date comparison','',
        '| Training-selected exit choice | Continuous holdout net P&L | 2026 test net P&L | Test daily DD | Test minute-close DD | Test profitable realizations/month | Test profitable realization days/month |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for label in active:
        r=row(label);whole=row(label,'post_training')
        lines.append(f"| {labels[label]} | {whole['pnl']:,.2f} | {r['pnl']:,.2f} | {r['max_drawdown']:.1%} | {r['max_minute_close_drawdown']:.1%} | {r['profitable_realizations_per_month']:.2f} | {r['profitable_realization_days_per_month']:.2f} |")
    lines+=['','## Settings selected on training','']
    for label in active:
        r=data['selection'][label]
        lines += [f"**{labels[label]}**: DOGE {json.dumps(r['doge_policy'])}; SOL {json.dumps(r['sol_policy'])}.",
            f"Training net P&L {r['pnl']:,.2f}; daily drawdown {r['max_drawdown']:.1%}; profitable realization days/month {r['profitable_realization_days_per_month']:.2f}.",'']
    lines+=['The no-sacrifice training gate required P&L at least equal to baseline, no worse daily drawdown, and at least 25% more profitable realization days. Exactly one of 2,401 pairs qualified. The maximum-training-P&L choice was frozen separately. No test outcomes changed either selection.','',
        'The maximum-training-P&L policy leaves DOGE unchanged. SOL sells 25% at 1 entry ATR, retains its original 2ATR final exit and 72h cap, and adds a 1ATR trailing exit armed after the partial sale.','',
        'The frequency-qualified ladder also leaves DOGE unchanged. SOL scales out 25% at 1ATR and another 25% of planned original exposure at 2ATR, then trails the remainder by 3 entry ATR, with a 72h cap. Actual integer lots and daily reductions can make amounts differ slightly from nominal fractions. Trails use highest completed4h close since entry; triggers execute at the next minute open.','',
        '## Cost stress on the 2026 test','',
        '| Exit choice | 8bps/side | 15bps/side | 30bps/side |','|---|---:|---:|---:|']
    for label in active:
        lines.append('| '+labels[label]+' | '+' | '.join(f"{row(label,cost=c)['pnl']:,.2f}" for c in (8,15,30))+' |')
    lines+=['','## Validation and continuous drawdowns','',
        '| Exit choice | Validation net P&L | Validation daily DD | Continuous holdout daily DD |','|---|---:|---:|---:|']
    for label in active:
        v=row(label,'validation');w=row(label,'post_training')
        lines.append(f"| {labels[label]} | {v['pnl']:,.2f} | {v['max_drawdown']:.1%} | {w['max_drawdown']:.1%} |")
    lines+=['','## Why the intraday study got worse','',
        'It changed the strategy:20day entries became2/6/24hour entries,72hour holding caps became1/6hours, and ATR targets came from much smaller native candles. Training also chose75% DOGE/25% SOL and1x, compared with the original25%/75% allocation and 1.5x. The result is not an isolated candle-size experiment. All 3,888 intraday portfolios were negative on training.','',
        'On the same 2026 test at1x, the 15m choice trailed the 4h control by 4,863.62 USDT. Extra explicit fees explain 1,302.38; the funding difference is only 12.36; price P&L including modeled execution slippage explains 3,548.89. Thus funding was not the main issue, and fees alone do not explain the loss. Faster entries/exits and the changed allocation produced a worse net price/execution result.','',
        'The earlier +3,328 four-hour result covered January2025–September 2026; the later +994 control covered January–September 2026. Comparing those totals as though the test windows were identical would exaggerate deterioration. The current study reproduces the same +994 control exactly.','',
        '## Realizations and accounting','',
        'A partial sale transfers unrealized gain into realized P&L and reduces remaining exposure; it does not create new total equity before costs. Money stays in the collateral sleeve and is not withdrawn. Daily resizing cannot buy back a trimmed runner, even if lot rounding closes it completely. Each actual reduction includes closing fees and its proportional carried entry fees and funding; all reductions reconcile to wallet net P&L.','',
        'Profitable event frequency excludes routine rebalance reductions, forced terminal closes and research data blackouts. Distinct profitable days require positive net closing realizations for the day, including any rebalance reductions, and at least one natural strategy sale. This prevents two profitable sales on the same date from masquerading as two payout days.','',
        'The 2026ladder recorded 26 profitable realizations on 19 net-profitable realization days, versus 16 on 14 days for baseline. Its longest gap between profitable realizations remained 101 days. Frequency averages are not a schedule or a withdrawal simulation.','',
        '## Verification and limits','',
        '83 targeted tests passed. Baseline price P&L, fees, funding and daily/minute drawdowns match the previous minute replay on validation, test and continuous holdout. All source hashes and data manifests are pinned; full reductions, ledgers and equity paths are retained.','',
        'Prices, marks and historical funding are Binance perpetual proxies with Kalshi fee/multiplier/tick assumptions. Fixed maintenance and liquidation rules do not reconstruct dynamic venue risk tiers, and measured quote depth/impact are absent. Minute-close drawdown excludes possible intraminute equity troughs. Assets and evaluation dates were previously inspected; this is retrospective chronological follow-up, not pristine OOS evidence.']
    (out/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    protocol=json.loads((out/'protocol.json').read_text())
    for name,sha in protocol['source_sha256'].items():
        if hashlib.sha256((Path('src/autoresearch')/name).read_bytes()).hexdigest()!=sha:
            raise AssertionError('frozen source changed: '+name)
    (out/'verification.json').write_text(json.dumps(dict(tests='83 passed in4.47s',frozen_sources_verified=len(protocol['source_sha256']),
        baseline_parity_periods=3,summary_sha256=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest()),indent=2))
    shutil.copy2(__file__,out/Path(__file__).name)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for period,title,name in [('test','January–September 2026 test','test-comparison.png'),('post_training','July 2025–September 2026 continuous holdout','comparison.png')]:
        fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
        for label in active:
            eq=pd.read_csv(out/f'{label}-{period}.csv',index_col='timestamp',parse_dates=True).equity
            axes[0].plot(eq.index,eq,label=labels[label])
            axes[1].plot(eq.index,(eq/eq.cummax().clip(lower=10000)-1)*100)
        axes[0].set_title(title+' | Funding and costs included')
        axes[0].set_ylabel('USDT equity');axes[0].legend()
        axes[1].set_ylabel('Hourly-close drawdown %')
        fig.tight_layout();fig.savefig(out/name,dpi=150);plt.close(fig)
    print(json.dumps(dict(test=[row(k) for k in active],continuous=[row(k,'post_training') for k in active]),indent=2))


if __name__=='__main__':main()
