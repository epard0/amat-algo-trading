"""Evaluate saved training winners on additional history without retuning."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import numpy as np
import pandas as pd
from autoresearch.crypto_spot_study import SYMBOLS,aggregate,targets,replay,bootstrap
from autoresearch.genetic.data import load_crypto


def main():
    parent=Path('outputs/crypto-spot-btc-eth-2026-10-19-v2')
    out=parent/'external-2024-2026'; out.mkdir(exist_ok=False)
    source=Path('data/research/crypto-spot-2018-2026-09')
    selection=json.loads((parent/'selection.json').read_text())
    protocol=dict(period=['2024-01-01','2026-09-30'],selection_sha256=hashlib.sha256((parent/'selection.json').read_bytes()).hexdigest(),
        manifest_sha256=hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),costs=[15,30,50],matched_control='always-long with identical 30-day volatility sizing, per-asset 20% target and daily rebalance',
        selection='unchanged original training winners; no additional search',
        gate='positive at all costs, primary Sharpe>=0.5 and drawdown>=-25%; bootstrap lower excess CI>0',
        annual='separate calendar slices reset to cash; 2026 is nine months',
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    data=load_crypto(source,SYMBOLS,start=dt.date(2018,1,1),end=dt.date(2026,9,30))
    (out/'data-audit.json').write_text(json.dumps(data.manifest,indent=2))
    original=load_crypto(Path('data/research/genetic-patterns-2026-09-27'),SYMBOLS,start=dt.date(2018,1,1),end=dt.date(2023,12,31))
    results=[]; cis={}; matched_cis={}; paths={}; gates={}; parity={}
    for key,chosen in selection['by_timeframe'].items():
        h=int(key); fs=aggregate(data,h); target=targets(fs,chosen['config']); daily={}
        old_target=targets(aggregate(original,h),chosen['config'])
        np.testing.assert_allclose(target[:len(old_target)],old_target,rtol=1e-10,atol=1e-12,equal_nan=True)
        parity[key]='original target prefix matches extended dataset to 1e-10 relative tolerance'
        vol_control=np.column_stack([(0.20/(f.close.pct_change(fill_method=None).rolling(30*24//h).std()*np.sqrt(365*24/h))).clip(0,1).shift(1).to_numpy() for f in fs.values()])
        for name,start,end in [('external','2024-01-01','2026-09-30'),('2024','2024-01-01','2024-12-31'),('2025','2025-01-01','2025-12-31'),('2026YTD','2026-01-01','2026-09-30')]:
            for cost in ((15,30,50) if name=='external' else (15,)):
                for policy,bh in [('selected',False),('buyhold',True),('volatility-matched-long',False)]:
                    stats,path,ret=replay(fs,vol_control if policy=='volatility-matched-long' else target,start,end,cost,buyhold=bh)
                    results.append(dict(timeframe=h,period=name,cost_bps=cost,policy=policy,**stats))
                    path.to_csv(out/f'{h}h-{name}-{cost}bps-{policy}.csv',index_label='timestamp')
                    if name=='external' and cost==15:
                        daily[policy]=ret; paths[h,policy]=ret
        cis[key]=bootstrap(daily['selected'],daily['buyhold'])
        matched_cis[key]=bootstrap(daily['selected'],daily['volatility-matched-long'])
        relevant=[r for r in results if r['timeframe']==h and r['period']=='external' and r['policy']=='selected']
        base=next(r for r in relevant if r['cost_bps']==15)
        gates[key]=all(r['total_return']>0 for r in relevant) and base['sharpe']>=.5 and base['max_drawdown']>=-.25 and cis[key][0]>0
    summary=dict(results=results,bootstrap_excess_return_95ci=cis,gates=gates,prefix_parity=parity,volatility_matched_excess_95ci=matched_cis)
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    lines=['# Locked external BTC/ETH spot evaluation','', 'January 2024–September 2026; original 2018–2021 training selections unchanged.','',
        '| Bars | Period | Cost/side | Policy | Net return | Max DD | Sharpe | Orders |','|---|---|---:|---|---:|---:|---:|---:|']
    for r in results:
        lines.append(f"| {r['timeframe']}h | {r['period']} | {r['cost_bps']} bps | {r['policy']} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['sharpe']:.2f} | {r['orders']} |")
    lines+=['',f'Paired 30-day-block bootstrap excess-return 95% intervals: `{cis}`',f'Volatility-matched excess-return 95% intervals: `{matched_cis}`',f'Gates: `{gates}`','',
        'Annual rows reset to cash and therefore do not compound to the continuous external path. 2026 includes January–September only.',
        'Spot USDT return proxy; spread/slippage assumptions are not measured fills. Original protocol limitations continue to apply.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(11,7))
    for ax,h in zip(axes,(1,4)):
        for policy in ('selected','buyhold','volatility-matched-long'):
            ret=paths[h,policy]; ax.plot(ret.index,10000*(1+ret).cumprod(),label=policy)
        ax.set_title(f'{h}h: locked external test, 15bps per side'); ax.legend(); ax.set_ylabel('USDT equity')
    fig.tight_layout(); fig.savefig(out/'equity.png',dpi=150)
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
