"""Report exactly the three frozen walk-forward candidates."""
from pathlib import Path
import hashlib
import json
import shutil
import pandas as pd

OUT=Path('outputs/crypto-doge-sol-walkforward-2026-10-19')


def main():
    out=OUT;s=json.loads((out/'summary.json').read_text());p=json.loads((out/'protocol.json').read_text())
    lines=['# Frozen DOGE/SOL walk-forward comparison','',
        '**No candidate passed the registered robustness gate. ADX14 had the highest net P&L, but its risk and training eligibility failed. No strategy is promoted to paper trading and withdrawals remain disabled.**','',
        'Seven quarterly tests cover January 2025–September 2026. Each quarter selects sizing on only its preceding 12 months, ranked at 15bps per side. Three candidates only: original four-hour exits, fixed SOL ladder, and ADX14 >=20 selecting the ladder at SOL entry. All retain 25% DOGE /75% SOL allocation and identical entry rules. All periods were previously examined: this is retrospective robustness evidence.','',
        '| Candidate | Net P&L 8bps | Net P&L 15bps | Net P&L 30bps | Max minute-close DD at15bps | Positive quarters at15bps | Worst quarter at15bps | Profitable sales/month at15bps | Profitable sale days/month at15bps |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for a in s['aggregate']:
        lines.append(f"| {a['name']} | {a['net_pnl_8']:,.2f} | {a['net_pnl_15']:,.2f} | {a['net_pnl_30']:,.2f} | {a['max_drawdown_15']:.2%} | {a['positive_quarters_15']}/7 | {a['worst_quarter_return_15']:.2%} | {a['profitable_sales_per_month_15']:.2f} | {a['profitable_days_per_month_15']:.2f} |")
    lines+=['','Start equity is10,000 USDT. P&L compounds through seven sequential quarters; quarter-boundary flattening is costed and collateral redistributed25/75. Each quarter starts flat and waits for a new signal rather than opening a position that started before the quarter. No buyback after partial reductions. These conventions differ from the previous continuous fixed-size study, so its dollar totals are not directly comparable.','',
        '## Frozen criteria','',p['selection'],p['robust_gate'],p['ranking'],'',
        'Sizing choices were fixed0.5x/1x/1.5x/2x per sleeve or daily30day volatility targets20%/30%/40%, clipped0.25x..2x. The same grid was available to all three candidates. All21 fold/candidate sizing selections were saved before evaluation. Volatility sizing was never selected.','',
        '| Test quarter | Original sizing / return | Ladder sizing / return | ADX14 sizing / return |','|---|---|---|---|']
    for f in range(7):
        chosen=next(c for c in s['selection'] if c['fold']==f)
        cells=[]
        for c in range(3):
            r=next(r for r in s['folds'] if r['candidate']==c and r['fold']==f and r['cost']==15)
            sel=next(x for x in s['selection'] if x['candidate']==c and x['fold']==f)
            cells.append(f"{r['size']} / {r['return_on_equity']:.2%}"+(' (fallback; no eligible training size)' if not sel['eligible'] else ''))
        lines.append('| '+chosen['test'][0]+' | '+' | '.join(cells)+' |')
    lines+=['','## Interpretation','',
        'ADX14 earns more than both alternatives at all three cost levels, but loses22.85% in2026Q1 and draws down31.65% peak to trough at15bps. The ladder has slightly less drawdown but loses22.85% in that quarter too. Original loses money at30bps and has33.83% drawdown. No liquidation or margin rejection occurs in any scenario.','',
        'The trailing-year sizing process favors1.5x or2x after profitable periods. This does not prevent the following quarter loss. Original and ladder have no eligible positive training sizing for2026Q2/Q3; ADX14 has none for2026Q3. The registered0.5x fallback remains invested for comparison but does not count as a successful training selection. No rules or gates were relaxed after seeing outcomes.','',
        'There are no new strategy trades in2025Q4 under the fresh-entry convention. A zero-return quarter is not counted as positive. Monthly frequency includes all calendar time and profitable natural reductions, net of allocated funding/fees; terminal quarter closes and routine resizing are excluded.','',
        '## Forward execution evidence and next gates','',
        'The standby observer reads actual Kalshi books, marks and published native funding; it submits no venue orders and creates no strategy paper fills. The raw timestamped quotes, integer contract book walks and funding are persisted in forward-observations.jsonl. Hypothetical book walks are not fill-performance evidence. Existing nine-account maximum-leverage paper campaign is unchanged and does not validate this sizing policy.','',
        'Order-book diagnostics were corrected on October20,2026: raw levels were unsorted and could contain zero-size entries. All675 stored observations were rebuilt from unchanged raw quotes. Corrected median spreads were7.07bps DOGE and2.40bps SOL. Median hypothetical1000USDT one-way costs including the modeled4bps fee were7.72–7.82bps DOGE and5.28bps SOL, with no negative cost readings. The original first-observation cost warning was invalid and is withdrawn. See quote-repair.json for side-specific medians/p95, original-file hashes and backups. Hypothetical recorded depth covered the requested integer contracts in every repaired observation; this does not establish actual fill performance, latency or capacity at maximum leverage.','',
        'Paper promotion remains blocked by the frozen historical gate. If a candidate qualifies under a separately registered future study, the forward gate is: '+p['forward_gate'],
        'Withdrawals are separately specified in withdrawal-protocol.json and are not simulated before the strategy qualification and forward gates pass.','',
        '## Verification and limits','',
        '54 targeted tests passed, including copied-wallet parity, non10,000 compounded capital reconciliation, daily sizing causality, no buying back partial sales, fresh-entry boundaries, selection constraints, ADX and underlying perpetual accounting. Source hashes were frozen before fitting and verified after replay.','',
        'Historical prices, marks and funding remain Binance proxies with Kalshi multiplier/tick and4bps fee assumptions. Other per-side costs model spread/slippage. No historical quote depth or capacity is measured; maintenance and liquidation tiers are approximations. All historical dates/assets were previously inspected. Quarter selection does not erase prior research selection bias. Fixed training draws and seven test quarters are a small sample.']
    (out/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    for name,sha in p['source_sha256'].items():assert hashlib.sha256((Path('src/autoresearch')/name).read_bytes()).hexdigest()==sha,name
    withdrawal=dict(status='disabled',reason='No historical robust candidate; no forward qualification',activation='Only after historical robust gate and frozen forward gate pass',separate_experiment=True,accounts=['identical no-withdrawal control','monthly20percent eligible profit withdrawal'],rule='At each UTC month end withdraw20% of positive cumulative net realized strategy profits above the previous realized-profit high-water mark; cap by excess cash after initial/maintenance margin and a30% collateral reserve. Never withdraw principal; no withdrawal after a losing month. Use actual contract rounding and charge any required reduction costs.',comparison='Terminal equity plus cumulative withdrawals, dollar max drawdown, margin headroom, payout months and amounts, total costs. Keep signal/exit/sizing identical. No optimization of withdrawal fraction after results.',funding_and_fees='All realized profits net of entry/exit fees and allocated native funding; unrealized P&L never considered distributable profit')
    (out/'withdrawal-protocol.json').write_text(json.dumps(withdrawal,indent=2))
    verification=dict(targeted_tests='54 passed in1.40s',source_hashes_verified=len(p['source_sha256']),training_replays=147,test_replays=63,summary_sha256=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest(),all_portfolio_and_wallet_identities_passed=True,protocol_frozen_before_data_load=True)
    (out/'verification.json').write_text(json.dumps(verification,indent=2));shutil.copy2(__file__,out/Path(__file__).name)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
    for c in s['aggregate']:
        eq=pd.read_csv(out/f"c{c['candidate']}-15-equity.csv",index_col='timestamp',parse_dates=True).equity
        axes[0].plot(eq.index,eq,label=c['name']);axes[1].plot(eq.index,(eq/eq.cummax().clip(lower=10000)-1)*100)
    axes[0].set_title('Frozen exits | Rolling training-only sizing | 15bps per side + funding');axes[0].set_ylabel('USDT equity');axes[0].legend();axes[1].set_ylabel('Hourly-close drawdown %');fig.tight_layout();fig.savefig(out/'comparison.png',dpi=150);plt.close(fig)

if __name__=='__main__':main()
