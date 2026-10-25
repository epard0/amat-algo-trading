"""Compact results for frozen ADX entry-window comparison."""
import hashlib
import json
from pathlib import Path
import shutil
import pandas as pd

OUT=Path('outputs/crypto-adx-breakout-windows-2026-10-20')


def main():
    out=OUT;s=json.loads((out/'summary.json').read_text());protocol=json.loads((out/'protocol.json').read_text())
    def row(w,period='continuous',case='median'):return next(r for r in s['results'] if r['window']==w and r['period']==period and r['cost_case']==case)
    raw=max(s['training'],key=lambda r:r['pnl'])
    lines=['# ADX DOGE/SOL breakout-window comparison','',
        '**No eligible training selection. All four windows hit modeled liquidation in training. Shorter entry windows did not outperform20days in the continuous later account.**','',
        'Four joint entry-window candidates:5/10/15/20calendar days on four-hour closes, applied to both DOGE andSOL. Training:January2024–June2025. Validation:July–December2025. Test:January–September2026. Continuous later period:July2025–September2026. Every standalone replay starts10,000USDT. Only the entry lookback changes. Ten-day channel exits,72hour caps, DOGE0.5ATR target, SOL ADX14>=20 entry-locked ladder/original exit selection and active25/75 allocation remain fixed.','',
        '## Training selection','',
        'The protocol ranks median-cost training net P&L, requiring positive P&L,zero liquidation/margin rejection,at least6 actual entries and supported measured depth. Selection was saved before later replay. All four failed the zero-liquidation condition. The10day rule was the unconstrained training P&L leader, not an eligible selected strategy. No gates were relaxed after results.','',
        '| Window | Training net P&L | Training max DD | Modeled liquidated legs |','|---|---:|---:|---:|']
    for r in s['training']:lines.append(f"| {r['window']}days | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['liquidations']} |")
    lines+=['','All four training paths liquidated the SOL position onMarch5,2024 at19:53:06.001UTC under the current-margin historical scenario. Each account then halted permanently; capital was not reset. This is not evidence of an actual Kalshi liquidation in2024. It exposes the fragility of applying current maximum initial leverage to that historical price path. Training frequencies are depressed by the subsequent halt, so they are not presented as normal active trading rates.','',
        '## Continuous later account, median costs','',
        '| Window | Net P&L | Max minute-close DD | Actual entries | Entries/month | Take-profit events | TP/month | Liquidations |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for w in (5,10,15,20):
        r=row(w);lines.append(f"| {w}days | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['entries']} | {r['entries_per_month']:.2f} | {r['take_profit_events']} | {r['take_profit_events_per_month']:.2f} | {r['liquidations']} |")
    lines+=['','## 2026 test, median costs','',
        '| Window | Net P&L | Max minute-close DD | Entries/month | TP/month | Liquidations |','|---|---:|---:|---:|---:|---:|']
    for w in (5,10,15,20):
        r=row(w,'test');lines.append(f"| {w}days | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['entries_per_month']:.2f} | {r['take_profit_events_per_month']:.2f} | {r['liquidations']} |")
    lines+=['','The15day rule liquidates and halts in both the2026test and continuous later replay. Its lower maximum drawdown and lower frequency partly reflect stopping early; they do not demonstrate a safer continuing strategy. Take-profit counts include full targets and partial ladder sales, net-cost profitability reported separately in summary.json. Actual entries count only transitions from flat to filled holdings, excluding routine resizing. Frequencies use the entire period including halted time.','',
        '## Continuous-period execution stress','',
        '| Window | Median-cost net P&L | p95-cost net P&L | Doubled-p95-slippage net P&L |','|---|---:|---:|---:|']
    for w in (5,10,15,20):lines.append('| '+str(w)+'days | '+' | '.join(f"{row(w,case=c)['pnl']:,.2f}" for c in ('median','p95','double_p95_slippage'))+' |')
    lines+=['','## Interpretation','',
        'Shorter windows admit more breakouts, but the additional entries do not improve net results in this comparison. Five days nearly doubles take-profit frequency while losing71.1% of starting capital and suffering93.9% peak-to-trough drawdown. Ten days improves frequency but retains only650USDT continuous profit, with82.8% drawdown, and loses2,612USDT in2026. Fifteen days liquidates later. Twenty days is the strongest later-period comparator, not a qualified historical robustness winner because its training also liquidates. These results provide no basis to change the running paper entry rule to5/10/15days.','',
        '## Frozen model and limits','',
        'Current published native side/size leverage curves and1.3 initial-margin multipliers are copied unchanged from the previous leverage study, with2% reserve. All candidates share one pooled collateral wallet and normalize25/75 allocations over active assets. Full positions resize daily and on active-set transitions; partial sales are never bought back. Leverage therefore varies by asset and size around2.7–2.9x at entry, and held leverage can drift. Research replay uses minute maintenance checks and full adverse-price liquidation, followed by permanent halt.','',
        'The research replay retains the prior study convention: it does not model the forward service35% initial-equity new-order guard or its30second sizing cadence. The live paper service can stop placing strategy orders earlier, so particularly the deeply losing5/10day histories are counterfactual research paths, not exact operational account forecasts. No paper configuration or source-pinned strategy code was changed by this study.','',
        'Corrected empirical median/p95 one-way slippage curves use675 native order-book observations by side and order notional;4bps fee and historical Binance funding are included. Doubled-p95 stress doubles slippage only. No execution-cost curve or limit was re-fit for any window. The200k measured-order domain was sufficient for every replay. Native integer lots/ticks and actual historical funding timestamps are preserved. Each period starts flat and skips any pre-existing carried signal. Signals begin with the existing January2024 data history and each window has its own lookback warmup.','',
        'Historical minute prices, marks and funding are Binance perpetual proxies. October2026 native liquidity/margin assumptions are applied retrospectively to previously examined2024–2026 periods; native historical margin curves and books were not retrieved. These are chronological sensitivity comparisons, not pristine unseen out-of-sample or native Kalshi execution evidence. Few windows do not erase prior research selection bias.','',
        '## Verification','',
        '12 targeted tests passed:20day plan parity, causal lookbacks and plans, unchanged10day exit channel, shorter-window entry timing, training selection gates, entry-event counting, and existing max-leverage/funding/partial-sale accounting. The20day control reproduces the prior max-leverage study in all9 later-period/cost comparisons.40 replays completed, with exact wallet and reduction identities and frozen source hashes verified.']
    (out/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    relevant=dict(selection=s['selection'],unconstrained_training_leader=raw,training=s['training'],continuous=[row(w) for w in (5,10,15,20)],test=[row(w,'test') for w in (5,10,15,20)],paper_default_changed=False)
    (out/'relevant-results.json').write_text(json.dumps(relevant,indent=2))
    for name,sha in protocol['source_sha256'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==sha,name
    (out/'verification.json').write_text(json.dumps(dict(targeted_tests='12 passed in1.72s',replays=40,control_parity_cases=len(json.loads((out/'control-parity.json').read_text())),frozen_sources_verified=len(protocol['source_sha256']),all_wallet_and_reduction_identities_passed=True,summary_sha256=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest()),indent=2))
    shutil.copy2(__file__,out/Path(__file__).name)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for period in ('continuous','test'):
        fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
        for w in (5,10,15,20):
            eq=pd.read_csv(out/f'{period}-median-{w}.csv',index_col='timestamp',parse_dates=True).equity
            axes[0].plot(eq.index,eq,label=f'{w}days');axes[1].plot(eq.index,(eq/eq.cummax().clip(lower=10000)-1)*100)
        axes[0].set_title(f'ADX DOGE/SOL at current maximum leverage | {period} | Costs + funding');axes[0].set_ylabel('USDT equity');axes[0].legend();axes[1].set_ylabel('Hourly-close drawdown %');fig.tight_layout();fig.savefig(out/f'{period}-comparison.png',dpi=150);plt.close(fig)

if __name__=='__main__':main()
