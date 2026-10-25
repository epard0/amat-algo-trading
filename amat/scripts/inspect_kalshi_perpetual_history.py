"""Read-only account fee table and historical funding coverage by active crypto."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from inspect_kalshi_perpetual_markets import request, KEY_DIR, OUT


def main():
    key=serialization.load_pem_private_key((KEY_DIR/'private_key.pem').read_bytes(),password=None)
    key_id=os.environ['KALSHI_API_KEY']
    fees=request('/trade-api/v2/margin/fee_tiers',key,key_id)
    if fees.status_code==200:(OUT/'fees.json').write_text(json.dumps(fees.json(),indent=2))
    else:(OUT/'fee-status.json').write_text(json.dumps(dict(status=fees.status_code)))
    markets=json.loads((OUT/'markets.json').read_text())['markets']
    active=[m for m in markets if m['asset_class']=='Crypto' and m['status']=='active']
    def fetch(m):
        ticker=m['ticker']
        response=request('/trade-api/v2/margin/funding_rates/historical',key,key_id,dict(ticker=ticker,start_ts=1577836800,end_ts=1790812800))
        row=dict(ticker=ticker,status=response.status_code)
        if response.status_code==200:
            body=response.json();(OUT/f'{ticker}-historical-funding.json').write_text(json.dumps(body))
            rates=body.get('funding_rates',[])
            times=sorted(r['funding_time'] for r in rates)
            row.update(events=len(rates),first=times[0] if times else None,last=times[-1] if times else None)
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:coverage=list(pool.map(fetch,active))
    (OUT/'historical-coverage.json').write_text(json.dumps(coverage,indent=2))
    print(json.dumps(dict(fee_status=fees.status_code,fees=fees.json() if fees.status_code==200 else None,coverage=coverage),indent=2))

if __name__=='__main__':main()
