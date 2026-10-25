"""Rebuild version2 diagnostics from immutable quote/funding observations."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from observe_walkforward_market import ROOT, PERPS, quote_metrics
from kalshi_perps.domain import registry


def main():
    out=ROOT/'outputs/crypto-doge-sol-walkforward-2026-10-19'
    source=out/'forward-observations.jsonl'
    original=source.read_bytes()
    rows=[json.loads(line) for line in original.splitlines()]
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=out/'quote-repair-backups'/stamp;backup.mkdir(parents=True)
    for name in ('forward-observations.jsonl','forward-observation-state.json','forward-observation-error.json','FINDINGS.md','FORWARD_STATUS.md'):
        if (out/name).exists():shutil.copy2(out/name,backup/name)
    instruments=registry(json.loads((PERPS/'configs/instruments.json').read_text()))
    changed=0;negative_before=0;negative_after=0
    samples={}
    for row in rows:
        old=row['metrics'];new=quote_metrics(row['quotes'],instruments)
        changed+=old!=new
        for metrics,direction in ((old,'before'),(new,'after')):
            for m in metrics.values():
                for fill in m['hypothetical_1000usd_book_walk'].values():
                    value=fill['one_way_cost_bps_including_4bps_fee']
                    if value is not None and value<0:
                        if direction=='before':negative_before+=1
                        else:negative_after+=1
        row['metrics']=new;row['metrics_version']=2
        for ticker,m in new.items():
            target=samples.setdefault(ticker,dict(spread_bps=[],bid_cost_bps=[],ask_cost_bps=[],bid_fill_fraction=[],ask_fill_fraction=[]))
            target['spread_bps'].append(m['spread_bps'])
            for side,label in [('bids','bid'),('asks','ask')]:
                f=m['hypothetical_1000usd_book_walk'][side];cost=f['one_way_cost_bps_including_4bps_fee']
                if cost is not None:target[label+'_cost_bps'].append(cost)
                if f['requested_contracts']:target[label+'_fill_fraction'].append(float(f['filled_contracts'])/f['requested_contracts'])
    # Recalculation changes only derived metrics and an explicit version field.
    for raw,repaired in zip(original.splitlines(),rows,strict=True):
        old=json.loads(raw)
        for key,value in old.items():
            if key not in ('metrics','metrics_version'):assert repaired[key]==value,key
    assert negative_after==0
    temp=source.with_suffix('.tmp');temp.write_text(''.join(json.dumps(row)+'\n' for row in rows));temp.replace(source)
    statefile=out/'forward-observation-state.json';state=json.loads(statefile.read_text())
    assert state['observations']==len(rows)
    state['last']=rows[-1];state['metrics_version']=2
    temp=statefile.with_suffix('.tmp');temp.write_text(json.dumps(state,indent=2));temp.replace(statefile)
    stats={}
    for ticker,fields in samples.items():
        stats[ticker]={name:dict(count=len(values),median=float(np.median(values)),p95=float(np.quantile(values,.95)),minimum=float(min(values)),maximum=float(max(values))) for name,values in fields.items()}
    manifest=dict(repaired_at_utc=stamp,rows=len(rows),changed_rows=changed,negative_costs_before=negative_before,negative_costs_after=negative_after,raw_quotes_funding_and_timestamps_unchanged=True,original_sha256=hashlib.sha256(original).hexdigest(),repaired_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),backup=str(backup),metrics_version=2,statistics=stats,first_metrics=rows[0]['metrics'],last_metrics=rows[-1]['metrics'],statistic_scope='All stored observations through repair; hypothetical integer-contract1000USDT book walks, modeled4bps fee. Not actual fills or strategy P&L.')
    (out/'quote-repair.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
