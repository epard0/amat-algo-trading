"""Attribute saved perpetual ledger cash flows to long and short exposure."""
import argparse
from decimal import Decimal
import json
import math
from pathlib import Path


def attribute(ledger):
    q=Decimal(0)
    totals={side:{'price_pnl':Decimal(0),'funding_pnl':Decimal(0),'fees':Decimal(0)} for side in ('long','short')}
    for event in ledger:
        if event['event']=='funding':
            if q:totals['long' if q>0 else 'short']['funding_pnl']+=Decimal(event['payment'])
        if event['event'] not in ('fill','liquidation'):continue
        delta=Decimal(event['contracts']); fee=Decimal(event['fee']); realized=Decimal(event['realized_pnl'])
        closes=q*delta<0
        if closes:
            closed=min(abs(q),abs(delta)); closing_fee=fee*closed/abs(delta)
            totals['long' if q>0 else 'short']['price_pnl']+=realized
            totals['long' if q>0 else 'short']['fees']+=closing_fee
            if abs(delta)>closed:totals['long' if delta>0 else 'short']['fees']+=fee-closing_fee
        else:totals['long' if delta>0 else 'short']['fees']+=fee
        q+=delta
    if q:raise ValueError('ledger has an open terminal position')
    for total in totals.values():total['net_pnl']=total['price_pnl']+total['funding_pnl']-total['fees']
    return {side:{k:float(v) for k,v in total.items()} for side,total in totals.items()}


def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);args=p.parse_args()
    results=[]
    summary=json.loads((args.output/'summary.json').read_text())
    for file in sorted(args.output.glob('*-8bps-*-ledger.json')):
        for record in json.loads(file.read_text()):
            attribution=attribute(record['ledger'])
            label=file.name.removesuffix('-ledger.json')
            prefix,mode=label.split('-8bps-')
            strategy,period=prefix.rsplit('-',1)
            row=next(r for r in summary['results'] if r['strategy']==strategy and r['period']==period and r['mode']==mode and r['cost_bps']==8)
            expected=row['per_asset'][record['symbol']]['pnl']
            if not math.isclose(sum(a['net_pnl'] for a in attribution.values()),expected,abs_tol=1e-6):
                raise AssertionError('directional attribution does not reconcile')
            results.append(dict(path=file.name,symbol=record['symbol'],attribution=attribution))
    (args.output/'direction-attribution.json').write_text(json.dumps(results,indent=2))
    print('attributed',len(results),'asset ledgers')

if __name__=='__main__':main()
