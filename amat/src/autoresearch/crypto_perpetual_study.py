"""Cash-and-position historical long/short perpetual replay through the paper wrapper."""
from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, ROUND_DOWN
import hashlib
import itertools
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from autoresearch.crypto_spot_study import aggregate, replay as spot_replay, targets as spot_targets, SYMBOLS
from autoresearch.crypto_risk_matched import paired_intervals, volatility
from autoresearch.genetic.data import CandleDataset, load_crypto
from autoresearch.perpetual_sim import ContractSpec, PerpOrder, PerpetualSimulator, decimal

HOUR = 3600000
TRAIN = ('2020-01-01', '2021-12-31')
PERIODS = {'validation': ('2022-01-01','2022-12-31'), 'test': ('2023-01-01','2023-12-31'),
           'external': ('2024-01-01','2026-09-30'), 'post_training': ('2022-01-01','2026-09-30')}


def candidate_grid():
    return [dict(timeframe=h, lookback=d, exit_ratio=e, sizing=s, leverage=l)
            for h,d,e,s,l in itertools.product((1,4),(5,10,20),(2,4),('full','vol20'),(1,2))]


def load_inputs(root):
    manifest=json.loads((root/'manifest.json').read_text())
    index=pd.date_range('2020-01-01','2026-10-01',freq='1h',inclusive='left',tz='UTC')
    frames={}; funding={}; quality={}
    for symbol in SYMBOLS:
        pieces={}
        for kind in ('klines','markPriceKlines','fundingRate'):
            record=next(r for r in manifest['datasets'] if r['symbol']==symbol and r['kind']==kind)
            path=root/record['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:
                raise ValueError('input checksum mismatch')
            frame=pd.read_csv(path)
            if len(frame)!=record['rows']: raise ValueError('input row count mismatch')
            if kind=='fundingRate':
                times=frame.calc_time.to_numpy(dtype='int64')
                hours=times//HOUR
                offsets=times%HOUR
                if (offsets>60000).any(): raise ValueError('funding time not within first minute of hour')
                if len(set(hours))!=len(hours): raise ValueError('multiple funding records per hour')
                intervals=frame.funding_interval_hours.to_numpy(dtype='float64')
                if not np.isfinite(intervals).all() or (intervals<=0).any(): raise ValueError('invalid funding intervals')
                gaps=np.diff(times)/HOUR
                if (gaps>np.maximum(intervals[:-1],intervals[1:])+.017).any():
                    raise ValueError('funding gap exceeds reported interval; cannot assume zero funding')
                rates=frame.last_funding_rate.to_numpy(dtype='float64')
                if not np.isfinite(rates).all() or (abs(rates)>=1).any(): raise ValueError('invalid funding rate')
                first=int(index[0].timestamp()*1000)//HOUR; stop=int((index[-1]+pd.Timedelta(hours=1)).timestamp()*1000)//HOUR
                if hours[0]!=first or stop-hours[-1]>intervals[-1]: raise ValueError('funding boundary incomplete')
                funding[symbol]={int(t//HOUR): (int(t),str(r)) for t,r in zip(times,frame.last_funding_rate,strict=True)}
                pieces['funding_quality']=dict(events=len(frame),max_timestamp_offset_ms=int(offsets.max()),
                    max_interval_hours=float(intervals.max()),min_rate=float(rates.min()),max_rate=float(rates.max()),
                    total_rate_sum=float(rates.sum()),missing_assumed_zero=False)
                continue
            times=frame.open_time_ms.to_numpy(dtype='int64')
            if len(set(times))!=len(times): raise ValueError('duplicate candle')
            valid=(times%HOUR==0)&(frame.close_time_ms.to_numpy()==times+HOUR-1)
            o,hi,lo,c=[frame[k].to_numpy(dtype='float64') for k in ('open','high','low','close')]
            valid &= np.isfinite(np.column_stack([o,hi,lo,c])).all(axis=1)&(lo>0)&(lo<=np.minimum(o,c))&(hi>=np.maximum(o,c))
            f=frame.loc[valid,['open','high','low','close']].copy()
            f.index=pd.to_datetime(times[valid],unit='ms',utc=True); f=f.reindex(index)
            if kind=='klines':
                tradable=pd.Series(frame.loc[valid,'volume'].to_numpy()>0,index=pd.to_datetime(times[valid],unit='ms',utc=True))
                pieces['tradable']=tradable.reindex(index).fillna(False).astype(float)
            pieces[kind]=f
        combined=pieces['klines'].copy()
        for name in ('open','high','low','close'): combined['mark_'+name]=pieces['markPriceKlines'][name]
        missing=combined.isna().any(axis=1)
        # No missing trade/mark hours allowed in an authoritative funding replay.
        if missing.any(): raise ValueError(f'{symbol}: {int(missing.sum())} unavailable trade/mark hours')
        combined['tradable']=pieces['tradable']
        frames[symbol]=combined
        quality[symbol]=dict(hours=len(combined),missing_hours=int(missing.sum()),funding=pieces['funding_quality'],zero_volume_untradable_hours=int((combined.tradable==0).sum()))
    return frames,funding,quality


def strategy_weights(frames,cfg):
    """Symmetric high/low breakouts; exits can go flat or reverse on opposite entry."""
    h=cfg['timeframe']; n=cfg['lookback']*24//h; exit_n=n//cfg['exit_ratio']
    weights=[]
    for frame in frames.values():
        valid_prices=frame[['open','high','low','close']].where(frame.tradable>0,axis=0)
        f=valid_prices.resample(f'{h}h').agg({'open':'first','high':'max','low':'min','close':'last'})
        count=valid_prices.close.resample(f'{h}h').count(); f[count!=h]=np.nan
        high_entry=f.high.rolling(n).max().shift(1); low_entry=f.low.rolling(n).min().shift(1)
        high_exit=f.high.rolling(exit_n).max().shift(1); low_exit=f.low.rolling(exit_n).min().shift(1)
        c=f.close.to_numpy(); he=high_entry.to_numpy(); le=low_entry.to_numpy()
        hx=high_exit.to_numpy(); lx=low_exit.to_numpy(); state=0; position=np.zeros(len(c))
        for i in range(len(c)):
            if state==1 and c[i]<lx[i]: state=0
            elif state==-1 and c[i]>hx[i]: state=0
            if c[i]>he[i]: state=1
            elif c[i]<le[i]: state=-1
            position[i]=state
        if cfg['sizing']=='vol20':
            vol=f.close.pct_change(fill_method=None).rolling(30*24//h).std()*np.sqrt(365*24/h)
            size=(.20/vol).clip(0,1).to_numpy(copy=True)
        else: size=np.ones(len(f))
        size[np.isnan(he)]=np.nan
        # 2% execution/collateral buffer; leverage multiplies actual desired exposure.
        series=pd.Series(position*size*.98*cfg['leverage'],index=f.index+pd.Timedelta(hours=h))
        weights.append(series.reindex(frame.index,method='ffill').to_numpy())
    return np.column_stack(weights)


def run_perpetual(frames,funding,weights,dates,cost=8,scale=1.,capture=False,leverage_limit=2):
    """Hourly chronological events, discrete funding and worst mark before close.

    Two independent collateral sleeves, 5000 USDT each. Account maximum leverage
    is supplied per policy; target exposure is 0.98x/1.96x before scaling.
    """
    idx=next(iter(frames.values())).index
    mask=(idx>=pd.Timestamp(dates[0],tz='UTC'))&(idx<pd.Timestamp(dates[1],tz='UTC')+pd.Timedelta(days=1))
    rows=np.flatnonzero(mask)
    if not len(rows): raise ValueError('empty replay interval')
    arrays=[f.to_numpy() for f in frames.values()]
    names=list(next(iter(frames.values())).columns)
    col={n:names.index(n) for n in names}
    sims=[PerpetualSimulator(ContractSpec(s,multiplier='.001',quantity_step=1,price_tick='.01',
        max_leverage=leverage_limit,maintenance_rate='.005',fee_rate='.0005',slippage_rate=str((cost-5)/10000),
        liquidation_fee_rate='.005'),5000) for s in SYMBOLS]
    previous=np.zeros(2); values=[]; marks=[]; signs=[]; exposures=[]; fill_errors=0
    for i in rows:
        ms=int(idx[i].timestamp()*1000); portfolio=0.; gross=0.
        for j,(symbol,sim,a) in enumerate(zip(SYMBOLS,sims,arrays,strict=True)):
            row=a[i]; op=decimal(row[col['open']]); mo=decimal(row[col['mark_open']])
            sim.mark_to_market(mo,ms,snapshot=False)
            event=funding[symbol].get(ms//HOUR)
            if event:
                when,rate=event
                sim.apply_funding(event_id=str(when),rate=rate,mark=mo,timestamp_ms=when)
            desired=weights[i,j]*scale
            sign=np.sign(desired) if np.isfinite(desired) else np.nan
            rebalance=np.isfinite(sign) and (sign!=previous[j] or idx[i].hour==0)
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
                        sim.submit(order,bid=op,ask=op,mark=mo,timestamp_ms=ms+60001)
                    except ValueError as exc:
                        # Margin rejections are observable inactivity, never silently resized.
                        if 'initial margin' not in str(exc): raise
                        fill_errors+=1
                        sim.ledger.append(dict(event='margin_rejection',timestamp_ms=ms+60001,message=str(exc)))
            if np.isfinite(sign) and row[col['tradable']]>0: previous[j]=sign
            adverse=row[col['mark_low']] if sim.contracts>0 else row[col['mark_high']]
            sim.mark_to_market(adverse,ms+60002,snapshot=False)
            sim.mark_to_market(row[col['mark_close']],ms+HOUR-1,snapshot=False)
            portfolio+=float(sim.equity); gross+=float(sim.notional)
        values.append(portfolio); exposures.append(gross/max(portfolio,1e-12))
    # Terminal close executes at last trade close; funding has already been applied.
    for j,(sim,a) in enumerate(zip(sims,arrays,strict=True)):
        if sim.contracts:
            price=decimal(a[rows[-1],col['close']]); delta=-sim.contracts; direction=1 if delta>0 else -1
            limit=sim._execution_price(price,direction)
            sim.submit(PerpOrder(f'final-{j}',sim.spec.ticker,'bid' if delta>0 else 'ask',abs(delta),limit,reduce_only=True),
                       bid=price,ask=price,mark=sim.mark,timestamp_ms=int(idx[rows[-1]].timestamp()*1000)+HOUR-1)
    values[-1]=sum(float(s.equity) for s in sims)
    path=pd.DataFrame({'equity':values,'gross_exposure':exposures},index=idx[rows])
    daily=path.equity.resample('1D').last(); ret=daily.pct_change(); ret.iloc[0]=daily.iloc[0]/10000-1
    eq=np.r_[10000,daily.to_numpy()]; dd=eq/np.maximum.accumulate(eq)-1
    trades=sum(sum(e['event']=='fill' for e in s.ledger) for s in sims)
    liquidation=sum(sum(e['event']=='liquidation' for e in s.ledger) for s in sims)
    final=float(eq[-1]); years=len(daily)/365
    stats=dict(pnl=final-10000,total_return=final/10000-1,cagr=(final/10000)**(1/years)-1 if final>0 else None,
        annualized_volatility=volatility(ret),sharpe=float(ret.mean()/ret.std()*np.sqrt(365)) if ret.std()>0 else 0.,
        max_drawdown=float(dd.min()),fees=sum(float(s.fees) for s in sims),
        funding_pnl=sum(float(s.funding_pnl) for s in sims),realized_price_pnl=sum(float(s.realized_pnl) for s in sims),
        orders=trades,liquidations=liquidation,margin_rejections=fill_errors,
        mean_gross_exposure=float(np.mean(exposures)),max_gross_exposure=float(np.max(exposures)),
        per_asset={s.spec.ticker:dict(pnl=float(s.equity)-5000,funding_pnl=float(s.funding_pnl),fees=float(s.fees),halted=s.halted) for s in sims})
    if not np.isclose(stats['pnl'],stats['realized_price_pnl']+stats['funding_pnl']-stats['fees'],atol=1e-7):
        raise AssertionError('closed-wallet accounting identity failed')
    return stats,path,ret,([dict(symbol=s.spec.ticker,ledger=s.ledger) for s in sims] if capture else None)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); out=args.output; out.mkdir(parents=True,exist_ok=False)
    source=Path('data/research/crypto-perpetual-2020-2026-09')
    protocol=dict(train=TRAIN,periods=PERIODS,candidates=candidate_grid(),selection='maximum training net P&L among >=20 fills and zero training liquidations; one winner per timeframe/leverage; global winner also locked',
        primary_cost_bps=8,stress_costs_bps=[15,30],commission_bps=5,
        slippage='3/10/25bps adverse inclusive spread allowance; quoted trade open proxy; no actual bid/ask',
        leverage='actual target full exposure .98x/1.96x; vol20 sizing scales this by min(1,.20/trailing asset vol); account maximum matches 1x/2x policy; 2% policy buffer',
        funding='archive actual rates and event timestamps; funding charged to pre-order positions using boundary mark-open proxy; no zero-filled missing rates',
        timing='hourly marks and actual funding event first; completed 1h/4h signal executes boundary trade-open proxy at timestamp +60.001 seconds; adverse mark extreme then close',
        margin='independent 5000 USDT sleeves; 0.5% maintenance, 0.5% liquidation fee, adverse hour-mark extreme liquidation; no account reset after liquidation',
        quantity='illustrative linear .001 base units/contract, integer contracts, .01 quote price tick',
        risk_match='fixed 12% annual daily portfolio-vol target; scale each locked perp/spot strategy down using 2020-2021 training only; never leverage spot above1x or any policy beyond native exposure; report realized later risk',
        spot_comparators='original locked hourly/four-hour spot breakout selections, 15bps primary and 30/50bps stress',
        primary='global training P&L winner at matched risk vs corresponding spot timeframe; paired 30day bootstrap,95% interval; additional variants descriptive',
        limitations=['2020-2021 training is short and bull-heavy','later spot markets were inspected previously; temporal holdouts are not pristine unseen evidence',
        'funding rates are historical but funding mark is hourly boundary proxy, no event mark from funding archive',
        'hourly worst mark is conservative liquidation proxy, not venue exact intrabar liquidation','fixed margin assumptions do not reconstruct historical risk tiers',
        'no measured quotes, market impact, insurance fund, ADL or prospective evidence','all balances USDT with zero collateral/cash yield'],
        fee_source='https://www.binance.com/en-NG/support/faq/detail/360033544231',
        input_manifest_sha256=hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(__file__).with_name('perpetual_sim.py'),Path(__file__).with_name('crypto_spot_study.py')]})
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    frames,funding,audit=load_inputs(source); (out/'data-audit.json').write_text(json.dumps(audit,indent=2))
    # Cache causal weights by full recipe. All training rankings precede holdouts.
    training=[]; cache={}
    for k,cfg in enumerate(candidate_grid()):
        weights=strategy_weights(frames,cfg); cache[k]=weights
        stats,_,_,_=run_perpetual(frames,funding,weights,TRAIN,leverage_limit=cfg['leverage'])
        training.append(dict(id=k,config=cfg,**stats))
        print(f'training {k+1}/48 P&L={stats["pnl"]:.2f}',flush=True)
        (out/'training-progress.json').write_text(json.dumps(training,indent=2))
    selected={}
    for h,l in itertools.product((1,4),(1,2)):
        eligible=[r for r in training if r['config']['timeframe']==h and r['config']['leverage']==l and r['orders']>=20 and r['liquidations']==0]
        if not eligible: raise ValueError(f'no eligible candidates {h}h/{l}x')
        selected[f'{h}h-{l}x']=max(eligible,key=lambda r:(r['pnl'],-r['id']))
    winner=max(selected,key=lambda k:selected[k]['pnl'])
    (out/'selection.json').write_text(json.dumps(dict(selected=selected,global_winner=winner),indent=2))
    spot_data=load_crypto(Path('data/research/crypto-spot-2018-2026-09'),SYMBOLS,start=dt.date(2020,1,1),end=dt.date(2026,9,30))
    old_selection=json.loads(Path('outputs/crypto-spot-btc-eth-2026-10-19-v2/selection.json').read_text())
    spot={}; calibration={}
    for h in (1,4):
        fs=aggregate(spot_data,h); target=spot_targets(fs,old_selection['by_timeframe'][str(h)]['config'])
        spot[h]=fs,target
    # Bisection calibration stays in training, never increases above native risk.
    def scale_for(run):
        top=run(1.)[2]; native=volatility(top)
        if native<=.12: return 1.,native,native
        f=lambda x:volatility(run(x)[2])-.12
        scale=float(brentq(f,.001,1.,xtol=.002))
        return scale,native,volatility(run(scale)[2])
    for name,r in selected.items():
        run=lambda scale:run_perpetual(frames,funding,cache[r['id']],TRAIN,scale=scale,leverage_limit=r['config']['leverage'])
        scale,native,achieved=scale_for(run)
        calibration[name]=dict(scale=scale,native_training_vol=native,matched_training_vol=achieved)
    for h,(fs,target) in spot.items():
        run=lambda scale:spot_replay(fs,target*scale,*TRAIN,15)
        scale,native,achieved=scale_for(run)
        calibration[f'spot-{h}h']=dict(scale=scale,native_training_vol=native,matched_training_vol=achieved)
    (out/'calibration.json').write_text(json.dumps(calibration,indent=2))
    results=[]; daily={}
    for name,r in selected.items():
        for period,dates in PERIODS.items():
            for cost in (8,15,30):
                for mode,scale in [('native',1.),('matched',calibration[name]['scale'])]:
                    stats,path,ret,ledger=run_perpetual(frames,funding,cache[r['id']],dates,cost,scale,capture=cost==8,leverage_limit=r['config']['leverage'])
                    label=f'{name}-{period}-{cost}bps-{mode}'
                    path.to_csv(out/f'{label}.csv',index_label='timestamp')
                    if ledger is not None: (out/f'{label}-ledger.json').write_text(json.dumps(ledger))
                    results.append(dict(strategy=name,period=period,cost_bps=cost,mode=mode,**stats))
                    daily[name,period,cost,mode]=ret
            print(f'evaluated {name} {period}',flush=True)
    for h,(fs,target) in spot.items():
        name=f'spot-{h}h'
        for period,dates in PERIODS.items():
            for cost in (15,30,50):
                for mode,scale in [('native',1.),('matched',calibration[name]['scale'])]:
                    stats,path,ret=spot_replay(fs,target*scale,*dates,cost)
                    stats.update(pnl=stats['total_return']*10000,annualized_volatility=volatility(ret))
                    results.append(dict(strategy=name,period=period,cost_bps=cost,mode=mode,**stats))
                    daily[name,period,cost,mode]=ret
                    path.to_csv(out/f'{name}-{period}-{cost}bps-{mode}.csv',index_label='timestamp')
    comparisons=[]
    for name,r in selected.items():
        h=r['config']['timeframe']
        for period in PERIODS:
            a=daily[name,period,8,'matched']; b=daily[f'spot-{h}h',period,15,'matched']
            comparisons.append(dict(strategy=name,period=period,spot=f'spot-{h}h',**paired_intervals(a,b,samples=2000)))
    summary=dict(results=results,comparisons=comparisons,global_training_winner=winner)
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    lines=['# Historical perpetual long/short BTC/ETH study','',f'Global training P&L winner: {winner}. All later-period rankings are descriptive.','',
        '| Strategy | Period | Mode | Cost/side | Net P&L | Return | Vol | DD | Sharpe | Funding P&L | Liquidations |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in results:
        lines.append(f"| {r['strategy']} | {r['period']} | {r['mode']} | {r['cost_bps']}bps | {r['pnl']:.2f} | {r['total_return']:.2%} | {r['annualized_volatility']:.2%} | {r['max_drawdown']:.2%} | {r['sharpe']:.2f} | {r.get('funding_pnl',0):.2f} | {r.get('liquidations',0)} |")
    lines+=['','## Limits','']+protocol['limitations']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(12,8))
    for ax,mode in zip(axes,('native','matched')):
        for name in [*selected,'spot-1h','spot-4h']:
            cost=15 if name.startswith('spot') else 8
            ret=daily[name,'external',cost,mode]
            ax.plot(ret.index,10000*(1+ret).cumprod(),label=name)
        ax.set_title(f'2024–September 2026: {mode} risk, historical funding included for perps')
        ax.set_ylabel('USDT equity'); ax.legend(ncol=3)
    fig.tight_layout(); fig.savefig(out/'equity.png',dpi=150)
    snapshots=out/'source'; snapshots.mkdir()
    for path in [Path(__file__),Path(__file__).with_name('perpetual_sim.py')]: shutil.copy2(path,snapshots/path.name)
    print(json.dumps(dict(winner=winner,selection={k:v['config'] for k,v in selected.items()},calibration=calibration),indent=2))

if __name__=='__main__':main()
