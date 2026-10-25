"""Exact event-driven perpetual wallets with canonical minute mark paths.

Wallet mutations use the existing Decimal simulator. Between mutations, fixed
contracts make equity affine in price, permitting vectorized marks and detection
of the first minute crossing the liquidation threshold. No synthetic minute bars.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
import numpy as np
import pandas as pd

from autoresearch.crypto_directional import closed_trade_metrics
from autoresearch.perpetual_sim import ContractSpec, PerpOrder, PerpetualSimulator, decimal

MINUTE = 60000


def short_weights(frame, minutes, entry_hours, tp_atr, max_hold_hours):
    grouped = frame[['open', 'high', 'low', 'close']].where(frame.tradable>0, axis=0).resample(f'{minutes}min')
    prices = grouped.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    prices[grouped.close.count()!=minutes] = np.nan
    n = entry_hours*60//minutes
    exit_n = max(1, n//4)
    c, hi, lo = prices.close, prices.high, prices.low
    upper = hi.rolling(n).max().shift(1).to_numpy()
    lower = lo.rolling(exit_n).min().shift(1).to_numpy()
    tr = pd.concat([hi-lo, (hi-c.shift()).abs(), (lo-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().to_numpy()
    out = np.zeros(len(c))
    state = 0
    reference = entry_atr = 0.
    entered = 0
    for i, price in enumerate(c.to_numpy()):
        if not np.isfinite(price) or not np.isfinite(upper[i]) or not np.isfinite(atr[i]):
            out[i] = np.nan
            continue
        exited = False
        if state and (price<lower[i] or price-reference>=tp_atr*entry_atr or i-entered>=max_hold_hours*60//minutes):
            state = 0
            exited = True
        if not state and not exited and price>upper[i]:
            state = 1
            reference, entry_atr, entered = price, atr[i], i
        out[i] = state*.98
    series = pd.Series(out, index=prices.index+pd.Timedelta(minutes=minutes))
    return series.reindex(frame.index, method='ffill').to_numpy()


def prepare(frame, funding):
    index = frame.index
    ms = index.as_unit('ms').asi8
    if len(index)<2 or not (np.diff(ms)==MINUTE).all():
        raise ValueError('canonical minute grid required')
    events = {}
    for when, rate in funding.values():
        pos = int(np.searchsorted(ms, when, side='right')-1)
        if 0<=pos<len(ms) and ms[pos]<=when<ms[pos]+MINUTE:
            if pos in events:
                raise ValueError('duplicate funding minute')
            if when-ms[pos]>=6000:
                raise ValueError('funding timestamp falls after model execution offset')
            events[pos] = (int(when), rate)
    unavailable = ~frame['mark_available'].to_numpy(dtype=bool) if 'mark_available' in frame else np.zeros(len(frame), dtype=bool)
    blackout = unavailable.copy()
    blackout[:-1] |= unavailable[1:]
    return dict(index=index, ms=ms, a={c: frame[c].to_numpy() for c in frame}, funding=events,
        unavailable=unavailable, blackout=blackout)


def replay_events(data, weights, dates, *, capital, leverage, symbol, specs, cost=8, capture=False, full_path=False):
    """Integer contracts, actual ticks, funding before orders, minute liquidation.

    Signal changes, midnight resizing and funding are mutation boundaries. All
    intermediate minute opens/lows/closes are checked for liquidation.
    """
    index, ms, a = data['index'], data['ms'], data['a']
    if data['blackout'].any():
        weights = weights.copy()
        weights[data['blackout']] = 0.
    first = int(index.searchsorted(pd.Timestamp(dates[0], tz='UTC')))
    stop = int(index.searchsorted(pd.Timestamp(dates[1], tz='UTC')+pd.Timedelta(days=1)))
    if first>=stop:
        raise ValueError('empty replay')
    spec = ContractSpec(symbol, multiplier=specs['multiplier'], quantity_step=1, price_tick=specs['price_tick'],
        max_leverage=max(1, leverage), maintenance_rate='.005', fee_rate='.0004',
        slippage_rate=str((cost-4)/10000), liquidation_fee_rate='.005')
    sim = PerpetualSimulator(spec, capital)
    w = np.nan_to_num(weights[first:stop], nan=0.)
    changes = np.flatnonzero(np.diff(w)!=0)+first+1
    availability = np.flatnonzero(np.diff(a['tradable'][first:stop])!=0)+first+1
    mark_availability = np.flatnonzero(np.diff(data['unavailable'][first:stop])!=0)+first+1
    midnight = np.flatnonzero(ms[first:stop]%86400000==0)+first
    fund_positions = [p for p in data['funding'] if first<=p<stop]
    bounds = np.unique(np.r_[first, changes, availability, mark_availability, midnight, fund_positions, stop]).astype(int)
    equity = np.empty(stop-first)
    previous = 0
    rejected = 0
    multiplier = float(spec.multiplier)
    for i, end in zip(bounds[:-1], bounds[1:], strict=True):
        timestamp = int(ms[i])
        if data['unavailable'][i] and sim.contracts:
            raise AssertionError('missing mark must never price an open position')
        sim.mark_to_market(a['mark_open'][i], timestamp, snapshot=False)
        event = data['funding'].get(i)
        if event:
            when, rate = event
            sim.apply_funding(event_id=str(when), rate=rate, mark=a['mark_open'][i], timestamp_ms=when)
        desired = weights[i]*leverage
        sign = np.sign(desired) if np.isfinite(desired) else np.nan
        rebalance = np.isfinite(sign) and (sign!=previous or timestamp%86400000==0)
        if rebalance and not sim.halted and a['tradable'][i]>0:
            op = decimal(a['open'][i])
            qty = (decimal(desired)*max(Decimal(0), sim.equity)/(spec.multiplier*op)).to_integral_value(rounding=ROUND_DOWN)
            delta = qty-sim.contracts
            if delta:
                direction = 1 if delta>0 else -1
                try:
                    sim.submit(PerpOrder(f'{timestamp}', symbol, 'bid' if delta>0 else 'ask', abs(delta), sim._execution_price(op, direction)),
                        bid=op, ask=op, mark=a['mark_open'][i], timestamp_ms=timestamp+6000)
                except ValueError as exc:
                    if 'initial margin' not in str(exc):
                        raise
                    rejected += 1
        if np.isfinite(sign) and a['tradable'][i]>0:
            previous = sign
        sim.mark_to_market(a['mark_low'][i] if sim.contracts>0 else a['mark_high'][i], timestamp+6001, snapshot=False)
        sim.mark_to_market(a['mark_close'][i], timestamp+MINUTE-1, snapshot=False)
        equity[i-first] = float(sim.equity)
        if end<=i+1:
            continue
        if not sim.contracts:
            equity[i+1-first:end-first] = float(sim.cash)
            continue
        q = float(sim.contracts)*multiplier
        cash, entry = float(sim.cash), float(sim.average_entry)
        close = a['mark_close'][i+1:end]
        adverse = a['mark_low'][i+1:end] if q>0 else a['mark_high'][i+1:end]
        eq_adverse = cash+q*(adverse-entry)
        breached = np.flatnonzero(eq_adverse<=abs(q)*adverse*float(spec.maintenance_rate)+1e-9)
        equity[i+1-first:end-first] = cash+q*(close-entry)
        if len(breached):
            k = i+1+int(breached[0])
            ts = int(ms[k])
            sim.mark_to_market(a['mark_open'][k], ts, snapshot=False)
            sim.mark_to_market(a['mark_low'][k] if sim.contracts>0 else a['mark_high'][k], ts+6001, snapshot=False)
            sim.mark_to_market(a['mark_close'][k], ts+MINUTE-1, snapshot=False)
            if not sim.halted:
                raise AssertionError('vector threshold must trigger Decimal liquidation')
            equity[k-first:end-first] = float(sim.equity)
    last = stop-1
    if sim.contracts:
        sim.mark_to_market(a['mark_close'][last], int(ms[last])+MINUTE-1, snapshot=False)
        delta = -sim.contracts
        price = decimal(a['close'][last])
        direction = 1 if delta>0 else -1
        sim.submit(PerpOrder('final', symbol, 'bid' if delta>0 else 'ask', abs(delta), sim._execution_price(price, direction), reduce_only=True),
            bid=price, ask=price, mark=sim.mark, timestamp_ms=int(ms[last])+MINUTE-1)
    equity[-1] = float(sim.equity)
    pnl = float(sim.equity)-capital
    if not np.isclose(pnl, float(sim.realized_pnl+sim.funding_pnl-sim.fees), atol=1e-6):
        raise AssertionError('wallet identity')
    # Hourly index is sufficient for this helper's episode extraction; replace
    # its duration-derived count below with the exact canonical-minute duration.
    ep = closed_trade_metrics([sim], index[first:stop])
    if not np.isclose(ep['episode_net_pnl'], pnl, atol=1e-6):
        raise AssertionError('episode identity')
    terminal_ms = int(ms[last])+MINUTE-1
    blackout_exits = set(int(t)+6000 for t in ms[data['blackout']])
    for episode in ep['closed_episodes']:
        episode['forced_blackout'] = episode['exit_ms'] in blackout_exits
    natural = [e for e in ep['closed_episodes'] if e['exit_ms']!=terminal_ms and not e['forced_blackout']]
    days = (stop-first)/1440
    positive = [e for e in natural if e['net_pnl']>0]
    stats = dict(pnl=pnl, funding_pnl=float(sim.funding_pnl), fees=float(sim.fees), realized_price_pnl=float(sim.realized_pnl),
        liquidations=sum(e['event']=='liquidation' for e in sim.ledger), margin_rejections=rejected,
        profitable_closes=len(positive), natural_closes=len(natural), closed_episodes=ep['closed_episodes'],
        profitable_closes_per_month=len(positive)/(days/30.4375))
    path = pd.DataFrame({'equity': equity}, index=index[first:stop])
    if not full_path:
        path = path.resample('1D').last()
    return stats, path, sim.ledger if capture else None


def combined_metrics(sleeves, capital=10000, *, end_ms=None):
    paths = [s[1] for s in sleeves]
    if any(not p.index.equals(paths[0].index) for p in paths):
        raise ValueError('sleeve calendars differ')
    path = pd.DataFrame({'equity': sum(p.equity for p in paths)})
    daily = path.equity.resample('1D').last()
    eq = np.r_[capital, daily.to_numpy()]
    pnl = float(eq[-1]-capital)
    if not np.isclose(pnl, sum(s[0]['pnl'] for s in sleeves), atol=1e-6):
        raise AssertionError('combined wallet identity')
    episodes = [e for s in sleeves for e in s[0]['closed_episodes'] if e['exit_ms']!=end_ms and not e.get('forced_blackout', False)]
    positive = sorted(e['exit_ms'] for e in episodes if e['net_pnl']>0)
    month_count = len(daily)/30.4375
    ret = daily.pct_change()
    ret.iloc[0] = daily.iloc[0]/capital-1
    stats = dict(pnl=pnl, ending_equity=float(eq[-1]), max_drawdown=float((eq/np.maximum.accumulate(eq)-1).min()),
        profitable_closes_per_month=len(positive)/month_count, profitable_closes=len(positive), natural_closes=len(episodes),
        median_holding_hours=float(np.median([e['holding_hours'] for e in episodes])) if episodes else None,
        max_days_between_profit_closes=float(np.max(np.diff(positive))/86400000) if len(positive)>1 else None,
        funding_pnl=sum(s[0]['funding_pnl'] for s in sleeves), fees=sum(s[0]['fees'] for s in sleeves),
        liquidations=sum(s[0]['liquidations'] for s in sleeves), margin_rejections=sum(s[0]['margin_rejections'] for s in sleeves),
        annualized_volatility=float(ret.std()*np.sqrt(365)))
    if len(path)>len(daily):
        minute_eq = np.r_[capital, path.equity.to_numpy()]
        stats['max_minute_close_drawdown'] = float((minute_eq/np.maximum.accumulate(minute_eq)-1).min())
    return stats, path
