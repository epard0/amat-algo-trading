"""Read-only30sample deep native cost calibration for previously unsupported order sizes."""
from pathlib import Path
import sys,json,time,hashlib
from datetime import datetime,timezone
sys.path.insert(0,str(Path('../kalshi-perps/src').resolve()))
from kalshi_perps.venue import Transport
from kalshi_perps.domain import book,registry
out=Path('data/research/kalshi-deep-books-2026-10-20');raw=out/'quotes.jsonl'
if raw.exists():raise ValueError('preserve prior capture')
tr=Transport('production',writable=False);inst=registry(json.loads(Path('../kalshi-perps/configs/instruments.json').read_text()))
for i in range(30):
    quotes={};start=tr.clock()
    for t in ('KXDOGEPERP','KXSOLPERP'):
        q=tr.request('GET','/markets/'+t+'/orderbook',params={'depth':100})['orderbook'];book(q,inst[t]);quotes[t]=q
    receipt=dict(timestamp_ms=tr.clock(),started_ms=start,requested_depth=100,quotes=quotes)
    with raw.open('a') as f:f.write(json.dumps(receipt)+'\n')
    if (i+1)%10==0:print('captured',i+1,'/30',flush=True)
    if i<29:time.sleep(2)
tr.client.close()
manifest=dict(samples=30,requested_depth=100,source='Unsigned GET /trade-api/v2/margin/markets/{ticker}/orderbook',collected_at_utc=datetime.now(timezone.utc).isoformat(),quote_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),collector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),use='Only extend frozen675sample cost curves above200,000USD; do not revise existing buckets.')
(out/'manifest.json').write_text(json.dumps(manifest,indent=2))
