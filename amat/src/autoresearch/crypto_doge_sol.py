"""Training-only allocation search with exact independent perpetual sleeves."""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.crypto_directional import directional_weights


def profit_weights(frame, tp_atr=None, max_hold_hours=None):
    """Completed 4h 20/10-day long breakouts, next-open execution.

    Entry ATR/reference are signal-close values, not assumed execution fills.
    No same-bar re-entry; holding cap is conditional on a TP policy.
    """
    if tp_atr is None:
        return directional_weights({'asset': frame}, dict(sizing='full', leverage=1, tp_atr=None), {'asset': 1})
    if max_hold_hours is None or max_hold_hours % 4:
        raise ValueError('TP needs a holding cap divisible by four hours')
    f = frame[['open', 'high', 'low', 'close']].where(frame.tradable > 0, axis=0)
    grouped = f.resample('4h')
    prices = grouped.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    prices[grouped.close.count() != 4] = np.nan
    c, hi, lo = prices.close, prices.high, prices.low
    entry = hi.rolling(120).max().shift(1).to_numpy()
    exit_lo = lo.rolling(60).min().shift(1).to_numpy()
    tr = pd.concat([hi-lo, (hi-c.shift()).abs(), (lo-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().to_numpy()
    state = 0
    ref = entry_atr = 0.
    entered = 0
    out = np.zeros(len(c))
    for i, price in enumerate(c.to_numpy()):
        if not np.isfinite(price) or not np.isfinite(entry[i]):
            out[i] = np.nan
            continue
        exited = False
        if state and (price < exit_lo[i] or price-ref >= tp_atr*entry_atr or i-entered >= max_hold_hours//4):
            state = 0
            exited = True
        if not state and not exited and price > entry[i]:
            state = 1
            ref, entry_atr, entered = price, atr[i], i
        out[i] = state*.98
    series = pd.Series(out, index=prices.index+pd.Timedelta(hours=4))
    return series.reindex(frame.index, method='ffill').to_numpy()[:, None]


def portfolio_metrics(paths, sleeve_stats, capital=10000):
    """Sum collateral sleeves without fictitious transfers or scaled fills."""
    if not paths or any(not p.index.equals(paths[0].index) for p in paths):
        raise ValueError('sleeve time grids must match')
    path = pd.DataFrame({'equity': sum(p.equity for p in paths)})
    daily = path.equity.resample('1D').last()
    eq = np.r_[capital, daily.to_numpy()]
    ret = daily.pct_change()
    ret.iloc[0] = daily.iloc[0]/capital-1
    dd = eq/np.maximum.accumulate(eq)-1
    hourly = np.r_[capital, path.equity.to_numpy()]
    episodes = [e for stats in sleeve_stats for e in stats['closed_episodes']]
    end_ms = int(path.index[-1].timestamp()*1000)+3600000-1
    natural = [e for e in episodes if e['exit_ms'] != end_ms]
    profits = sorted(e['exit_ms'] for e in natural if e['net_pnl'] > 0)
    months = len(daily)/30.4375
    pnl = float(eq[-1]-capital)
    accounted = sum(s['realized_price_pnl']+s['funding_pnl']-s['fees'] for s in sleeve_stats)
    if not np.isclose(pnl, accounted, atol=1e-6):
        raise AssertionError('combined wallet identity failed')
    weekly = ret.resample('W-SUN').apply(lambda x: float(np.prod(1+x)-1))
    stats = dict(pnl=pnl, ending_equity=float(eq[-1]), total_return=pnl/capital,
        max_drawdown=float(dd.min()), max_hourly_drawdown=float((hourly/np.maximum.accumulate(hourly)-1).min()),
        annualized_volatility=float(ret.std()*np.sqrt(365)),
        profitable_closes=len(profits), profitable_closes_per_month=len(profits)/months,
        natural_closes=len(natural), forced_terminal_closes=len(episodes)-len(natural),
        median_holding_hours=float(np.median([e['holding_hours'] for e in natural])) if natural else None,
        max_days_between_profit_closes=float(np.max(np.diff(profits))/86400000) if len(profits)>1 else None,
        positive_week_fraction=float((weekly>0).mean()),
        fees=sum(s['fees'] for s in sleeve_stats), funding_pnl=sum(s['funding_pnl'] for s in sleeve_stats),
        liquidations=sum(s['liquidations'] for s in sleeve_stats), margin_rejections=sum(s['margin_rejections'] for s in sleeve_stats))
    return stats, path


def select_training(rows):
    eligible = [r for r in rows if r['liquidations'] == 0 and r['natural_closes'] >= 6 and r['pnl'] > 0]
    if not eligible:
        raise ValueError('no active profitable training portfolio')
    constrained = [r for r in eligible if r['max_drawdown'] >= -.35 and r['profitable_closes_per_month'] >= 2]
    conservative = [r for r in eligible if r['max_drawdown'] >= -.25 and r['profitable_closes_per_month'] >= 2]
    return dict(max_pnl=max(eligible, key=lambda r: r['pnl']),
        balanced=max(constrained, key=lambda r: r['pnl']) if constrained else None,
        lower_drawdown=max(conservative, key=lambda r: r['pnl']) if conservative else None,
        eligible_count=len(eligible), balanced_eligible_count=len(constrained), conservative_eligible_count=len(conservative))
