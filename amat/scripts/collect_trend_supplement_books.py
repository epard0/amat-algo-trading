"""Read-only native execution calibration before broad trend-short study."""
import sys,json,time,hashlib,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
sys.path.insert(0,str(Path('../kalshi-perps/src').resolve()))
from kalshi_perps.venue import Transport,VenueError
from cryptography.hazmat.primitives import serialization
from kalshi_perps.domain import registry,book
OUT=Path('data/research/trend-supplement-native-2026-10-22');OUT.mkdir(exist_ok=True)
if (OUT/"quotes.jsonl").exists():raise ValueError("preserve prior capture")
SYMBOLS=['SUI','WLD'];TICKERS=['KX'+s+'PERP' for s in SYMBOLS]
t=Transport('production',writable=False);markets=t.pages('/markets','markets',limit=100);risk=t.request('GET','/risk_parameters');fees=None
for name in ('private_key.pem','kalshi_paper_private_key.pem'):
 try:
  key=serialization.load_pem_private_key((Path.home()/'Documents/Polymarket-BTC-15-Minute-Trading-Bot'/name).read_bytes(),password=None)
  auth=Transport('production',key_id=os.environ['KALSHI_API_KEY'],private_key=key,writable=False)
  fees=auth.request('GET','/fee_tiers');auth.client.close();break
 except VenueError as exc:
  auth.client.close()
  if exc.status not in (401,403):raise
if fees is None:raise RuntimeError('Authenticated fee retrieval failed')
raw={r['ticker']:r for r in markets if r['ticker'] in TICKERS};assert set(raw)==set(TICKERS);inst=registry(dict(markets=list(raw.values())))
for i in inst.values():i.admit()
(OUT/'venue.json').write_text(json.dumps(dict(retrieved_ms=t.clock(),markets=raw,risk_parameters=risk,fees=fees),indent=2))
for j in range(30):
 def fetch(ticker):
  q=t.request('GET','/markets/'+ticker+'/orderbook',params={'depth':100})['orderbook'];book(q,inst[ticker]);return ticker,q
 with ThreadPoolExecutor(max_workers=5) as pool:quotes=dict(pool.map(fetch,TICKERS))
 with (OUT/'quotes.jsonl').open('a') as f:f.write(json.dumps(dict(timestamp_ms=t.clock(),quotes=quotes))+'\n')
 print('captured',j+1,flush=True)
 if j<29:time.sleep(1)
t.client.close();rows=[json.loads(l) for l in (OUT/'quotes.jsonl').read_text().splitlines()];curves={}
for ticker in TICKERS:
 curves[ticker]={}
 for side in ['bids','asks']:
  table=[];prev_med=prev_p95=0.
  for size in [100,500,1000,2500,5000,10000,25000,50000,100000,200000,300000,500000,750000,1000000]:
   values=[]
   for row in rows:
    q={s:sorted([(float(p),float(n)) for p,n in row['quotes'][ticker][s] if float(n)>0],reverse=s=='bids') for s in ['bids','asks']};mid=(q['bids'][0][0]+q['asks'][0][0])/2;count=size/mid;left=count;value=0
    for price,quantity in q[side]:
     take=min(left,quantity);left-=take;value+=take*price
     if left<1e-10:break
    if left>1e-8:continue
    values.append(max(0.,(value/count/mid-1)*(1 if side=='asks' else -1)*10000))
   med=max(prev_med,float(np.median(values))) if values else None;high=max(prev_p95,med,float(np.quantile(values,.95))) if values else None
   coverage=len(values)/len(rows)
   table.append(dict(notional=size,full_coverage_fraction=coverage,median_bps=med,p95_bps=high,samples=len(values)))
   if coverage>=.95:prev_med=med;prev_p95=high
  curves[ticker][side]=table
(OUT/'cost-curves.json').write_text(json.dumps(curves,indent=2));(OUT/'manifest.json').write_text(json.dumps(dict(samples=30,requested_depth=100,quotes_sha256=hashlib.sha256((OUT/'quotes.jsonl').read_bytes()).hexdigest(),collector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),read_only=True),indent=2));print('DONE',flush=True)
