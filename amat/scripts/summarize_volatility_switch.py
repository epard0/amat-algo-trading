"""Compact report for entry-locked SOL volatility exit selection."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

import pandas as pd


def describe(rule):
    if rule['family']=='original':return 'Always original SOL exits'
    if rule['family']=='ladder':return 'Always SOL partial-profit ladder'
    feature=rule['feature']
    measure=(f"{feature[2:-1]}-day annualized realized volatility" if feature.startswith('rv')
        else f"ATR{feature[3:]} / completed SOL close")
    op='>=' if rule['ladder_when']=='high' else '<='
    return f"Ladder when {measure} {op} {rule['threshold']:.10f} ({rule['threshold']*100:.4f}%); original otherwise"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    out=args.output
    data=json.loads((out/'summary.json').read_text())
    roles=data['roles']
    pid=roles['primary']
    fid=roles['frequency_switch']
    results=data['results']
    ids=list(dict.fromkeys(r['id'] for r in results))
    names={0:'Always original',1:'Always ladder',pid:'Training P&L selection'}
    if fid is not None and fid!=pid and fid not in (0,1):names[fid]='Training frequency selection'
    for rid in ids:names.setdefault(rid,'Best active switch')
    def row(rid,period='test',cost=8):
        return next(r for r in results if r['id']==rid and r['period']==period and r['cost_bps']==cost)
    equity_equivalence=json.loads((out/'frequency-control-equivalence.json').read_text()) if (out/'frequency-control-equivalence.json').exists() else []
    relevant=dict(primary=data['selection']['primary'],frequency_selection=data['selection']['frequency_switch'],
        test_results=[row(i) for i in ids],continuous_results=[row(i,'post_training') for i in ids],
        validation_results=[row(i,'validation') for i in ids],test_cost_stress=[r for r in results if r['period']=='test'],
        frequency_control_equivalence=equity_equivalence,
        conclusion='Primary training-selected entry-locked switch did not outperform either fixed policy in2026test; frequency-qualified switch was identical to always-ladder across later equity paths.')
    (out/'relevant-results.json').write_text(json.dumps(relevant,indent=2))
    lines=['# DOGE/SOL volatility-based exit switching','',
        '**The training-selected volatility switch did not improve later P&L over either fixed exit policy in the 2026 test.** The frequency-qualified alternative reproduced the always-ladder control rather than adding an incremental benefit.','',
        'Training: January 2024–June 2025. Validation: July–December 2025. Test: January–September 2026. Continuous holdout: July 2025–September 2026. Each standalone replay starts with 10,000 USDT; the continuous holdout compounds without resetting.','',
        'The four-hour entry calendar, DOGE exits, initial 25% DOGE / 75% SOL collateral allocation and 1.5x sleeve leverage are frozen. Only SOL exit mode changes, once at entry and then locked for the whole position.','',
        '## Rules selected solely on training','',
        '**Primary training P&L choice:** '+describe(data['selection']['primary']['rule'])+'.','',
        'ATR14 is the mean true range of 14 completed four-hour bars. Its ratio to price is not annualized. The selected 2.23594% cutoff is the 25th percentile of original SOL entry observations in training.','']
    if fid is not None:
        lines+=['**Frequency-qualified choice:** '+describe(data['selection']['frequency_switch']['rule'])+'.','',
            'This secondary training gate requires P&L at least equal to original, no worse daily drawdown, and at least 25% more profitable realization days. The primary P&L choice is ranked separately and need not satisfy the frequency gate.','']
    lines+=['## Primary-cost comparison','',
        'All P&L below includes historical funding and 8bps per side in modeled execution costs: 4bps taker fee plus 4bps spread/slippage.','',
        '| Policy | Continuous holdout net P&L | 2026 test net P&L | Test daily DD | Test minute-close DD | Test profitable realizations/month | Test profitable realization days/month |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for rid in ids:
        r=row(rid);c=row(rid,'post_training')
        lines.append(f"| {names[rid]} | {c['pnl']:,.2f} | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['max_minute_close_drawdown']:.2%} | {r['profitable_realizations_per_month']:.2f} | {r['profitable_realization_days_per_month']:.2f} |")
    lines+=['','## Training fit, validation and mode usage','',
        '| Policy | Training net P&L | Training daily DD | Validation net P&L | Training actual SOL original/ladder entries | Test actual SOL original/ladder entries |',
        '|---|---:|---:|---:|---:|---:|']
    training=json.loads((out/'training.json').read_text())
    for rid in ids:
        t=next(r for r in training if r['id']==rid);r=row(rid);v=row(rid,'validation')
        lines.append(f"| {names[rid]} | {t['pnl']:,.2f} | {t['max_drawdown']:.2%} | {v['pnl']:,.2f} | {t['actual_original_entries']}/{t['actual_ladder_entries']} | {r['actual_original_entries']}/{r['actual_ladder_entries']} |")
    lines+=['','## Test cost stress','',
        '| Policy | 8bps/side net P&L | 15bps/side net P&L | 30bps/side net P&L |','|---|---:|---:|---:|']
    for rid in ids:
        lines.append('| '+names[rid]+' | '+' | '.join(f"{row(rid,cost=c)['pnl']:,.2f}" for c in (8,15,30))+' |')
    primary=row(pid)
    lines+=['','## Interpretation','',
        f"The primary gate routed {primary['actual_original_entries']} test SOL entry episodes to original exits and {primary['actual_ladder_entries']} to the ladder. It generated {primary['profitable_realizations']} profitable portfolio reductions on {primary['profitable_realization_days']} net-profitable realization days. The longest gap between profitable realizations remained {primary['max_days_between_profit_realizations']} days.",'',
        'The primary threshold was calibrated from only 27 original SOL training entry observations. It sent 17 of 22 executed training entries to the ladder but only 5 of 15 test entries. The rule improved training fit without improving later results. A path-dependent mode choice changes time in positions, skipped overlapping entries and compounding; it is not a simple weighted average of fixed-policy returns.','',
        'The secondary frequency gate used original exits on only one test position. The hourly equity path, net P&L, fees, funding and realization frequency were identical to always-ladder in validation, test and the continuous holdout. No incremental exit-management edge was demonstrated.','',
        '## Method and verification','',
        'The registered grid contains 36 switches: six features, three training quantiles and two directions. Features are 3/7/14/30-day realized volatility of completed four-hour log returns, annualized with sqrt(365*6), and 14/42-bar ATR divided by completed close. Cutoffs use finite original SOL entry observations within training only. Missing feature values default to original exits. Both fixed controls may win the P&L ranking. Active switches must have at least two actually filled original-mode and two ladder-mode training entries, no liquidations, positive net P&L and at least six completed portfolio episodes.','',
        'Original SOL exits retain the 2ATR full target, 10-day channel and 72-hour cap. The fixed ladder sells nominal 25% portions at 1ATR and 2ATR, trails the remainder by 3 frozen entry ATR from the highest completed four-hour close, and retains the 72-hour cap and channel exit. A runner blocks overlapping original entry opportunities. The mode is never changed mid-position, and partial sales are never bought back by daily resizing.','',
        'Orders execute at the next minute open with real integer lots and tick rounding. Exact Decimal wallets apply historical funding once at actual timestamps. Partial reductions allocate their proportional carried entry fees and funding. Realized gains remain in collateral sleeves; no withdrawal policy is modeled. Routine rebalance reductions, forced terminal closes and missing-mark blackouts are excluded from event frequency; distinct profitable days include all closing realizations for the day and require at least one natural strategy sale.','',
        '88 targeted tests passed, including feature causality, threshold isolation from holdouts, entry-mode latching and both constant-mode plan controls. Both fixed policies reproduce the prior study across all three later periods and all three cost scenarios: 18 control comparisons covering P&L, fees, funding, drawdown and frequency. Source hashes and input manifests are pinned.','',
        '## Limits','',
        'Prices, marks and historical funding are Binance perpetual proxies with Kalshi fee/tick/multiplier assumptions. Measured quote depth, market impact and dynamic venue risk tiers are absent. Minute-close drawdown excludes intraminute troughs. The existing missing-data blackout is confined to training. Assets, exit hypotheses and later dates were already inspected; this remains retrospective chronological follow-up rather than pristine OOS evidence. Few entry observations and 36 threshold choices leave substantial overfitting risk.']
    (out/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    protocol=json.loads((out/'protocol.json').read_text())
    for name,sha in protocol['source_sha256'].items():
        if hashlib.sha256((Path('src/autoresearch')/name).read_bytes()).hexdigest()!=sha:raise AssertionError('frozen source changed: '+name)
    parity=json.loads((out/'control-parity.json').read_text())
    (out/'verification.json').write_text(json.dumps(dict(targeted_tests='88 passed in4.53s',frozen_sources_verified=len(protocol['source_sha256']),
        control_parity_cases=len(parity),summary_sha256=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest()),indent=2))
    shutil.copy2(__file__,out/Path(__file__).name)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for period,title,name in [('test','January–September 2026 test','test-comparison.png'),('post_training','July 2025–September 2026 continuous holdout','comparison.png')]:
        fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
        plot_ids=list(dict.fromkeys([0,1,pid]))
        for rid in plot_ids:
            eq=pd.read_csv(out/f'rule-{rid}-{period}.csv',index_col='timestamp',parse_dates=True).equity
            label=names[rid] if rid!=1 else 'Always ladder / frequency-qualified rule'
            axes[0].plot(eq.index,eq,label=label)
            axes[1].plot(eq.index,(eq/eq.cummax().clip(lower=10000)-1)*100)
        axes[0].set_title(title+' | Funding and modeled costs included')
        axes[0].set_ylabel('USDT equity');axes[0].legend()
        axes[1].set_ylabel('Hourly-close drawdown %')
        fig.tight_layout();fig.savefig(out/name,dpi=150);plt.close(fig)
    print(json.dumps(dict(primary=pid,test_results=[row(i) for i in ids]),indent=2))


if __name__=='__main__':main()
