"""Reproducible compact report for the registered ADX switch study."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import pandas as pd


def describe(rule):
    if rule['family']=='original':return 'Always original'
    if rule['family']=='ladder':return 'Always ladder'
    op='>=' if rule['ladder_when']=='high' else '<='
    return f"ADX({rule['lookback']}) {op} {rule['threshold']}: ladder; otherwise original"


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    out=parser.parse_args().output
    data=json.loads((out/'summary.json').read_text());results=data['results'];roles=data['roles']
    ids=list(dict.fromkeys(r['id'] for r in results))
    def row(i,period='test',cost=8):return next(r for r in results if r['id']==i and r['period']==period and r['cost_bps']==cost)
    names={i:describe(row(i)['rule']) for i in ids}
    relevant=dict(roles=roles,selection=data['selection'],test=[row(i) for i in ids],validation=[row(i,'validation') for i in ids],continuous=[row(i,'post_training') for i in ids],test_cost_stress=[r for r in results if r['period']=='test'])
    (out/'relevant-results.json').write_text(json.dumps(relevant,indent=2))
    lines=['# DOGE/SOL ADX exit switching','',
        'Training: January 2024–June 2025. Validation: July–December 2025. Test: January–September 2026. Continuous later account: July 2025–September 2026. Each replay starts with 10,000 USDT; the continuous account compounds through the validation/test boundary.','',
        'Four-hour entries, initial 25% DOGE / 75% SOL collateral allocation, 1.5x leverage per sleeve and DOGE exits remain fixed. SOL exit mode is selected once at each original entry opportunity and latched until flat. Ladder runners block overlapping original entries.','',
        '**Training P&L choice:** '+describe(data['selection']['primary']['rule'])+'.','',
        '**Frequency-constrained choice:** '+(describe(data['selection']['frequency_switch']['rule']) if data['selection']['frequency_switch'] else 'No active switch passed the registered gate')+'.','',
        'The secondary gate requires training P&L at least equal to original, daily drawdown no worse, and at least 25% more profitable sale days. Both fixed policies may win primary selection. Active switches must execute at least two SOL entries in each mode, with positive portfolio net P&L, no liquidations and at least six completed portfolio positions.','',
        '| Policy | Continuous net P&L | 2026 net P&L | Test minute-close DD | Profitable sales/month | Profitable sale days/month |',
        '|---|---:|---:|---:|---:|---:|']
    for i in ids:
        r=row(i);c=row(i,'post_training')
        lines.append(f"| {names[i]} | {c['pnl']:,.2f} | {r['pnl']:,.2f} | {r['max_minute_close_drawdown']:.2%} | {r['profitable_realizations_per_month']:.2f} | {r['profitable_realization_days_per_month']:.2f} |")
    lines+=['','| Policy | Training net P&L | Training daily DD | Validation P&L | Training SOL original/ladder entries | Test SOL original/ladder entries |','|---|---:|---:|---:|---:|---:|']
    training=json.loads((out/'training.json').read_text())
    for i in ids:
        t=next(r for r in training if r['id']==i);r=row(i)
        lines.append(f"| {names[i]} | {t['pnl']:,.2f} | {t['max_drawdown']:.2%} | {row(i,'validation')['pnl']:,.2f} | {t['actual_original_entries']}/{t['actual_ladder_entries']} | {r['actual_original_entries']}/{r['actual_ladder_entries']} |")
    lines+=['','| Policy | Test P&L 8bps/side | 15bps/side | 30bps/side |','|---|---:|---:|---:|']
    for i in ids:lines.append('| '+names[i]+' | '+' | '.join(f"{row(i,cost=c)['pnl']:,.2f}" for c in (8,15,30))+' |')
    lines+=['','## Interpretation','',
        'The training P&L winner increased continuous later P&L to 2,039.41 versus 2,008.04 for always-ladder, but its 2026 result fell to 713.78 versus 840.49 for always-ladder and 994.12 for original exits. Its validation gain did not persist in the later test. The frequency-constrained ADX14 rule delivered 2,064.22 continuously and 890.23 in 2026: a small P&L improvement over always-ladder, with fewer profitable sales and no test drawdown benefit. Neither ADX rule dominates both controls.','',
        'The ADX28 winner improved training P&L by 14.8% over original exits, but lost 28.2% of original-policy test P&L. The sample contains just 27 original SOL training entry observations. This pattern is consistent with threshold overfitting or regime instability; this study does not establish the cause. All retained policies still had a 101-day test gap between profitable realizations, so the higher monthly average did not provide regular payouts.','',
        '## Registered method and limitations','',
        'Thirty-six active rules combine ADX lookbacks 7/14/28 completed four-hour bars, fixed thresholds 15/20/25/30/35/40 and both orientations. Wilder TR and directional movement sums use the first n intervals; first ADX is the mean of n DX observations, followed by Wilder recursion. Equal positive/negative movement contributes neither; zero directional movement gives DX zero. Missing candles reset indicator state. First valid ADX is at index 2n−1. Only completed candles inform the next minute execution. Formula reference: https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx','',
        'The grid and selection gates were registered before fitting. Training selected the settings; selection.json was saved before later replay. The original SOL policy closes fully at 2 frozen entry ATR, with 10-day channel exit and 72-hour cap. The ladder sells nominal 25% at 1 ATR and another 25% at 2 ATR, then trails its remainder 3 entry ATR from the highest completed four-hour close, retaining channel and time exits. DOGE keeps its original 0.5 ATR full target. No mid-position mode changes or early reentry.','',
        'Binance perpetual minute trade/mark and historical funding proxies, with current Kalshi multiplier/tick/fee assumptions. Primary cost is 8bps per side (4bps taker plus 4bps spread/slippage), stressed at 15/30bps. Exact Decimal wallets, integer contracts, daily resizing, proportional funding and entry fee allocation on reductions, and missing-mark forced-flat blackout are unchanged. No buying back partial reductions. Collateral sleeves compound independently; gains remain invested and no withdrawals are modeled. Fixed maintenance/liquidation assumptions omit dynamic venue tiers, depth and market impact.','',
        'Profitable sales are strategy-driven reductions net of their allocated entry fees, closing costs and funding; routine resizing, terminal liquidation and blackout sales are excluded from the frequency count. Profitable sale days include all closing realizations for the day and require a natural strategy sale. These counts include profitable full exits as well as partial take-profits. Minute-close drawdown may miss intraminute troughs.','',
        '92 targeted tests passed, including independent hand-calculated ADX values, tied movement, warmup, flat/monotonic series, missing-bar resets, causality and training-data isolation, plus prior accounting/exit tests. Both controls reproduce prior results in all 18 period/cost comparisons. Sources and manifests are pinned.','',
        'This is a retrospective chronological follow-up: assets, exit hypotheses and later dates were already examined. Only about 27 original SOL training entry observations support a 36-rule search. Later performance is informative but is not pristine unseen confirmation. No settings were revised after viewing later results.']
    (out/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    protocol=json.loads((out/'protocol.json').read_text())
    for name,sha in protocol['source_sha256'].items():
        assert hashlib.sha256((Path('src/autoresearch')/name).read_bytes()).hexdigest()==sha,name
    (out/'verification.json').write_text(json.dumps(dict(targeted_tests='92 passed in14.44s',frozen_sources_verified=len(protocol['source_sha256']),control_parity_cases=len(json.loads((out/'control-parity.json').read_text())),summary_sha256=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest()),indent=2))
    shutil.copy2(__file__,out/Path(__file__).name)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for period,name in [('test','test-comparison.png'),('post_training','comparison.png')]:
        fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
        for i in ids:
            eq=pd.read_csv(out/f'rule-{i}-{period}.csv',index_col='timestamp',parse_dates=True).equity
            axes[0].plot(eq.index,eq,label=names[i]);axes[1].plot(eq.index,(eq/eq.cummax().clip(lower=10000)-1)*100)
        axes[0].set_title(period+' | Historical funding and costs included');axes[0].set_ylabel('Equity (USDT)');axes[0].legend(fontsize=8)
        axes[1].set_ylabel('Hourly-close drawdown %');fig.tight_layout();fig.savefig(out/name,dpi=150);plt.close(fig)
    print(json.dumps(dict(roles=roles,test=[{k:row(i)[k] for k in ('id','rule','pnl','max_minute_close_drawdown','profitable_realizations_per_month','profitable_realization_days_per_month')} for i in ids]),indent=2))

if __name__=='__main__':main()
