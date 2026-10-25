"""Relevant DOGE/SOL short-breakout results, preserving training selections."""
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
    out = args.output
    summary = json.loads((out/'summary.json').read_text())
    selection = summary['selection']
    chosen = selection['selected']
    results = summary['results']
    primary = selection['primary']
    training = json.loads((out/'training.json').read_text())
    positive_training = sum(r['pnl']>0 for r in training)
    def row(label, period='test', cost=8):
        return next(r for r in results if r['strategy']==label and r['period']==period and r['cost_bps']==cost)
    def artifact(label, period, suffix):
        p = out/f'{label}-{period}{suffix}'
        if p.exists():
            return p
        # P&L and constrained selections may have exactly the same recipe.
        for other in chosen:
            if chosen[other]['id']==chosen[label]['id']:
                alternative = out/f'{other}-{period}{suffix}'
                if alternative.exists():
                    return alternative
        raise FileNotFoundError(p)
    preferred = []
    for m in (1, 5, 15, 30):
        label = f'{m}m-frequent' if f'{m}m-frequent' in chosen else f'{m}m-pnl'
        if label in chosen:
            preferred.append(label)
    diagnostics = []
    for label in preferred:
        for period in ('validation', 'test', 'post_training'):
            p = artifact(label, period, '.csv')
            equity = pd.read_csv(p, index_col='timestamp', parse_dates=True).equity
            end_ms = int(equity.index[-1].timestamp()*1000)+3600000-1
            episodes = json.loads(artifact(label, period, '-episodes.json').read_text())
            natural = [e for e in episodes if e['exit_ms']!=end_ms and not e.get('forced_blackout', False)]
            net_by_day = {}
            for e in natural:
                day = str(pd.Timestamp(e['exit_ms'], unit='ms', tz='UTC').date())
                net_by_day[day] = net_by_day.get(day, 0.)+e['net_pnl']
            days = len(equity.resample('1D').last())
            positive_days = sum(v>0 for v in net_by_day.values())
            diagnostics.append(dict(strategy=label, period=period, profitable_realization_days=positive_days,
                profitable_realization_days_per_month=positive_days/(days/30.4375), daily_episode_net_pnl=net_by_day))
    relevant = dict(training_candidates=len(training), positive_net_training_candidates=positive_training,
        primary_selected_on_training=primary, global_training_pnl=selection['global_training_pnl'],
        training_selections=selection, preferred_by_interval=preferred,
        interval_test_results=[row(k) for k in preferred],
        interval_pnl_test_results=[row(f'{m}m-pnl') for m in (1, 5, 15, 30) if f'{m}m-pnl' in chosen],
        primary_results=[r for r in results if r['strategy']==primary],
        controls=[r for r in results if r['strategy'].startswith('previous-4h')],
        realization_diagnostics=diagnostics)
    (out/'relevant-results.json').write_text(json.dumps(relevant, indent=2))
    lines = ['# DOGE/SOL short-window breakout results', '',
        '10,000 USDT initial equity. Training January 2024–June 2025; validation July–December 2025; test January–September 2026. All recipes and thresholds were fixed before holdout replay.', '',
        f'Positive-net training candidates: **{positive_training} / {len(training)}**. Primary diagnostic selected on training: **{primary}**. Global unconstrained training P&L selection: **{selection["global_training_pnl"]}**. Where training P&L is negative, later replay is diagnostic and does not represent a passed profitability screen.', '',
        '## Same-date test comparison', '',
        'Each interval shows its training-selected frequency-qualified choice where available, otherwise its training P&L winner. No later P&L ranking changes the choice.', '',
        '| Policy | Net P&L | Max daily DD | Max minute-close DD | Profitable natural closes/month | Median hold hours |',
        '|---|---:|---:|---:|---:|---:|']
    for label in preferred+['previous-4h-1.5x', 'previous-4h-1.0x']:
        r = row(label)
        lines.append(f"| {label} | {r['pnl']:,.2f} | {r['max_drawdown']:.1%} | {r['max_minute_close_drawdown']:.1%} | {r['profitable_closes_per_month']:.2f} | {r['median_holding_hours']} |")
    lines += ['', '## Training-selected settings', '',
        '| Policy | DOGE/SOL capital | Leverage per sleeve | Entry lookback | DOGE target / hold cap | SOL target / hold cap | Training net P&L |',
        '|---|---|---:|---:|---|---|---:|']
    for label in preferred:
        r = chosen[label]
        c = r['config']
        lines.append(f"| {label} | {c['doge_allocation']:.0%}/{c['sol_allocation']:.0%} | {c['leverage']}x | {c['entry_hours']}h | {c['doge_tp_atr']}ATR/{c['doge_max_hold_hours']}h | {c['sol_tp_atr']}ATR/{c['sol_max_hold_hours']}h | {r['pnl']:,.2f} |")
    lines += ['', '## Primary selection across periods and costs', '', '| Period | Cost/side | Net P&L | Daily DD | Profitable closes/month |', '|---|---:|---:|---:|---:|']
    for r in relevant['primary_results']:
        lines.append(f"| {r['period']} | {r['cost_bps']}bps | {r['pnl']:,.2f} | {r['max_drawdown']:.1%} | {r['profitable_closes_per_month']:.2f} |")
    lines += ['', '## Unconstrained training-P&L selections', '', '| Interval | Test net P&L | Daily DD | Profitable closes/month |', '|---|---:|---:|---:|']
    for r in relevant['interval_pnl_test_results']:
        lines.append(f"| {r['config']['minutes']}m | {r['pnl']:,.2f} | {r['max_drawdown']:.1%} | {r['profitable_closes_per_month']:.2f} |")
    if (out/'training-target-scale.json').exists():
        lines += ['', '## Training target size versus costs', '',
            'Across all training candles (not conditional entry fills), the median 0.5ATR target on 1m bars is about 7bps for both assets. The primary modeled round-trip cost is about 16bps before funding. Triggering a target is therefore not sufficient to realize net profit. Per-interval target scales are saved in training-target-scale.json.']
    lines += ['', '## Selection and accounting', '',
        'The grid contains 3,888 portfolios: four signal intervals; 2/6/24-hour entry windows; channel exits at one quarter of entry lookback; 25/50/75% initial DOGE allocation; 1/1.5/2x common sleeve leverage; independent DOGE/SOL 0.5/1/2ATR targets with 1/6-hour hold caps. ATR14 is measured in native signal bars and frozen at entry signal close. Entries and exits execute at the next minute open, with tick rounding, fees and slippage.', '',
        'Each sleeve compounds independently. Allocation is the initial collateral split, without transfers or periodic reallocation. Daily position resizing uses each sleeve’s own current equity. Targets use 98% of the leverage cap.', '',
        'Eligibility requires no training liquidations and at least 20 natural completed episodes. Frequency-qualified choices additionally require positive training P&L, daily drawdown at most 35%, and at least four profitable full-position closes/month. The primary choice is the maximum training P&L among frequency-qualified choices; absent any, it is the global training P&L choice. Test data never choose recipes.', '',
        'Historical funding is charged once at actual events. Primary costs are 8bps per side: 4bps taker fee plus 4bps modeled spread/slippage; the global training winners also face 15/30bps stress. Profitable closes include all opening/closing/rebalance fees, funding and price P&L. Forced end-of-period and data-blackout exits are excluded from frequency counts. These are irregular realized episodes, not withdrawals.', '',
        'Minute mark data are real archive observations. Both assets have two unavailable mark minutes on August 12, 2024, after daily-archive repair. Every policy must exit before the gap, pay costs, stay flat through it, and pay re-entry costs. Placeholder prices can only mark flat wallets; assertions enforce this. The gap is in training, outside validation/test.', '',
        'The fast replay mutates the established Decimal wallet at every order, funding event and daily resize. Between events, fixed contracts make mark equity affine in price. Every intervening minute low/high is checked for the first liquidation threshold crossing. Parity tests compare the full minute equity path with the original minute wrapper, including intermediate liquidation and funding.', '',
        '## Verification and limits', '',
        'The corrected replay includes tradability-resume boundaries. An exhaustive audit of all 144 cached signals found no affected training orders, and every selected training wallet was replayed with identical metrics. All later portfolios and controls were recomputed. The original hourly control treats an hour as tradable if any minute trades. Provenance is retained in protocol.json, reuse-audit.json, selected-training-parity.json and correction-result-deltas.json.', '',
        'Historical trade, mark and funding data are Binance perpetual proxies, not native Kalshi funding or executions. Current Kalshi fee and multiplier/tick snapshots define scenarios. Fixed maintenance/liquidation rules do not recreate dynamic Kalshi risk tiers. Quote depth, impact, taxes and collateral yield are not modeled. Minute-close drawdown excludes possible intraminute troughs.', '',
        'These assets and later dates were previously inspected. This is retrospective chronological follow-up, with a large training search, rather than pristine out-of-sample evidence. Validation is reported without selecting on it. No live orders or account changes were made.', '',
        'Data source documentation: [Binance USD-M market data](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price-Kline-Candlestick-Data).']
    (out/'FINDINGS.md').write_text('\n'.join(lines)+'\n')
    protocol = json.loads((out/'protocol.json').read_text())
    for name, sha in protocol['source_sha256'].items():
        if hashlib.sha256((Path('src/autoresearch')/name).read_bytes()).hexdigest()!=sha:
            raise AssertionError('frozen source changed: '+name)
    (out/'verification.json').write_text(json.dumps(dict(frozen_source_hashes_verified=len(protocol['source_sha256']),
        summary_sha256=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest()), indent=2))
    shutil.copy2(__file__, out/Path(__file__).name)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={'height_ratios':[2, 1]})
    for label in preferred+['previous-4h-1.5x']:
        p = out/f'{label}-test.csv' if label.startswith('previous') else artifact(label, 'test', '.csv')
        equity = pd.read_csv(p, index_col='timestamp', parse_dates=True).equity
        axes[0].plot(equity.index, equity, label=label)
        axes[1].plot(equity.index, (equity/equity.cummax().clip(lower=10000)-1)*100)
    axes[0].set_title('January–September 2026 test | Training-selected recipes, historical funding and costs')
    axes[0].set_ylabel('USDT equity')
    axes[0].legend()
    axes[1].set_ylabel('Hourly-close drawdown %')
    fig.tight_layout()
    fig.savefig(out/'comparison.png', dpi=150)
    print(json.dumps(dict(primary=primary, test=[row(k) for k in preferred], controls=[row('previous-4h-1.5x')]), indent=2))


if __name__=='__main__':
    main()
