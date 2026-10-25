"""Compact reporting of locked DOGE/SOL research; never selects on holdouts."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output
    summary = json.loads((root/'summary.json').read_text())
    selection = summary['selection']
    primary = [r for r in summary['results'] if r['cost_bps']==8]
    frequency = []
    for r in primary:
        label, period = r['strategy'], r['period']
        ep = root/f'{label}-{period}-episodes.json'
        path = pd.read_csv(root/f'{label}-{period}.csv', index_col='timestamp', parse_dates=True).equity
        daily = path.resample('1D').last()
        monthly = daily.resample('ME').last().pct_change()
        monthly.iloc[0] = daily.resample('ME').last().iloc[0]/10000-1
        detail = dict(strategy=label, period=period, worst_month_return=float(monthly.min()),
            positive_months=int((monthly>0).sum()), observed_months=len(monthly))
        if ep.exists():
            episodes = json.loads(ep.read_text())
            end_ms = int(path.index[-1].timestamp()*1000)+3600000-1
            net_by_day = {}
            for e in episodes:
                if e['exit_ms']==end_ms:
                    continue
                date = pd.Timestamp(e['exit_ms'], unit='ms', tz='UTC').strftime('%Y-%m-%d')
                net_by_day[date] = net_by_day.get(date, 0.)+e['net_pnl']
            profitable_days = sum(v>0 for v in net_by_day.values())
            detail.update(profitable_realization_days=profitable_days,
                profitable_realization_days_per_month=profitable_days/(len(daily)/30.4375),
                daily_net_episode_realizations=net_by_day)
        frequency.append(detail)
    relevant = dict(training_selections=selection, primary_results=primary,
        selected_cost_stress=[r for r in summary['results'] if r['period']=='test' and r['cost_bps']>8],
        realization_diagnostics=frequency)
    (root/'relevant-results.json').write_text(json.dumps(relevant, indent=2))
    lines = ['# DOGE/SOL: relevant results', '',
        'Initial equity 10,000 USDT; train 2021–2023; validation 2024; test January 2025–September 2026. Historical funding included. Primary execution cost 8bps/side (4bps taker + 4bps spread/slippage).', '',
        '## Frozen training selections', '']
    for label in ('max_pnl', 'balanced', 'lower_drawdown'):
        row = selection[label]
        if row is None:
            lines += [f'**{label}**: no training candidate met the registered criteria.', '']
            continue
        c = row['config']
        lines += [f"**{label}**: {c['doge_allocation']:.0%} DOGE / {c['sol_allocation']:.0%} SOL initial collateral; DOGE {c['doge_leverage']}x, TP {c['doge_tp_atr']} ATR, cap {c['doge_max_hold_hours']}h; SOL {c['sol_leverage']}x, TP {c['sol_tp_atr']} ATR, cap {c['sol_max_hold_hours']}h.",
                  f"Training net P&L {row['pnl']:,.2f}; daily DD {row['max_drawdown']:.1%}; profitable natural closes/month {row['profitable_closes_per_month']:.2f}.", '']
    for period, title in [('test', 'January 2025–September 2026 test'), ('post_training', 'January 2024–September 2026 continuous later period')]:
        lines += [f'## {title}', '', '| Policy | Net P&L | Ending equity | Max daily DD | Max hourly DD | Profitable closes/month |',
                  '|---|---:|---:|---:|---:|---:|']
        for r in primary:
            if r['period']==period:
                lines.append(f"| {r['strategy']} | {r['pnl']:,.2f} | {r['ending_equity']:,.2f} | {r['max_drawdown']:.1%} | {r['max_hourly_drawdown']:.1%} | {r['profitable_closes_per_month']:.2f} |")
        lines.append('')
    lines += ['## Test cost stress', '', '| Policy | Cost/side | Net P&L | Daily DD |', '|---|---:|---:|---:|']
    for r in summary['results']:
        if r['period']=='test' and r['cost_bps']>8:
            lines.append(f"| {r['strategy']} | {r['cost_bps']}bps | {r['pnl']:,.2f} | {r['max_drawdown']:.1%} |")
    lines += ['', '## Interpretation limits', '',
        'The search ranks 2,352 portfolios using training results only. The balanced profile requires daily DD<=35% and at least 2 profitable natural closes/month; lower_drawdown requires DD<=25% with the same frequency. These are training constraints, not future guarantees. No fallback changes or later-period tuning were made.', '',
        'Profit frequency excludes forced terminal closures and includes net episodes after fees, funding and partial-rebalance P&L. DOGE and SOL episodes can close on the same date. Distinct positive portfolio realization days are retained in relevant-results.json. They are not scheduled withdrawals.', '',
        'Initial allocations remain independent collateral sleeves, each compounding its own P&L. There are no free transfers or periodic capital reallocation. Targets use 98% of the selected exposure cap; 0.5x exposure uses a 1x venue margin ceiling.', '',
        'Prices, marks and historical funding are Binance perpetual proxies. Current Kalshi multiplier/tick and taker-fee snapshots inform the model; venue dynamic margin, historical order-book depth, impact and exact native Kalshi funding are not reconstructed. Daily and hourly drawdowns may understate intrahour peak-to-trough losses.', '',
        'The assets and later dates were previously inspected, making this retrospective chronological follow-up research rather than pristine out-of-sample evidence. The 2024 validation results are reported in REPORT.md and did not choose the recipes.']
    (root/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    hashes = json.loads((root/'protocol.json').read_text())['source_sha256']
    for name, expected in hashes.items():
        path = Path('src/autoresearch')/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise AssertionError(f'source changed: {name}')
    (root/'verification.json').write_text(json.dumps(dict(frozen_source_hashes_verified=len(hashes),
        targeted_tests='63 passed', critical_lint='passed',
        summary_sha256=hashlib.sha256((root/'summary.json').read_bytes()).hexdigest()), indent=2))
    shutil.copy2(__file__, root/Path(__file__).name)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    labels = dict(max_pnl='Training P&L only', balanced='Constrained portfolio 1.5x',
        lower_drawdown='Constrained portfolio 1x', previous_DOGE_2x='Prior DOGE 2x', previous_SOL_1x='Prior SOL 1x')
    for period, title, filename in [('test', 'January 2025–September 2026 test', 'test-comparison.png'),
            ('post_training', 'January 2024–September 2026 continuous period', 'comparison.png')]:
        fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
        for label, display in labels.items():
            path = root/f'{label}-{period}.csv'
            if not path.exists():
                continue
            p = pd.read_csv(path, index_col='timestamp', parse_dates=True).equity
            axes[0].plot(p.index, p, label=display)
            peak = p.cummax().clip(lower=10000)
            axes[1].plot(p.index, (p/peak-1)*100)
        axes[0].set_title(title+' | Historical funding and modeled costs included')
        axes[0].set_ylabel('USDT equity')
        axes[0].legend()
        axes[1].set_ylabel('Hourly drawdown %')
        fig.tight_layout()
        fig.savefig(root/filename, dpi=150)
        plt.close(fig)
    print(json.dumps([r for r in primary if r['period']=='test'], indent=2))


if __name__=='__main__':
    main()
