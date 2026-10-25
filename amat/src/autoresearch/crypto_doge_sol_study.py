"""Registered DOGE/SOL allocation, leverage and take-profit experiment."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from autoresearch.crypto_directional import run_directional
from autoresearch.crypto_doge_sol import portfolio_metrics, profit_weights, select_training
from autoresearch.crypto_universe_study import load_asset

TRAIN = ('2021-01-01', '2023-12-31')
PERIODS = dict(validation=('2024-01-01', '2024-12-31'), test=('2025-01-01', '2026-09-30'),
               post_training=('2024-01-01', '2026-09-30'))
EXITS = [(None, None)] + list(itertools.product((.5, 1., 2.), (24, 72)))
LEVERAGES = (.5, 1., 1.5, 2.)
ALLOCATIONS = (.25, .5, .75)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    if (out/'protocol.json').exists():
        raise ValueError('refusing to overwrite registered study')
    sources = [Path(__file__), Path(__file__).with_name('crypto_doge_sol.py'),
               Path(__file__).with_name('crypto_directional.py'), Path(__file__).with_name('crypto_universe_study.py'),
               Path(__file__).with_name('perpetual_sim.py')]
    roots = dict(DOGEUSDT=Path('data/research/kalshi-universe-proxies-2021-2026-09'),
                 SOLUSDT=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09'))
    inventory = Path('outputs/crypto-directional-2026-10-19/kalshi-inventory/markets.json')
    protocol = dict(training=TRAIN, periods=PERIODS, starting_capital=10000,
        doge_allocations=ALLOCATIONS, sol_allocation='1 minus DOGE allocation', per_sleeve_leverage=LEVERAGES,
        exit_configs=EXITS, candidate_portfolios=len(ALLOCATIONS)*len(LEVERAGES)**2*len(EXITS)**2,
        capital_policy='initial fixed independent collateral allocations; each sleeve compounds its own P&L; no transfers or periodic capital reallocation; idle cash stays in its sleeve',
        signals='long-only 4h 20day breakout/10day channel; ATR14 frozen at entry signal close; TP0.5/1/2ATR and 24/72h maximum hold; next-hour open execution; .98 times leverage exposure cap; daily size rebalance',
        selection='training only: zero liquidations, positive P&L, >=6 natural completed episodes. max_pnl unconstrained; balanced max P&L with daily DD<=35% and >=2 profitable natural closes/month; lower_drawdown max P&L with DD<=25% and same frequency. No fallback or test-period tuning.',
        execution='exact existing Decimal perpetual simulator, historical discrete funding, Kalshi snapshot multipliers/ticks; independent isolated margin with .5% maintenance, .5% liquidation fee; 0.5x exposure uses 1x venue margin ceiling with half-size targets',
        costs='8bps per side =4bps taker+4bps spread/slippage; selected recipes also tested at15/30bps per side',
        profit_frequency='net completed position episodes after all funding, fees and rebalance P&L; excludes forced terminal closes; includes separate DOGE and SOL episodes, which may occur on the same date; no withdrawals',
        caveats=['Binance price/mark/funding proxies, not native historical Kalshi fills or funding',
            'dates/assets already inspected in preceding research; retrospective chronological holdout, not pristine OOS',
            'no reconstructed historical bid/ask depth, impact, or Kalshi dynamic risk tiers',
            'allocation search in2352 portfolios increases selection risk; validation/test never select the recipe',
            'hourly mark extrema check liquidation; daily and hourly closing drawdowns do not capture every intrahour equity trough'],
        source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        manifest_sha256={s: hashlib.sha256((r/'manifest.json').read_bytes()).hexdigest() for s, r in roots.items()},
        inventory_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2))
    snap = out/'source'
    snap.mkdir()
    for p in sources:
        shutil.copy2(p, snap/p.name)
    markets = json.loads(inventory.read_text())['markets']
    assets, signals, quality = {}, {}, {}
    for symbol, root in roots.items():
        frames, funding, audit = load_asset(root, symbol)
        market = next(m for m in markets if m['ticker'] == f'KX{symbol.removesuffix("USDT")}PERP')
        specs = {symbol: dict(multiplier=market['contract_size'],
            price_tick=str(float(market['tick_size'])/float(market['contract_size'])))}
        assets[symbol] = frames, funding, specs
        quality.update(audit)
        for ei, (tp, hold) in enumerate(EXITS):
            signals[symbol, ei] = profit_weights(frames[symbol], tp, hold)
    (out/'data-audit.json').write_text(json.dumps(quality, indent=2))

    def replay(symbol, fraction, leverage, exit_id, dates, cost=8, capture=False):
        f, fu, specs = assets[symbol]
        return run_directional(f, fu, signals[symbol, exit_id]*leverage, dates, capital=10000*fraction,
            cost=cost, fee_bps=4, leverage_limit=max(1., leverage), contract_specs=specs, capture=capture)

    # Only training data are replayed until selection.json is frozen.
    bank = {}
    n = 0
    for symbol, fraction, leverage, ei in itertools.product(roots, ALLOCATIONS, LEVERAGES, range(len(EXITS))):
        stats, path, _, _ = replay(symbol, fraction, leverage, ei, TRAIN)
        bank[symbol, fraction, leverage, ei] = stats, path[['equity']]
        n += 1
        if n % 14 == 0:
            print(f'training sleeves {n}/168', flush=True)
    rows = []
    for allocation, dl, sl, de, se in itertools.product(ALLOCATIONS, LEVERAGES, LEVERAGES, range(7), range(7)):
        doge = bank['DOGEUSDT', allocation, dl, de]
        sol = bank['SOLUSDT', 1-allocation, sl, se]
        stats, _ = portfolio_metrics([doge[1], sol[1]], [doge[0], sol[0]])
        cfg = dict(doge_allocation=allocation, sol_allocation=1-allocation,
            doge_leverage=dl, sol_leverage=sl, doge_exit_id=de, sol_exit_id=se,
            doge_tp_atr=EXITS[de][0], doge_max_hold_hours=EXITS[de][1],
            sol_tp_atr=EXITS[se][0], sol_max_hold_hours=EXITS[se][1])
        rows.append(dict(id=len(rows), config=cfg, **stats))
    (out/'training.json').write_text(json.dumps(rows, indent=2))
    selection = select_training(rows)
    (out/'selection.json').write_text(json.dumps(selection, indent=2))
    print('LOCKED TRAINING SELECTION', json.dumps(selection), flush=True)
    del bank
    results = []
    for label in ('max_pnl', 'balanced', 'lower_drawdown'):
        chosen = selection[label]
        if chosen is None:
            continue
        cfg = chosen['config']
        for period, dates in PERIODS.items():
            for cost in (8, 15, 30):
                sleeves = [replay('DOGEUSDT', cfg['doge_allocation'], cfg['doge_leverage'], cfg['doge_exit_id'], dates, cost, capture=cost==8),
                           replay('SOLUSDT', cfg['sol_allocation'], cfg['sol_leverage'], cfg['sol_exit_id'], dates, cost, capture=cost==8)]
                stats, path = portfolio_metrics([s[1] for s in sleeves], [s[0] for s in sleeves])
                results.append(dict(strategy=label, period=period, cost_bps=cost, config=cfg, **stats))
                if cost == 8:
                    path.to_csv(out/f'{label}-{period}.csv', index_label='timestamp')
                    (out/f'{label}-{period}-episodes.json').write_text(json.dumps([e for s in sleeves for e in s[0]['closed_episodes']], indent=2))
                    (out/f'{label}-{period}-ledgers.json').write_text(json.dumps([s[3] for s in sleeves], indent=2))
        print('evaluated selected', label, flush=True)
    # Frozen preceding-study controls, evaluated on identical later dates.
    for label, spec in dict(previous_DOGE_2x=('DOGEUSDT', 2., 4), previous_SOL_1x=('SOLUSDT', 1., 4)).items():
        symbol, leverage, ei = spec  # index4 is TP1ATR/72h.
        for period, dates in PERIODS.items():
            s, p, _, _ = replay(symbol, 1., leverage, ei, dates)
            stats, path = portfolio_metrics([p], [s])
            results.append(dict(strategy=label, period=period, cost_bps=8, **stats))
            path.to_csv(out/f'{label}-{period}.csv', index_label='timestamp')
    (out/'summary.json').write_text(json.dumps(dict(selection=selection, results=results), indent=2))
    lines = ['# DOGE/SOL allocation and profit-taking study', '',
        '10,000 USDT initial capital. Training2021–2023; validation2024; testJanuary2025–September2026. Selection frozen before later replay. Historical Binance perpetual proxies, Kalshi contract/fee scenarios.', '',
        '## Training-selected configurations', '']
    for label in ('max_pnl', 'balanced', 'lower_drawdown'):
        lines += [f'**{label}**: '+json.dumps(selection[label]), '']
    lines += ['## Later results', '', '| Strategy | Period | Cost/side | Net P&L | Daily DD | Hourly DD | Profitable closes/month | Median hold hours |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in results:
        lines.append(f"| {r['strategy']} | {r['period']} | {r['cost_bps']}bps | {r['pnl']:,.2f} | {r['max_drawdown']:.2%} | {r['max_hourly_drawdown']:.2%} | {r['profitable_closes_per_month']:.2f} | {r['median_holding_hours']} |")
    lines += ['', '## Limits', '']+protocol['caveats']+['', protocol['profit_frequency'], '', protocol['capital_policy']]
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
    for label in ('max_pnl', 'balanced', 'lower_drawdown', 'previous_DOGE_2x', 'previous_SOL_1x'):
        path = out/f'{label}-post_training.csv'
        if not path.exists():
            continue
        p = pd.read_csv(path, index_col='timestamp', parse_dates=True).equity
        axes[0].plot(p.index, p, label=label.replace('_', ' '))
        dd = p/np.maximum.accumulate(np.maximum(p, 10000))-1
        axes[1].plot(p.index, dd*100)
    axes[0].set_ylabel('USDT equity')
    axes[0].set_title('Locked training selections: January2024–September2026, funding and costs included')
    axes[0].legend()
    axes[1].set_ylabel('Hourly drawdown %')
    fig.tight_layout()
    fig.savefig(out/'comparison.png', dpi=150)
    print(json.dumps([r for r in results if r['period']=='test' and r['cost_bps']==8], indent=2), flush=True)


if __name__ == '__main__':
    main()
