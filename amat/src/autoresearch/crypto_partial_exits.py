"""Four-hour entry calendar with real partial reductions and close-based trails."""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
import numpy as np
import pandas as pd

from autoresearch.crypto_directional import closed_trade_metrics
from autoresearch.crypto_doge_sol import profit_weights
from autoresearch.perpetual_sim import ContractSpec, PerpOrder, PerpetualSimulator, decimal

MINUTE = 60000
REASONS = {0:'hold', 1:'entry', 2:'partial_tp', 3:'trailing_exit', 4:'channel_exit', 5:'time_exit', 6:'baseline_exit'}


def policies():
    rows = [dict(family='baseline', factor=1., fraction=0., trail=None, hold=72)]
    import itertools
    for factor, fraction, trail in itertools.product((.25, .5), (.25, .5), (None, 1., 2.)):
        rows.append(dict(family='preserve', factor=factor, fraction=fraction, trail=trail, hold=72))
    for factor, fraction, trail, hold in itertools.product((.5, 1.), (.25, .5), (1., 2., 3.), (72, 168)):
        rows.append(dict(family='runner', factor=factor, fraction=fraction, trail=trail, hold=hold))
    for family, trail, hold in itertools.product(('ladder', 'trail_only'), (1., 2., 3.), (72, 168)):
        rows.append(dict(family=family, factor=1., fraction=.25 if family=='ladder' else 0., trail=trail, hold=hold))
    return rows


def exit_plan(frame, cfg, original_tp):
    """Anchor entries to original 4h strategy; change only sale/exit management.

    A runner blocks overlapping original entries until flat. High-water marks,
    activation and trail triggers use completed closes, executed next open.
    """
    hourly = frame.resample('1h').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'tradable':'max'})
    base = pd.Series(profit_weights(hourly, original_tp, 72)[:,0], index=hourly.index)
    if cfg['family']=='baseline':
        w = base.reindex(frame.index, method='ffill').to_numpy()
        return w, np.full(len(w), 6, dtype=np.int8)
    grouped = hourly[['open','high','low','close']].where(hourly.tradable>0, axis=0).resample('4h')
    p = grouped.agg({'open':'first','high':'max','low':'min','close':'last'})
    p[grouped.close.count()!=4] = np.nan
    tr = pd.concat([p.high-p.low, (p.high-p.close.shift()).abs(), (p.low-p.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().to_numpy()
    channel = p.low.rolling(60).min().shift(1).to_numpy()
    action_index = p.index+pd.Timedelta(hours=4)
    baseline = base.reindex(action_index).to_numpy()
    level = np.zeros(len(p))
    why = np.zeros(len(p), dtype=np.int8)
    remaining = 0.
    prior_base = 0.
    reference = entry_atr = high = 0.
    entered = stage = 0
    armed = False
    for i, price in enumerate(p.close.to_numpy()):
        b = baseline[i]
        onset = np.isfinite(b) and b>0 and prior_base==0
        if np.isfinite(b):
            prior_base = b
        if not np.isfinite(price) or not np.isfinite(atr[i]):
            level[i] = np.nan
            continue
        exited = False
        if remaining:
            high = max(high, price)
            reason = 0
            if cfg['family']=='preserve' and np.isfinite(b) and b==0:
                reason = 6
            elif price<channel[i]:
                reason = 4
            elif i-entered>=cfg['hold']//4:
                reason = 5
            elif armed and cfg['trail'] is not None and price<=high-cfg['trail']*entry_atr:
                reason = 3
            if reason:
                remaining = 0.
                why[i] = reason
                exited = True
            else:
                distance = price-reference
                if cfg['family']=='ladder':
                    targets = (.5*original_tp, original_tp)
                    # At most one scheduled reduction per close, with actual
                    # next-open fills. No multiple assumed intrabar fills.
                    if stage<2 and distance>=targets[stage]*entry_atr:
                        remaining -= .25
                        stage += 1
                        armed = True
                        why[i] = 2
                elif cfg['family']=='trail_only':
                    if distance>=original_tp*entry_atr:
                        armed = True
                elif not stage and distance>=cfg['factor']*original_tp*entry_atr:
                    remaining = 1-cfg['fraction']
                    stage = 1
                    armed = True
                    why[i] = 2
        if not remaining and not exited and onset:
            remaining = 1.
            reference = high = price
            entry_atr = atr[i]
            entered = i
            stage = 0
            armed = False
            why[i] = 1
        level[i] = remaining*.98
    w = pd.Series(level, index=action_index).reindex(frame.index, method='ffill').to_numpy()
    reason = pd.Series(why, index=action_index).reindex(frame.index, method='ffill').fillna(0).to_numpy(dtype=np.int8)
    return w, reason


def realized_reductions(ledger, terminal_ms, blackout_times):
    """Allocate carried entry costs/funding to each actual reduction, exactly."""
    q = Decimal(0)
    pool = Decimal(0)
    out = []
    for e in ledger:
        if e['event']=='funding':
            if q:
                pool += decimal(e['payment'])
            continue
        if e['event'] not in ('fill','liquidation'):
            continue
        delta, fee = decimal(e['contracts']), decimal(e['fee'])
        if delta>0:
            q += delta
            pool -= fee
            continue
        if q<=0 or -delta>q:
            raise AssertionError('partial research is long-only')
        allocated = pool*(-delta)/q
        net = decimal(e['realized_pnl'])-fee+allocated
        pool -= allocated
        q += delta
        reason = e.get('policy_reason', 'liquidation' if e['event']=='liquidation' else 'rebalance')
        if e['timestamp_ms']==terminal_ms:
            reason = 'terminal'
        elif e['timestamp_ms'] in blackout_times:
            reason = 'blackout'
        out.append(dict(timestamp_ms=e['timestamp_ms'], net_pnl=float(net), contracts=float(-delta),
            reason=reason, natural=reason not in ('rebalance','terminal','blackout')))
    if q or abs(pool)>Decimal('1e-16'):
        raise AssertionError('reduction costs must fully reconcile at terminal close')
    return out


def replay_exit_plan(data, weights, reasons, dates, *, capital, leverage, symbol, specs, cost=8, capture=False, full_path=False):
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


def reduction_metrics(sleeves, end_ms):
    from autoresearch.crypto_fast_breakout import combined_metrics
    stats, path = combined_metrics(sleeves, end_ms=end_ms)
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


def select_partial_training(rows, baseline_id=0):
    baseline = next(r for r in rows if r['id']==baseline_id)
    eligible = [r for r in rows if r['liquidations']==0 and r['pnl']>0]
    best = max(eligible, key=lambda r:r['pnl']) if eligible else None
    qualified = [r for r in eligible if r['pnl']>=baseline['pnl'] and r['max_drawdown']>=baseline['max_drawdown']
        and r['profitable_realization_days_per_month']>=1.25*baseline['profitable_realization_days_per_month']]
    return dict(baseline=baseline, max_training_pnl=best,
        no_sacrifice=max(qualified, key=lambda r:r['pnl']) if qualified else None,
        no_sacrifice_eligible=len(qualified))
