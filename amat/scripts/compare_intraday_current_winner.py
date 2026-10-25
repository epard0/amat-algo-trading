"""Replay the frozen symmetric 4h winner on identical minute execution dates."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from autoresearch.crypto_intraday_study import load_minute_inputs
from autoresearch.crypto_intraday import run_intraday
from autoresearch.crypto_perpetual_study import strategy_weights


def main():
    out=Path('outputs/crypto-directional-2026-10-19/intraday-current-winner');out.mkdir(parents=True,exist_ok=False)
    cfg=dict(timeframe=4,lookback=20,exit_ratio=2,sizing='vol20',leverage=2)
    (out/'protocol.json').write_text(json.dumps(dict(config=cfg,selection='frozen original 2020-2021 training winner; no new selection',
        periods={'validation':['2026-04-01','2026-06-30'],'test':['2026-07-01','2026-09-30'],'post_training':['2026-04-01','2026-09-30']},
        execution='same canonical minute trade/mark inputs as intraday alternatives',cost_bps=8,fee_bps=4,
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2))
    frames,_=load_minute_inputs(Path('data/research/crypto-minute-2026-q1-q3'))
    hourly={}
    for s,f in frames.items():
        fields={c:('first' if c.endswith('open') else 'last' if c.endswith('close') else 'max' if c.endswith('high') else 'min') for c in f.columns}
        hourly[s]=f.resample('1h').agg(fields)
    w=strategy_weights(hourly,cfg)
    weights=pd.DataFrame(w,index=next(iter(hourly.values())).index).reindex(next(iter(frames.values())).index,method='ffill').to_numpy()
    funding={}
    for s in frames:
        f=pd.read_csv(Path('data/research/crypto-perpetual-2020-2026-09')/f'{s}-fundingRate.csv')
        funding[s]={int(t//3600000):(int(t),str(r)) for t,r in zip(f.calc_time,f.last_funding_rate,strict=True)}
    results=[]
    for period,dates in {'validation':('2026-04-01','2026-06-30'),'test':('2026-07-01','2026-09-30'),'post_training':('2026-04-01','2026-09-30')}.items():
        stats,path,_,_=run_intraday(frames,funding,weights,dates)
        stats.pop('closed_episodes');results.append(dict(period=period,**stats))
        path.resample('1h').last().to_csv(out/f'{period}.csv',index_label='timestamp')
    (out/'summary.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))

if __name__=='__main__':main()
