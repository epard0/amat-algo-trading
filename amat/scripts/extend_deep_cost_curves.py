"""Keep prior675sample curves exactly; calibrate only larger sizes with deep books."""
from pathlib import Path
import json,hashlib
from autoresearch import crypto_adx_leverage_study as original
out=Path('data/research/kalshi-deep-books-2026-10-20')
manifest=json.loads((out/'manifest.json').read_text());raw=out/'quotes.jsonl';assert hashlib.sha256(raw.read_bytes()).hexdigest()==manifest['quote_sha256']
rows=[json.loads(x) for x in raw.read_text().splitlines()];assert len(rows)==30
original.SIZES=(225000,250000,300000,400000,500000,600000,750000,1000000)
tail=original.build_curves(rows)
base=json.loads(Path('outputs/crypto-adx-leverage-costs-2026-10-20-v2/cost-curves.json').read_text())
for ticker,sides in tail.items():
    for side,values in sides.items():
        prev_med=base[ticker][side][-1]['median_bps'];prev_p95=base[ticker][side][-1]['p95_bps']
        for value in values:
            if value['median_bps'] is not None:
                value['median_bps']=max(prev_med,value['median_bps'])
                value['p95_bps']=max(prev_p95,value['median_bps'],value['p95_bps'])
                if value['full_coverage_fraction']>=.95:prev_med=value['median_bps'];prev_p95=value['p95_bps']
            value['calibration_samples']=30;value['source']='New native depth100 observations; >=95% full coverage required; cost no lower than smaller supported bucket.'
        base[ticker][side].extend(values)
        supported=[x['notional'] for x in base[ticker][side] if x['full_coverage_fraction']>=.95]
        print(ticker,side,'maximum empirically supported order',max(supported),flush=True)
(out/'extended-cost-curves.json').write_text(json.dumps(base,indent=2))
manifest['extended_curve_sha256']=hashlib.sha256((out/'extended-cost-curves.json').read_bytes()).hexdigest();manifest['extension_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps(manifest,indent=2))
