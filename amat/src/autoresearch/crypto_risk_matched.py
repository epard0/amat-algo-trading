"""Training-calibrated risk comparison of locked crypto spot selections."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from autoresearch.crypto_spot_study import SYMBOLS, aggregate, replay, targets
from autoresearch.genetic.data import load_crypto

PERIODS = {
    'validation': ('2022-01-01', '2022-12-31'),
    'test': ('2023-01-01', '2023-12-31'),
    'external': ('2024-01-01', '2026-09-30'),
}


def volatility(returns):
    return float(returns.std(ddof=1) * np.sqrt(365))


def paired_intervals(a, b, seed=20261019, samples=4000):
    """Circular 30-day paired blocks. Annualized arithmetic excess, Sharpe gap."""
    if not a.index.equals(b.index):
        raise ValueError('paired calendars differ')
    x = np.column_stack([a, b]); n = len(x)
    rng = np.random.default_rng(seed); estimates = []
    for _ in range(samples):
        starts = rng.integers(0, n, size=(n + 29) // 30)
        ids = ((starts[:, None] + np.arange(30)) % n).ravel()[:n]
        z = x[ids]; means = z.mean(axis=0); sd = z.std(axis=0, ddof=1)
        estimates.append([365 * (means[0] - means[1]),
                          np.sqrt(365) * (means[0] / sd[0] - means[1] / sd[1])])
    estimates = np.asarray(estimates)
    return {
        'annualized_mean_excess_95ci': np.quantile(estimates[:, 0], [.025, .975]).tolist(),
        'annualized_mean_excess_family98_75ci': np.quantile(estimates[:, 0], [.00625, .99375]).tolist(),
        'sharpe_difference_95ci': np.quantile(estimates[:, 1], [.025, .975]).tolist(),
        'annualized_mean_excess': float(365 * (x[:, 0] - x[:, 1]).mean()),
    }


def calibrate(frames, base, desired_vol):
    """Solve for a cash allocation using training outcomes only; never exceed 1x."""
    def objective(scale):
        _, _, ret = replay(frames, base * scale, '2018-01-01', '2021-12-31', 15)
        return volatility(ret) - desired_vol
    top = objective(1.0)
    if top < 0:
        raise ValueError('control cannot reach training risk without leverage')
    return float(brentq(objective, .00001, 1.0, xtol=1e-5))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); out = args.output; out.mkdir(parents=True, exist_ok=False)
    parent = Path('outputs/crypto-spot-btc-eth-2026-10-19-v2')
    source = Path('data/research/crypto-spot-2018-2026-09')
    selection = json.loads((parent / 'selection.json').read_text())
    protocol = {
        'calibration_period': ['2018-01-01', '2021-12-31'], 'evaluation_periods': PERIODS,
        'risk_definition': 'sample standard deviation of daily net portfolio returns annualized sqrt(365)',
        'matching': 'solve fixed control exposure scale on training at 15bps; freeze across later periods/costs; no leverage',
        'controls': ['constant passive BTC/ETH sleeve allocation with daily rebalancing and residual cash',
                     'always long with causal 30-day asset volatility sizing, scaled to match training portfolio volatility'],
        'primary': 'original global training winner (4h) vs constant passive on external period at 15bps',
        'secondary': '1h and volatility-sized controls; 98.75% Bonferroni intervals for four external comparisons',
        'costs_bps_per_side': [15, 30, 50], 'cash_yield': 0,
        'evidence_status': 'retrospective diagnostic; these evaluation periods were already inspected in the preceding study',
        'decision': 'positive primary excess mean with 95% paired-block lower bound >0; lower bound >0 at family-adjusted confidence for secondary claims; all claims remain retrospective',
        'bootstrap': '4000 circular paired 30-day moving blocks; seed 20261019; fixed policies only, no correction of original search',
        'selection_sha256': hashlib.sha256((parent/'selection.json').read_bytes()).hexdigest(),
        'manifest_sha256': hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),
        'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'replay_sha256': hashlib.sha256(Path(__file__).with_name('crypto_spot_study.py').read_bytes()).hexdigest(),
    }
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2))
    data = load_crypto(source, SYMBOLS, start=dt.date(2018, 1, 1), end=dt.date(2026, 9, 30))
    (out/'data-audit.json').write_text(json.dumps(data.manifest, indent=2))
    calibration = {}; configurations = {}
    # Finish and save all training calibration before any evaluation replay.
    for h in (1, 4):
        frames = aggregate(data, h); cfg = selection['by_timeframe'][str(h)]['config']
        breakout = targets(frames, cfg)
        _, _, training = replay(frames, breakout, '2018-01-01', '2021-12-31', 15)
        desired = volatility(training)
        constant = np.ones_like(breakout)
        sized = np.column_stack([
            (0.20 / (f.close.pct_change(fill_method=None).rolling(30*24//h).std()
                     * np.sqrt(365*24/h))).clip(0, 1).shift(1).to_numpy()
            for f in frames.values()])
        controls = {'constant-passive': constant, 'volatility-passive': sized}
        policies = {'breakout': breakout}; scales = {}
        for name, base in controls.items():
            scale = calibrate(frames, base, desired)
            policies[name] = base * scale
            _, _, ret = replay(frames, policies[name], '2018-01-01', '2021-12-31', 15)
            scales[name] = dict(scale=scale, achieved_training_vol=volatility(ret))
        calibration[str(h)] = dict(breakout_training_vol=desired, controls=scales, config=cfg)
        configurations[h] = frames, policies
        print(f'{h}h training calibration complete', flush=True)
    (out/'calibration.json').write_text(json.dumps(calibration, indent=2))
    results = []; daily = {}; comparisons = []
    for h, (frames, policies) in configurations.items():
        for period, dates in PERIODS.items():
            for cost in (15, 30, 50):
                for name, target in policies.items():
                    stats, path, ret = replay(frames, target, *dates, cost)
                    stats['annualized_volatility'] = volatility(ret)
                    results.append(dict(timeframe=h, period=period, cost_bps=cost, policy=name, **stats))
                    path.to_csv(out/f'{h}h-{period}-{cost}bps-{name}.csv', index_label='timestamp')
                    daily[h, period, cost, name] = ret
        for period in PERIODS:
            a = daily[h, period, 15, 'breakout']
            for name in ('constant-passive', 'volatility-passive'):
                b = daily[h, period, 15, name]
                ci = paired_intervals(a, b)
                comparisons.append(dict(timeframe=h, period=period, control=name,
                                        realized_vol_ratio=volatility(a)/volatility(b), **ci))
    primary = next(c for c in comparisons if c['timeframe']==4 and c['period']=='external' and c['control']=='constant-passive')
    summary = dict(results=results, comparisons=comparisons,
                   primary_pass=primary['annualized_mean_excess_95ci'][0]>0,
                   evidence_status=protocol['evidence_status'])
    (out/'summary.json').write_text(json.dumps(summary, indent=2))
    lines = ['# BTC/ETH risk-matched comparison', '',
             'Risk is calibrated on 2018–2021 only. Later realized risk can diverge. This is a retrospective diagnostic on previously inspected periods.', '',
             '| Bars | Period | Cost/side | Policy | Return | CAGR | Volatility | Max DD | Sharpe |',
             '|---|---|---:|---|---:|---:|---:|---:|---:|']
    for r in results:
        lines.append(f"| {r['timeframe']}h | {r['period']} | {r['cost_bps']}bps | {r['policy']} | {r['total_return']:.2%} | {r['cagr']:.2%} | {r['annualized_volatility']:.2%} | {r['max_drawdown']:.2%} | {r['sharpe']:.2f} |")
    lines += ['', '## Paired comparisons at primary cost', '',
              '| Bars | Period | Control | Annualized mean excess | 95% interval | Realized vol ratio |',
              '|---|---|---|---:|---|---:|']
    for c in comparisons:
        lo, hi = c['annualized_mean_excess_95ci']
        lines.append(f"| {c['timeframe']}h | {c['period']} | {c['control']} | {c['annualized_mean_excess']:.2%} | [{lo:.2%}, {hi:.2%}] | {c['realized_vol_ratio']:.3f} |")
    lines += ['', f"Primary comparison passes statistical criterion: {summary['primary_pass']}",
              '', 'Cash earns zero. No taxes, capacity model, or measured quote fills. Calibrated passive allocations rebalance daily within independent sleeves. Full original search multiplicity and prior access are not erased by this comparison.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 7))
    for ax, h in zip(axes, (1, 4)):
        for name in configurations[h][1]:
            ret = daily[h, 'external', 15, name]
            ax.plot(ret.index, 10000*(1+ret).cumprod(), label=name)
        ax.set_title(f'{h}h: training-risk-matched controls; 15bps per side')
        ax.set_ylabel('USDT equity'); ax.legend()
    fig.tight_layout(); fig.savefig(out/'equity.png', dpi=150)
    shutil.copy2(__file__, out/Path(__file__).name)
    print(json.dumps(dict(calibration=calibration, primary=primary), indent=2))


if __name__ == '__main__':
    main()
