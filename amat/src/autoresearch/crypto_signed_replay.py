"""Signed pooled ADX replay; same broker accounting and persistent initial-equity halt."""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
import sys
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'kalshi-perps/src'))
from kalshi_perps.paper import fill,equity
from kalshi_perps.leverage import maximum_notional,margin_required
from kalshi_perps.domain import dec


def cost_bps(curves,ticker,side,notional,case):
    table=curves[ticker][side]
    supported=[r for r in table if r['full_coverage_fraction']>=.95]
    if not supported or notional>supported[-1]['notional']:raise ValueError('order outside observed depth calibration')
    # Next larger measured order size; no optimistic extrapolation.
    r=next(r for r in supported if r['notional']>=notional)
    return r['median_bps'] if case=='median' else r['p95_bps'] if case=='p95' else 2*r['p95_bps']


def replay_signed(data,plans,instruments,metadata,curves,dates,leverage,case='median',capital=10000,guard_fraction=.35):
    symbols=list(data);index=data[symbols[0]]['index'];ms=data[symbols[0]]['ms']
    first=index.searchsorted(pd.Timestamp(dates[0],tz='UTC'));stop=index.searchsorted(pd.Timestamp(dates[1],tz='UTC')+pd.Timedelta(days=1))
    state=dict(cash=str(capital),fees='0',realized_pnl='0',funding='0',positions={s:dict(count='0',entry='0',mark='0') for s in symbols},leverage=metadata)
    paths=np.empty(stop-first);ledger=[];pool={s:dec(0) for s in symbols};episodes={};closed=[];reductions=[];maxlev=0.;rejections=liquidations=0;halted=False
    guard_triggered=False;guard_ms=None;guard_equity=None;guard_positions=None;first_halt_cause=None;funding_after_guard=dec(0);blackout_closes_after_guard=0
    if guard_fraction is not None and not 0<guard_fraction<1:raise ValueError('guard fraction must lie between0and1')
    guard_threshold=dec(capital)*(1-dec(guard_fraction)) if guard_fraction is not None else None
    weights={};reasons={};bounds={first,stop}
    for s in symbols:
        w,why=plans[s];w=w.copy();w[data[s]['blackout']]=0
        weights[s]=w;reasons[s]=why
        bounds.update((np.flatnonzero(np.diff(np.nan_to_num(w[first:stop]))!=0)+first+1).tolist())
        bounds.update((np.flatnonzero(np.diff(data[s]['a']['tradable'][first:stop])!=0)+first+1).tolist())
        bounds.update((np.flatnonzero(np.diff(data[s]['unavailable'][first:stop])!=0)+first+1).tolist())
        bounds.update((np.flatnonzero(np.diff(data[s]['blackout'][first:stop])!=0)+first+1).tolist())
        bounds.update(i for i in data[s]['funding'] if first<=i<stop)
    bounds.update((np.flatnonzero(ms[first:stop]%86400000==0)+first).tolist());bounds=sorted(bounds)
    prior={s:0. for s in symbols}
    def mark(i,key):
        for s in symbols:
            if data[s]['unavailable'][i] and dec(state['positions'][s]['count']):raise AssertionError('held missing mark')
            state['positions'][s]['mark']=str(data[s]['a'][key][i]*float(instruments[s].contract_size))
    def execute(s,delta,underlying,when,reason,liquidation=False):
        nonlocal rejections
        if not delta:return False
        inst=instruments[s];op=dec(underlying)*inst.contract_size;side='asks' if delta>0 else 'bids'
        bps=cost_bps(curves,s,side,float(abs(delta)*op),case)
        slip=dec(bps)/10000
        price=(op*(1+slip if delta>0 else 1-slip)/inst.tick).to_integral_value(rounding=ROUND_CEILING if delta>0 else ROUND_FLOOR)*inst.tick
        if price<=0:raise ValueError('nonpositive execution price')
        old=dec(state['positions'][s]['count']);fee_rate=dec('.0004')+(dec('.005') if liquidation else 0)
        trial=deepcopy(state);result=fill(trial,s,delta,price,fee_rate)
        if (not old or old*delta>0) and equity(trial)<margin_required(trial,initial=True):rejections+=1;return False
        state.update(trial)
        fee=dec(result['fee']);realized=dec(result['realized_pnl'])
        if not old or old*delta>0:
            pool[s]-=fee
            if not old:episodes[s]=dict(entry_ms=when,net=dec(0))
        else:
            allocated=pool[s]*abs(delta)/abs(old);net=realized-fee+allocated;pool[s]-=allocated
            episodes[s]['net']+=net
            natural=reason not in ('rebalance','terminal','blackout','liquidation')
            reductions.append(dict(symbol=s,timestamp_ms=when,net_pnl=float(net),reason=reason,natural=natural,contracts=float(abs(delta))))
            if not dec(state['positions'][s]['count']):
                ep=episodes.pop(s);closed.append(dict(symbol=s,entry_ms=ep['entry_ms'],exit_ms=when,net_pnl=float(ep['net']),reason=reason))
        ledger.append(dict(symbol=s,timestamp_ms=when,reason=reason,execution_slippage_bps=bps,**result));return True
    def adverse_mark(s,key):
        return 'mark_high' if key=='mark_low' and dec(state['positions'][s]['count'])<0 else key
    def liquidate_if_needed(i,key):
        nonlocal halted,liquidations,first_halt_cause
        for s in symbols:
            if data[s]['unavailable'][i] and dec(state['positions'][s]['count']):raise AssertionError('held missing mark')
            state['positions'][s]['mark']=str(data[s]['a'][adverse_mark(s,key)][i]*float(instruments[s].contract_size))
        if any(dec(p['count']) for p in state['positions'].values()) and equity(state)<=margin_required(state,initial=False):
            for s in symbols:
                q=dec(state['positions'][s]['count'])
                if q:execute(s,-q,data[s]['a'][adverse_mark(s,key)][i],int(ms[i])+6001,'liquidation',True);liquidations+=1
            if first_halt_cause is None:first_halt_cause='liquidation'
            halted=True
    def check_guard(when):
        nonlocal halted,guard_triggered,guard_ms,guard_equity,guard_positions,first_halt_cause
        if guard_threshold is not None and not guard_triggered and equity(state)<=guard_threshold:
            guard_triggered=True;guard_ms=when;guard_equity=float(equity(state))
            guard_positions={s:p.copy() for s,p in state['positions'].items()}
            if first_halt_cause is None:first_halt_cause='loss_guard'
            halted=True
    reason_map={2:'partial_tp',3:'trailing_exit',4:'channel_exit',5:'time_exit',6:'baseline_exit',7:'take_profit'}
    for i,end in zip(bounds[:-1],bounds[1:],strict=True):
        # Research data-quality blackout must close before unavailable marks, even after a trading halt.
        for s in symbols:
            if data[s]['blackout'][i] and not data[s]['unavailable'][i] and dec(state['positions'][s]['count']):
                q=dec(state['positions'][s]['count']);execute(s,-q,data[s]['a']['open'][i],int(ms[i]),'blackout')
                if guard_triggered:blackout_closes_after_guard+=1
        liquidate_if_needed(i,'mark_open')
        for s in symbols:
            event=data[s]['funding'].get(i)
            if event:
                when,rate=event;q=dec(state['positions'][s]['count']);payment=-q*dec(state['positions'][s]['mark'])*dec(rate)
                state['cash']=str(dec(state['cash'])+payment);state['funding']=str(dec(state['funding'])+payment);pool[s]+=payment
                if guard_triggered:funding_after_guard+=payment
        liquidate_if_needed(i,'mark_open')
        check_guard(int(ms[i])+5000)
        levels={s:float(weights[s][i]) if np.isfinite(weights[s][i]) else prior[s] for s in symbols}
        active={s:(.25 if s.startswith('KXDOGE') else .75) for s in symbols if abs(levels[s])>0}
        active_changed=any(np.sign(levels[s])!=np.sign(prior[s]) for s in symbols)
        total=sum(active.values());value=max(dec(0),equity(state));targets={};whys={}
        for s in symbols:
            level=levels[s];old=dec(state['positions'][s]['count']);changed=level!=prior[s];daily=ms[i]%86400000==0
            if not (changed or daily or active_changed) or not data[s]['a']['tradable'][i] or halted:continue
            if prior[s]!=0 and (level==0 or np.sign(level)==np.sign(prior[s])) and abs(level)<abs(prior[s]):
                qty=(old*dec(level)/dec(prior[s])).to_integral_value(rounding=ROUND_DOWN)
                why='blackout' if data[s]['blackout'][i] else reason_map.get(int(reasons[s][i]),'baseline_exit')
            elif level==0:qty=dec(0);why='rebalance'
            else:
                budget=value*dec('.98')*dec(active[s]/total)
                notional=maximum_notional(metadata[s],1 if level>0 else -1,budget) if leverage=='max' else budget*dec(leverage)
                notional*=dec(abs(level)/.98)
                op=dec(data[s]['a']['open'][i])*instruments[s].contract_size
                qty=dec(1 if level>0 else -1)*(notional/op).to_integral_value(rounding=ROUND_DOWN)
                if abs(level)<.98 and prior[s]!=0 and old*qty>0:qty=dec(1 if qty>0 else -1)*min(abs(qty),abs(old))
                why='entry' if prior[s]==0 else 'rebalance'
            targets[s]=qty;whys[s]=why
        for reducing in (True,False):
            for s,qty in targets.items():
                old=dec(state['positions'][s]['count']);delta=qty-old
                is_reduction=old!=0 and old*delta<0
                if reducing:
                    if not is_reduction:continue
                    delta=dec(-1 if old>0 else 1)*min(abs(delta),abs(old))
                elif is_reduction:continue
                execute(s,delta,data[s]['a']['open'][i],int(ms[i])+6000,whys[s])
        for s in symbols:
            if data[s]['a']['tradable'][i]:prior[s]=levels[s]
        liquidate_if_needed(i,'mark_low');mark(i,'mark_close');check_guard(int(ms[i])+59999);paths[i-first]=float(equity(state))
        if end>i+1:
            eq=np.full(end-i-1,float(dec(state['cash'])));loweq=eq.copy();maintenance=np.zeros_like(eq);notional=np.zeros_like(eq);open_eq=eq.copy()
            for s in symbols:
                p=state['positions'][s];q=float(dec(p['count']));mult=float(instruments[s].contract_size)
                if not q:continue
                cl=data[s]['a']['mark_close'][i+1:end]*mult;lo=data[s]['a']['mark_low' if q>0 else 'mark_high'][i+1:end]*mult
                eq+=q*(cl-float(p['entry']));loweq+=q*(lo-float(p['entry']))
                open_eq+=q*(data[s]['a']['mark_open'][i+1:end]*mult-float(p['entry']))
                n=abs(q)*lo;notional+=abs(q)*cl
                points=sorted((float(k),float(v)) for k,v in metadata[s]['long' if q>0 else 'short'].items());sizes=np.array([p[0] for p in points]);lev=np.array([p[1] for p in points]);ids=np.searchsorted(sizes,n)
                if np.any(ids>=len(lev)):raise ValueError('maintenance exceeds published curve')
                maintenance+=n/lev[ids]
            paths[i+1-first:end-first]=eq
            breaches=np.flatnonzero(loweq<=maintenance)
            if guard_threshold is not None and not guard_triggered:
                candidates=np.flatnonzero((open_eq<=float(guard_threshold))|(eq<=float(guard_threshold)))
                if len(candidates):
                    g=int(candidates[0]);at_open=open_eq[g]<=float(guard_threshold)
                    # Low-based maintenance liquidation precedes a same-minute close observation.
                    if not len(breaches) or g<int(breaches[0]) or (g==int(breaches[0]) and at_open):
                        k=i+1+g;mark(k,'mark_open' if at_open else 'mark_close')
                        check_guard(int(ms[k])+(0 if at_open else 59999))
            # A flat wallet does not liquidate even if its equity is zero/negative.
            if len(breaches) and any(dec(p['count']) for p in state['positions'].values()):
                k=i+1+int(breaches[0]);liquidate_if_needed(k,'mark_low');check_guard(int(ms[k])+6001);paths[k-first:end-first]=float(equity(state))
            good=eq>0
            if len(breaches) and halted:good[int(breaches[0]):]=False
            if good.any():maxlev=max(maxlev,float(np.max(notional[good]/eq[good])))
        v=float(equity(state));n=sum(float(abs(dec(p['count']))*dec(p['mark'])) for p in state['positions'].values())
        if v>0:maxlev=max(maxlev,n/v)
    last=stop-1;mark(last,'mark_close')
    positions_before_terminal={s:p.copy() for s,p in state['positions'].items()}
    for s in symbols:
        q=dec(state['positions'][s]['count'])
        if q:execute(s,-q,data[s]['a']['close'][last],int(ms[last])+59999,'terminal')
    paths[-1]=float(equity(state));pnl=paths[-1]-capital
    assert np.isclose(pnl,float(dec(state['realized_pnl'])+dec(state['funding'])-dec(state['fees'])),atol=1e-6)
    assert np.isclose(pnl,sum(r['net_pnl'] for r in reductions),atol=1e-6)
    assert all(abs(v)<dec('1e-15') for v in pool.values())
    months=(stop-first)/1440/30.4375;positive=[r for r in reductions if r['natural'] and r['net_pnl']>0];tp=[r for r in reductions if r['reason']=='partial_tp' and r['net_pnl']>0];all_tp=[r for r in reductions if r['reason'] in ('partial_tp','take_profit')]
    eq=np.r_[capital,paths];dd=float((eq/np.maximum.accumulate(eq)-1).min())
    days={};natural_days=set()
    for r in reductions:
        if r['reason'] in ('terminal','blackout','liquidation'):continue
        day=r['timestamp_ms']//86400000;days[day]=days.get(day,0)+r['net_pnl']
        if r['natural']:natural_days.add(day)
    result=dict(guard_enabled=guard_fraction is not None,guard_threshold=float(guard_threshold) if guard_threshold is not None else None,guard_triggered=guard_triggered,guard_timestamp_ms=guard_ms,guard_equity=guard_equity,guard_positions=guard_positions,first_halt_cause=first_halt_cause,funding_after_guard=float(funding_after_guard),blackout_closes_after_guard=blackout_closes_after_guard,positions_before_terminal=positions_before_terminal,pnl=pnl,ending_equity=paths[-1],max_drawdown=dd,fees=float(dec(state['fees'])),funding_pnl=float(dec(state['funding'])),liquidations=liquidations,halted=halted,margin_rejections=rejections,max_effective_leverage=maxlev,profitable_realizations=len(positive),profitable_realizations_per_month=len(positive)/months,take_profit_events=len(all_tp),take_profit_events_per_month=len(all_tp)/months,profitable_take_profit_events=sum(r['net_pnl']>0 for r in all_tp),profitable_partial_take_profits=len(tp),profitable_partial_take_profits_per_month=len(tp)/months,profitable_sale_days_per_month=sum(days[d]>0 for d in natural_days)/months)
    return result,pd.DataFrame(dict(equity=paths),index=index[first:stop]),ledger,reductions,closed
