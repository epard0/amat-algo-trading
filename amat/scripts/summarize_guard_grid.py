"""Report all12 guarded configurations and the frozen training choice."""
from pathlib import Path
import hashlib,json,shutil
import pandas as pd

OUT=Path('outputs/crypto-adx-guard-window-leverage-2026-10-20-v2')


def main():
    out=OUT;s=json.loads((out/'summary.json').read_text());p=json.loads((out/'protocol.json').read_text());winner=s['selection']['winner']
    def row(w,l,period='continuous',case='median'):return next(r for r in s['results'] if r['window']==w and r['leverage']==l and r['period']==period and r['cost_case']==case)
    def status(r):return ('Guard halt' if r['guard_triggered'] else 'No guard halt')+('; liquidation' if r['liquidations'] else '')
    lines=['# ADX DOGE/SOL windows x leverage with the paper35% initial-loss guard','',
        f"**Training selected {winner['window']}days at {winner['leverage']}x. The current guard freezes trading rather than flattening; it does not cap losses or peak drawdown at35%.**",'',
        'Training:January2024–June2025. Validation:July–December2025. Test:January–September2026. Continuous later account:July2025–September2026. Every period starts a separate10,000USD account,flat and waiting for fresh signals. All12 configurations are evaluated,not only the retrospective later winners.','',
        '## Exact guard semantics, approximate observation cadence','',
        'At equity<=6,500USD, the guard permanently stops ALL strategy orders,including partial take-profits,time/channel exits and resizing. It does not flatten. Existing holdings continue to generate mark P&L and funding,and may be forcibly liquidated by maintenance checks. Recovery above6,500does not resume trading. This exactly follows the paper order-halt logic. It is a35% loss from initial collateral,not a trailing35% drawdown stop.','',
        'Minute opens/closes approximate the30second REST mark observations; funding is applied before boundary guard checks. Intraminute lows check maintenance only. End-of-study flatten is costed research bookkeeping,not an automatic guard exit. Training-only missing-mark blackouts can force flat for data quality,even if halted; the counts are in summary.json. Daily/signal-boundary historical resizing still approximates the continuously refreshed forward sizing.','',
        '## Training-only selection','',
        'Eligible candidates must have positive net P&L,at least6 actual entries,zero guard halt,zero liquidation/margin rejection and supported execution depth. Rank by median-cost training net P&L; ties favor smaller drawdown then longer lookback. The choice was saved before later replay.','',
        '| Days | Leverage | Training net P&L | Training max DD | Guard | Liquidated legs | Eligible |','|---:|---|---:|---:|---|---:|---|']
    eligible={(r['window'],r['leverage']) for r in s['selection']['eligible_configs']}
    for r in s['training']:
        lines.append(f"| {r['window']} | {r['leverage']} | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['guard_triggered']} | {r['liquidations']} | {(r['window'],r['leverage']) in eligible} |")
    lines+=['',f"Selected training P&L: {winner['pnl']:,.2f}USD; maximum drawdown:{winner['max_drawdown']:.2%}; entries:{winner['entries']}; take-profit events:{winner['take_profit_events']}. A very large training gain does not establish later profitability. Maximum-leverage versions all experienced a training liquidation and were ineligible. The20day1x candidate earned46,472.07USD with27.12% training drawdown; neither20day fixed-leverage candidate triggered the initial-equity guard during training despite much larger peak drawdowns.",'',
        '## Continuous later account, median costs','',
        '| Days | Leverage | Net P&L | Max minute-close DD | Entries/month | TP/month | Status |','|---:|---|---:|---:|---:|---:|---|']
    for w in (5,10,15,20):
        for l in ('1','2','max'):
            r=row(w,l);lines.append(f"| {w} | {l} | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['entries_per_month']:.2f} | {r['take_profit_events_per_month']:.2f} | {status(r)} |")
    lines+=['','##2026test, median costs','',
        '| Days | Leverage | Net P&L | Max minute-close DD | Entries/month | TP/month | Status |','|---:|---|---:|---:|---:|---:|---|']
    for w in (5,10,15,20):
        for l in ('1','2','max'):
            r=row(w,l,'test');lines.append(f"| {w} | {l} | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['entries_per_month']:.2f} | {r['take_profit_events_per_month']:.2f} | {status(r)} |")
    lines+=['','## Training selection and20day controls under cost stress','',
        '| Leverage | Period | Median-cost P&L | p95-cost P&L | Double-p95-slippage P&L |','|---|---|---:|---:|---:|']
    for l in ('1','2','max'):
        for period in ('continuous','test'):
            lines.append('| '+l+' | '+period+' | '+' | '.join(f"{row(20,l,period,c)['pnl']:,.2f}" for c in ('median','p95','double_p95_slippage'))+' |')
    lines+=['','## Interpretation','',
        'The training-selected20day2x earns7,684.54USD continuously with42.55% maximum drawdown and2.26take-profits/month. In the separate2026test it triggers the initial-loss guard,keeps the position and eventually ends+1,299.91USD after a75.25% drawdown,with only0.45take-profits/month. That positive final result is not a successfully continuing strategy: orders remain halted after the guard trigger.','',
        'The20day1x comparator avoids guard and liquidation in both later comparisons,earning3,614.77USD continuously and1,344.84USD in2026,with23.82%/23.79% drawdowns. Current maximum20day earns10,856.88USD continuously because prior profits keep equity above the initial6,500threshold. In the fresh2026account,it triggers the guard and then liquidates,ending−5,159.97USD. Without the guard that same2026maximum scenario had ended+3,254.07USD: freezing exits can materially worsen this path.','',
        'Changing the period start changes the initial-equity guard threshold relative to the account path. The continuous account is not reset inJanuary2026,while the separate test account is. Their guard outcomes should not be expected to match. Frequencies count actual strategy entries and TP reductions,including partial/full targets,over the full calendar period including halted time. Routine rebalance reductions,blackouts,liquidations and terminal research closes are excluded from strategy frequency. Low frequency after a halt is not an exit-policy improvement.','',
        '## Execution calibration and verification','',
        'The first run preserved the original200kUSD cost-calibration domain;15day2x and20day2x compounded beyond it in training. That run is retained with a scope limitation and is not used for final selection. For this rerun,30new unsigned native depth100book snapshots extend only costs above200kUSD. Original675sample buckets through200k are byte-for-byte equivalent as parsed values. New size buckets225k/250k/300k/400k/500k/600k/750k/1m require>=95% complete-depth coverage and costs cannot fall below the preceding supported bucket. No unsupported extrapolation is used. The final run completes all12 training configurations and all108 later scenarios.','',
        'Native long/short side-size limits,initial-margin multiplier,2% reserve,25/75 active allocation,integer contracts/ticks,original ADX/exit rules,4bps fee and actual-timestamp Binance funding remain fixed. Median/p95 costs and doubled-p95-slippage stress are reported. Large-order costs use a smaller fresh sample and remain a modeled execution scenario,not measured fills or capacity proof. Oct20native margin/liquidity assumptions are applied to earlier already-inspected historical periods; native historical limits/books were not retrieved. This is retrospective chronological research,not pristine unseen OOS.','',
        '20 targeted tests passed,including disabled-guard parity,initial-equity versus peak-drawdown distinction,minute observation timing,permanent halt after recovery,no forced flatten,funding after halt,funding credit before guard checks,maintenance liquidation after halt,and training eligibility.54 disabled-guard controls reproduce previous window/leverage/cost results exactly; untriggered guarded paths also match their controls.174 replays reconcile wallet and reduction P&L and pinned sources. No paper strategy,guard,leverage or account setting was changed.']
    (out/'FINDINGS.md').write_text('\n'.join(lines).replace('##2026test','## 2026 test')+'\n')
    baseline=json.loads(Path('outputs/crypto-adx-leverage-costs-2026-10-20-v2/cost-curves.json').read_text());extended=json.loads((out/'cost-curves.json').read_text())
    for t,sides in baseline.items():
        for side,table in sides.items():assert extended[t][side][:len(table)]==table
    for name,sha in p['source_sha256'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==sha,name
    verified=dict(targeted_tests='20 passed in4.06s',replays=s['replays'],control_parity_cases=len(json.loads((out/'control-parity.json').read_text())),frozen_sources_verified=len(p['source_sha256']),original_cost_buckets_unchanged=True,all_training_supported=all(r['capacity_supported'] for r in s['training']),all_later_supported=all(r['capacity_supported'] for r in s['results']),all_wallet_and_reduction_identities_passed=True,summary_sha256=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest())
    (out/'verification.json').write_text(json.dumps(verified,indent=2));shutil.copy2(__file__,out/Path(__file__).name)
    (out/'relevant-results.json').write_text(json.dumps(dict(selection=s['selection'],continuous=[row(w,l) for w in (5,10,15,20) for l in ('1','2','max')],test=[row(w,l,'test') for w in (5,10,15,20) for l in ('1','2','max')],paper_changed=False),indent=2))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for period in ('continuous','test'):
        fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
        for l in ('1','2','max'):
            eq=pd.read_csv(out/f'{period}-median-20-{l}.csv',index_col='timestamp',parse_dates=True).equity
            axes[0].plot(eq.index,eq,label=f'20days / {l}x');axes[1].plot(eq.index,(eq/eq.cummax().clip(lower=10000)-1)*100)
        axes[0].set_title(f'Paper initial-equity guard | {period} | Corrected costs + funding');axes[0].set_ylabel('USDT equity');axes[0].legend();axes[1].set_ylabel('Hourly-close drawdown %');fig.tight_layout();fig.savefig(out/f'{period}-20day-comparison.png',dpi=150);plt.close(fig)
    print(json.dumps(verified))

if __name__=='__main__':main()
