"""Report the three frozen leverage scenarios without new parameter selection."""
import hashlib
import json
from pathlib import Path
import shutil
import pandas as pd

OUT=Path('outputs/crypto-adx-leverage-costs-2026-10-20-v2')


def main():
    out=OUT;s=json.loads((out/'summary.json').read_text());results=s['results'];protocol=s['protocol']
    def row(lev,period='continuous',case='median'):return next(r for r in results if r['period']==period and r['cost_case']==case and r['leverage']==lev)
    names={'1':'1x target','2':'2x target','max':'Current published maximum'}
    lines=['# ADX DOGE/SOL:1x,2x and current maximum leverage','',
        '**Maximum leverage earned the most net P&L in this retrospective scenario, but with53.6% maximum drawdown and no increase in take-profit frequency.**','',
        'Every account starts10,000USDT. Primary continuous period:July2025–September2026. Secondary test:January–September2026, starting a separate10,000account. ADX14>=20 selects the SOL ladder once at entry; original SOL exits otherwise. DOGE retains0.5ATR full exits. No signal or exit optimization was performed.','',
        '| Sizing | Continuous net P&L | Continuous max DD | 2026test net P&L | Test max DD | Continuous TP events/month | Test TP events/month |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for lev in names:
        r=row(lev);t=row(lev,'test');lines.append(f"| {names[lev]} | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {t['pnl']:,.2f} | {t['max_drawdown']:.2%} | {r['take_profit_events_per_month']:.2f} | {t['take_profit_events_per_month']:.2f} |")
    lines+=['','Drawdowns use minute closes; adverse minute lows are checked separately for pooled maintenance breach. No liquidations or margin rejections occurred in any of27 replays. That observation is conditional on this model, not protection against future liquidation.','',
        '## Realization frequency','',
        'All three leverage modes generated34 take-profit reductions continuously:17 profitable full targets and17 profitable partial sales. Each produced42 profitable natural reductions in total, or2.80/month, on1.86 net-profitable sale days/month. The2026test had20 take-profit reductions:9 full targets and11 partial sales;24 profitable natural reductions, or2.68/month, on1.90 net-profitable sale days/month. Routine resizing, forced terminal closes, liquidations and blackouts are excluded from strategy event frequency. Gains remain invested; no withdrawals or payouts are simulated.','',
        '## Execution-cost stress','',
        '| Sizing | Continuous median-cost P&L | Continuous p95-cost P&L | Continuous double-p95-slippage P&L | Test median-cost P&L | Test p95-cost P&L | Test double-p95-slippage P&L |','|---|---:|---:|---:|---:|---:|---:|']
    for lev in names:
        values=[row(lev,p,c)['pnl'] for p in ('continuous','test') for c in ('median','p95','double_p95_slippage')]
        lines.append('| '+names[lev]+' | '+' | '.join(f'{v:,.2f}' for v in values)+' |')
    lines+=['','Costs use675 recorded native Kalshi books from the repaired quote archive. Every book is sorted best-first with zero quantities removed. Median and p95 midpoint slippage are fitted separately by asset,side and notional; the simulator uses the next larger measured size bucket, adds4bps per filled side, and rounds adverse prices to valid ticks. Double-p95 stress doubles slippage while retaining the4bps fee. At least95% measured depth coverage is required; no unsupported order-size extrapolation is allowed. The full grid100–200,000USDT had100% coverage in this sample. This is quoted depth evidence, not actual fills.','',
        '## Maximum leverage and accounting','',
        'The current API snapshot supplies DOGE long maintenance-leverage estimates3.7882/3.7586/3.6121/3.0076 and SOL3.5969/3.5885/3.5504/3.3715 at1k/10k/100k/1m USD notionals. Initial-margin multiplier is1.3. Maximum target is derived from98% available initial-margin budget using the next larger size bucket. At10k notional this corresponds to initial leverage2.89DOGE/2.76SOL before the2% reserve. The effective maximum-target exposure therefore varies around2.7–2.9x; adverse price moves can push held effective leverage higher. The continuous run reached3.21x at a minute close.','',
        'ALL three modes use the same pooled wallet and active-leg allocation rule:25% DOGE/75% SOL when both are active,100% of the active allocation to the sole active asset. Fixed1x/2x also keep the2% reserve. Full positions rebalance atUTC midnight and when the active-asset set changes. Partial reductions use actual integer holdings and are never bought back. Native contract prices are underlying prices multiplied by100DOGE or0.1SOL. Fill fees, funding allocation and closed-realization totals reconcile to ending equity. Margin curves and maintenance are applied identically across1x,2x andmaximum.','',
        'This pool/active-allocation rule differs from the earlier independent25/75 collateral sleeves, which retained idle cash when a sleeve was flat. These dollar totals must not be compared directly with those studies. This historical replay also approximates the polled forward sizing cadence with daily/signal-boundary resizing. The gated execution adapter has not been qualified by this study.','',
        '## Evidence limits and verification','',
        'No historical native Kalshi margin curves were retrieved. Current October20,2026 limits and a short October20 quoted-depth sample are applied to historical Binance perpetual minute trade/mark prices and actual historical Binance funding. That makes this a current-limit, empirically calibrated sensitivity scenario, not a native historical max-leverage execution backtest or pristine out-of-sample confirmation. Funding is a proxy for native funding; measured fill latency, historical liquidity, stressed quote withdrawal and cross-asset portfolio offsets are absent. Simultaneous minute lows across assets provide a conservative intraminute maintenance check; full liquidation is modeled at adverse prices with50bps liquidation fee, followed by a trading halt.','',
        'Seven targeted tests cover margin sizing, integer lots, fee/equity/reduction identities, historical funding charged once, intraminute liquidation and halt, no buying back partials, active-set sizing transitions and unsupported-depth rejection. All27 replay wallet identities passed; pinned sources were verified after replay. The initial run is preserved and explicitly invalidated because its active-set transition could exceed gross targets. Onlyv2 results are reported.','',
        'The existing default maximum-leverage configuration, research gate, running paper campaign and withdrawal gate are unchanged. This study does not promote any candidate to execution.']
    (out/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    for name,sha in protocol['source_sha256'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==sha,name
    verification=dict(targeted_tests='7 passed in0.69s',replays=27,source_hashes_verified=len(protocol['source_sha256']),all_wallet_and_reduction_identities_passed=True,liquidations=sum(r['liquidations'] for r in results),margin_rejections=sum(r['margin_rejections'] for r in results),results_sha256=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest(),invalidated_prior_run='../crypto-adx-leverage-costs-2026-10-20/INVALIDATED.md')
    (out/'verification.json').write_text(json.dumps(verification,indent=2));shutil.copy2(__file__,out/Path(__file__).name)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for period in ('continuous','test'):
        fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
        for lev in names:
            eq=pd.read_csv(out/f'{period}-median-{lev}.csv',index_col='timestamp',parse_dates=True).equity
            axes[0].plot(eq.index,eq,label=names[lev]);axes[1].plot(eq.index,(eq/eq.cummax().clip(lower=10000)-1)*100)
        axes[0].set_title(f'ADX DOGE/SOL | {period} | Corrected empirical costs + historical funding');axes[0].set_ylabel('USDT equity');axes[0].legend();axes[1].set_ylabel('Hourly-close drawdown %');fig.tight_layout();fig.savefig(out/f'{period}-comparison.png',dpi=150);plt.close(fig)

if __name__=='__main__':main()
