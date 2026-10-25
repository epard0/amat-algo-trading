"""Frozen exits with causal daily sizing; copied accounting kernel is parity tested."""
from decimal import Decimal, ROUND_DOWN
import numpy as np
import pandas as pd
from autoresearch.crypto_partial_exits import MINUTE,REASONS,realized_reductions
from autoresearch.crypto_directional import closed_trade_metrics
from autoresearch.perpetual_sim import ContractSpec,PerpOrder,PerpetualSimulator,decimal

SIZE_GRID=(dict(name='fixed-0.5',fixed=.5),dict(name='fixed-1',fixed=1.),dict(name='fixed-1.5',fixed=1.5),dict(name='fixed-2',fixed=2.),dict(name='vol20',target=.2),dict(name='vol30',target=.3),dict(name='vol40',target=.4))


def sizing_path(frame,cfg):
    if 'fixed' in cfg:return np.full(len(frame),cfg['fixed'])
    h=frame.close.resample('1h').last()
    vol=np.log(h).diff().rolling(30*24,min_periods=30*24).std(ddof=1)*np.sqrt(365*24)
    vol.index=vol.index+pd.Timedelta(hours=1)
    daily=vol.reindex(pd.date_range(frame.index[0].normalize(),frame.index[-1].normalize(),freq='1D',tz='UTC'),method='ffill')
    scale=(cfg['target']/daily).clip(.25,2.).fillna(.25)
    return scale.reindex(frame.index,method='ffill').to_numpy()


def fresh_plan(weights,reasons,index,dates):
    """Start flat and await a new true signal entry, never buy a carried position."""
    w=weights.copy();why=reasons.copy()
    first=index.searchsorted(pd.Timestamp(dates[0],tz='UTC'))
    if first and np.isfinite(w[first]) and w[first]>0:
        prior=np.nan_to_num(w[:first],nan=0.)
        if prior[-1]>0:
            after=np.flatnonzero(np.nan_to_num(w[first:],nan=1.)==0)
            stop=first+int(after[0]) if len(after) else len(w)
            w[first:stop]=0;why[first:stop]=0
    return w,why


def replay_sized_plan(data, weights, reasons, dates, *, capital, leverage, symbol, specs, cost=8, capture=False, full_path=False, sizing=None):
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
        desired = weights[i]*(sizing[i] if sizing is not None else leverage)
        level = weights[i]
        sign = level if np.isfinite(level) else np.nan
        rebalance = np.isfinite(sign) and (sign!=previous or timestamp%86400000==0)
        if rebalance and not sim.halted and a['tradable'][i]>0:
            op = decimal(a['open'][i])
            reducing = previous>0 and level<previous and sim.contracts>0
            if reducing:
                qty = (sim.contracts*decimal(level)/decimal(previous)).to_integral_value(rounding=ROUND_DOWN)
            else:
                qty = (decimal(desired)*max(Decimal(0), sim.equity)/(spec.multiplier*op)).to_integral_value(rounding=ROUND_DOWN)
                if level<.98 and previous>0:
                    qty = min(qty, sim.contracts)  # Never buy back a partial TP.
            reason = 'blackout' if data['blackout'][i] else REASONS.get(int(reasons[i]), 'hold') if reducing else 'entry' if previous==0 else 'rebalance'
            delta = qty-sim.contracts
            if delta:
                direction = 1 if delta>0 else -1
                try:
                    ledger_before = len(sim.ledger)
                    sim.submit(PerpOrder(f'{timestamp}', symbol, 'bid' if delta>0 else 'ask', abs(delta), sim._execution_price(op, direction)),
                        bid=op, ask=op, mark=a['mark_open'][i], timestamp_ms=timestamp+6000)
                    for fill in sim.ledger[ledger_before:]:
                        if fill['event']=='fill':
                            fill['policy_reason'] = reason
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
    reductions = realized_reductions(sim.ledger, terminal_ms, blackout_exits)
    if not np.isclose(sum(r['net_pnl'] for r in reductions), pnl, atol=1e-6):
        raise AssertionError('partial realization identity')
    stats['reductions'] = reductions
    for reduction in reductions:
        reduction['symbol'] = symbol
    return stats, path, sim.ledger if capture else None



def reduction_metrics(sleeves, end_ms, capital=10000):
    from autoresearch.crypto_fast_breakout import combined_metrics
    stats, path = combined_metrics(sleeves, capital=capital, end_ms=end_ms)
    rows = [r for s in sleeves for r in s[0]['reductions']]
    if not np.isclose(sum(r['net_pnl'] for r in rows), stats['pnl'], atol=1e-6):
        raise AssertionError('portfolio reduction identity')
    days = len(path.equity.resample('1D').last())
    months = days/30.4375
    by_day = {}
    natural_days = set()
    profit_times = []
    for r in rows:
        if r['reason'] in ('terminal', 'blackout'):
            continue
        day = str(pd.Timestamp(r['timestamp_ms'], unit='ms', tz='UTC').date())
        by_day[day] = by_day.get(day, 0.)+r['net_pnl']
        if r['natural']:
            natural_days.add(day)
            if r['net_pnl']>0:
                profit_times.append(r['timestamp_ms'])
    profitable_days = sum(by_day[d]>0 for d in natural_days)
    stats.update(profitable_realizations=len(profit_times), profitable_realizations_per_month=len(profit_times)/months,
        profitable_realization_days=profitable_days, profitable_realization_days_per_month=profitable_days/months,
        profitable_partial_sales=sum(r['natural'] and r['reason']=='partial_tp' and r['net_pnl']>0 for r in rows),
        max_days_between_profit_realizations=float(np.max(np.diff(sorted(profit_times)))/86400000) if len(profit_times)>1 else None)
    return stats, path

