"""Intraday candle-resolution study, canonical one-minute wrapper accounting."""
from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
import numpy as np
import pandas as pd
from autoresearch.perpetual_sim import ContractSpec, PerpOrder, PerpetualSimulator, decimal
from autoresearch.crypto_risk_matched import volatility
from autoresearch.crypto_directional import closed_trade_metrics
HOUR=3600000

def run_intraday(frames,funding,weights,dates,cost=8,scale=1.,capture=False,leverage_limit=2, capital=10000, fee_bps=4, contract_specs=None):
    """Hourly chronological events, discrete funding and worst mark before close.

    Two independent collateral sleeves, 5000 USDT each. Account maximum leverage
    is supplied per policy; target exposure is 0.98x/1.96x before scaling.
    """
    symbols=tuple(frames)
    sleeve_capital=capital/len(symbols)
    idx=next(iter(frames.values())).index
    step_ms=int((idx[1]-idx[0]).total_seconds()*1000)
    execution_offset=min(60001,step_ms//10)
    mask=(idx>=pd.Timestamp(dates[0],tz='UTC'))&(idx<pd.Timestamp(dates[1],tz='UTC')+pd.Timedelta(days=1))
    rows=np.flatnonzero(mask)
    if not len(rows): raise ValueError('empty replay interval')
    arrays=[f.to_numpy() for f in frames.values()]
    names=list(next(iter(frames.values())).columns)
    col={n:names.index(n) for n in names}
    sims=[PerpetualSimulator(ContractSpec(s,multiplier=str((contract_specs or {}).get(s,{}).get('multiplier','.001')),quantity_step=1,price_tick=str((contract_specs or {}).get(s,{}).get('price_tick','.01')),
        max_leverage=leverage_limit,maintenance_rate='.005',fee_rate=str(fee_bps/10000),slippage_rate=str((cost-fee_bps)/10000),
        liquidation_fee_rate='.005'),sleeve_capital) for s in symbols]
    previous=np.zeros(len(symbols)); values=[]; marks=[]; signs=[]; exposures=[]; fill_errors=0
    for i in rows:
        ms=int(idx[i].timestamp()*1000); portfolio=0.; gross=0.
        for j,(symbol,sim,a) in enumerate(zip(symbols,sims,arrays,strict=True)):
            row=a[i]
            desired=weights[i,j]*scale
            if not sim.contracts and (not np.isfinite(desired) or desired==0):
                portfolio+=float(sim.cash)
                if np.isfinite(desired):previous[j]=0
                continue
            op=decimal(row[col['open']]); mo=decimal(row[col['mark_open']])
            sim.mark_to_market(mo,ms,snapshot=False)
            event=funding[symbol].get(ms//HOUR)
            if event and ms <= event[0] < ms+step_ms:
                when,rate=event
                sim.apply_funding(event_id=str(when),rate=rate,mark=mo,timestamp_ms=when)
            desired=weights[i,j]*scale
            sign=np.sign(desired) if np.isfinite(desired) else np.nan
            rebalance=np.isfinite(sign) and (sign!=previous[j] or (idx[i].hour==0 and idx[i].minute==0))
            if rebalance and not sim.halted and row[col['tradable']]>0:
                # Use current marked equity and actual trade-open price for target units.
                qty=decimal(desired)*max(Decimal(0),sim.equity)/(sim.spec.multiplier*op)
                qty=qty.to_integral_value(rounding=ROUND_DOWN)
                delta=qty-sim.contracts
                if delta:
                    direction=1 if delta>0 else -1
                    limit=sim._execution_price(op,direction)
                    order=PerpOrder(f'{ms}-{j}',symbol,'bid' if delta>0 else 'ask',abs(delta),limit)
                    try:
                        sim.submit(order,bid=op,ask=op,mark=mo,timestamp_ms=ms+execution_offset)
                    except ValueError as exc:
                        # Margin rejections are observable inactivity, never silently resized.
                        if 'initial margin' not in str(exc): raise
                        fill_errors+=1
                        sim.ledger.append(dict(event='margin_rejection',timestamp_ms=ms+execution_offset,message=str(exc)))
            if np.isfinite(sign) and row[col['tradable']]>0: previous[j]=sign
            adverse=row[col['mark_low']] if sim.contracts>0 else row[col['mark_high']]
            sim.mark_to_market(adverse,ms+execution_offset+1,snapshot=False)
            sim.mark_to_market(row[col['mark_close']],ms+step_ms-1,snapshot=False)
            portfolio+=float(sim.equity); gross+=float(sim.notional)
        values.append(portfolio); exposures.append(gross/max(portfolio,1e-12))
    # Terminal close executes at last trade close; funding has already been applied.
    for j,(sim,a) in enumerate(zip(sims,arrays,strict=True)):
        if sim.contracts:
            price=decimal(a[rows[-1],col['close']]); delta=-sim.contracts; direction=1 if delta>0 else -1
            limit=sim._execution_price(price,direction)
            sim.submit(PerpOrder(f'final-{j}',sim.spec.ticker,'bid' if delta>0 else 'ask',abs(delta),limit,reduce_only=True),
                       bid=price,ask=price,mark=sim.mark,timestamp_ms=int(idx[rows[-1]].timestamp()*1000)+step_ms-1)
    values[-1]=sum(float(s.equity) for s in sims)
    path=pd.DataFrame({'equity':values,'gross_exposure':exposures},index=idx[rows])
    daily=path.equity.resample('1D').last(); ret=daily.pct_change(); ret.iloc[0]=daily.iloc[0]/capital-1
    eq=np.r_[capital,daily.to_numpy()]; dd=eq/np.maximum.accumulate(eq)-1
    trades=sum(sum(e['event']=='fill' for e in s.ledger) for s in sims)
    liquidation=sum(sum(e['event']=='liquidation' for e in s.ledger) for s in sims)
    final=float(eq[-1]); years=len(daily)/365
    stats=dict(pnl=final-capital,total_return=final/capital-1,cagr=(final/capital)**(1/years)-1 if final>0 else None,
        annualized_volatility=volatility(ret),sharpe=float(ret.mean()/ret.std()*np.sqrt(365)) if ret.std()>0 else 0.,
        max_drawdown=float(dd.min()),fees=sum(float(s.fees) for s in sims),
        funding_pnl=sum(float(s.funding_pnl) for s in sims),realized_price_pnl=sum(float(s.realized_pnl) for s in sims),
        orders=trades,liquidations=liquidation,margin_rejections=fill_errors,
        mean_gross_exposure=float(np.mean(exposures)),max_gross_exposure=float(np.max(exposures)),
        per_asset={s.spec.ticker:dict(pnl=float(s.equity)-sleeve_capital,funding_pnl=float(s.funding_pnl),fees=float(s.fees),halted=s.halted) for s in sims})
    if not np.isclose(stats['pnl'],stats['realized_price_pnl']+stats['funding_pnl']-stats['fees'],atol=1e-7):
        raise AssertionError('closed-wallet accounting identity failed')
    stats.update(closed_trade_metrics(sims,idx[rows]))
    if not np.isclose(stats['episode_net_pnl'],stats['pnl'],atol=1e-6):raise AssertionError('episode P&L does not reconcile')
    weekly=ret.resample('W-SUN').apply(lambda x:float(np.prod(1+x)-1))
    stats['positive_weeks']=int((weekly>0).sum())
    stats['observed_weeks']=len(weekly)
    stats['positive_week_fraction']=float((weekly>0).mean())
    return stats,path,ret,([dict(symbol=s.spec.ticker,ledger=s.ledger) for s in sims] if capture else None)


def minute_weights(frames,cfg,directions):
    """Four-hour 20/10 breakouts; optional ATR profit trigger and 72h time exit.

    A completed 4h close triggers exits; fills occur at the following open.
    ATR and reference close are frozen when an entry signal is first observed.
    """
    outputs=[];minutes=cfg['minutes'];n=20*1440//minutes;exit_n=10*1440//minutes
    for symbol,frame in frames.items():
        f=frame[['open','high','low','close']].where(frame.tradable>0,axis=0)
        grouped=f.resample(f'{minutes}min');prices=grouped.agg({'open':'first','high':'max','low':'min','close':'last'})
        prices[grouped.close.count()!=minutes]=np.nan
        c=prices.close;hi=prices.high;lo=prices.low
        entry_hi=hi.rolling(n).max().shift(1).to_numpy();entry_lo=lo.rolling(n).min().shift(1).to_numpy()
        exit_hi=hi.rolling(exit_n).max().shift(1).to_numpy();exit_lo=lo.rolling(exit_n).min().shift(1).to_numpy()
        tr=pd.concat([hi-lo,(hi-c.shift()).abs(),(lo-c.shift()).abs()],axis=1).max(axis=1)
        atr=tr.rolling(14).mean().to_numpy();close=c.to_numpy()
        vol=c.pct_change(fill_method=None).rolling(30*1440//minutes).std()*np.sqrt(365*1440/minutes)
        sizing=cfg['sizing']
        if sizing=='full':size=np.ones(len(c))
        else:size=(float(sizing.removeprefix('vol'))/100/vol).clip(0,1).to_numpy(copy=True)
        state=0;ref=0.;entry_atr=0.;entered=0;out=np.zeros(len(c));direction=directions[symbol]
        for i,price in enumerate(close):
            valid=np.isfinite(price) and np.isfinite(entry_hi[i]) and np.isfinite(size[i])
            if not valid:out[i]=np.nan;continue
            exited=False
            if state:
                channel=(price<exit_lo[i]) if state>0 else (price>exit_hi[i])
                tp_multiple=cfg.get('tp_atr')
                tp=tp_multiple is not None and state*(price-ref)>=tp_multiple*entry_atr
                timed=tp_multiple is not None and i-entered>=72*60//minutes
                if channel or tp or timed:state=0;exited=True
            if not state and not exited:
                trigger=(price>entry_hi[i]) if direction>0 else (price<entry_lo[i])
                if trigger:state=direction;ref=price;entry_atr=atr[i];entered=i
            out[i]=state*size[i]*.98*cfg['leverage']
        series=pd.Series(out,index=prices.index+pd.Timedelta(minutes=minutes))
        outputs.append(series.reindex(frame.index,method='ffill').to_numpy())
    return np.column_stack(outputs)


def closed_trade_metrics(sims,index):
    """Net completed episodes, including all rebalance P&L, fees and funding."""
    episodes=[]
    for sim in sims:
        q=Decimal(0); pnl=Decimal(0); entry=None
        for e in sim.ledger:
            if e['event']=='funding':
                if q:pnl+=decimal(e['payment'])
                continue
            if e['event'] not in ('fill','liquidation'):continue
            delta=decimal(e['contracts']);fee=decimal(e['fee']);realized=decimal(e['realized_pnl'])
            if q==0 or q*delta>0:
                if q==0:entry=e['timestamp_ms'];pnl=Decimal(0)
                pnl-=fee
            else:
                closed=min(abs(q),abs(delta));closing_fee=fee*closed/abs(delta)
                pnl+=realized-closing_fee
                if abs(delta)>=abs(q):
                    episodes.append(dict(symbol=sim.spec.ticker,entry_ms=entry,exit_ms=e['timestamp_ms'],net_pnl=float(pnl),
                        holding_hours=(e['timestamp_ms']-entry)/HOUR))
                    pnl=-(fee-closing_fee)
                    entry=e['timestamp_ms'] if abs(delta)>abs(q) else None
            q+=delta
        if q:raise AssertionError('terminal position must be flat for episode metrics')
    days=max(1,(index[-1]-index[0]).total_seconds()/86400+(index[1]-index[0]).total_seconds()/86400)
    profitable=sorted(e['exit_ms'] for e in episodes if e['net_pnl']>0)
    gaps=np.diff(profitable)/HOUR/24 if len(profitable)>1 else []
    return dict(round_trips=len(episodes),profitable_closes=len(profitable),
        profitable_closes_per_month=len(profitable)/(days/30.4375),
        round_trips_per_month=len(episodes)/(days/30.4375),
        median_holding_hours=float(np.median([e['holding_hours'] for e in episodes])) if episodes else None,
        median_days_between_profit_closes=float(np.median(gaps)) if len(gaps) else None,
        max_days_between_profit_closes=float(np.max(gaps)) if len(gaps) else None,
        episode_net_pnl=float(sum(e['net_pnl'] for e in episodes)),closed_episodes=episodes)
