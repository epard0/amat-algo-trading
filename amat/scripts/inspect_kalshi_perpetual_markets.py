"""Read-only Kalshi perpetual inventory. Secrets never printed or persisted."""
import base64
import datetime as dt
import json
import os
from pathlib import Path
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, ed25519

HOST='https://external-api.kalshi.com'
KEY_DIR=Path.home()/'Documents/Polymarket-BTC-15-Minute-Trading-Bot'
OUT=Path('outputs/crypto-directional-2026-10-19/kalshi-inventory')


def request(path, key, key_id, params=None):
    if path not in ('/trade-api/v2/margin/enabled','/trade-api/v2/margin/markets','/trade-api/v2/margin/fee_tiers','/trade-api/v2/margin/funding_rates/historical'):
        raise ValueError('read-only endpoint outside allowlist')
    timestamp=str(int(time.time()*1000)); message=(timestamp+'GET'+path).encode()
    if isinstance(key,ed25519.Ed25519PrivateKey):signature=key.sign(message)
    else:signature=key.sign(message,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=padding.PSS.DIGEST_LENGTH),hashes.SHA256())
    return requests.get(HOST+path,params=params,headers={
        'KALSHI-ACCESS-KEY':key_id,'KALSHI-ACCESS-TIMESTAMP':timestamp,
        'KALSHI-ACCESS-SIGNATURE':base64.b64encode(signature).decode()},timeout=30,allow_redirects=False)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    key_id=os.environ.get('KALSHI_API_KEY')
    if not key_id:raise RuntimeError('KALSHI_API_KEY absent; no secret fallback loaded')
    attempts=[]; chosen=None; enabled=None
    for name in ('private_key.pem','kalshi_paper_private_key.pem'):
        try:key=serialization.load_pem_private_key((KEY_DIR/name).read_bytes(),password=None)
        except Exception as exc:
            attempts.append(dict(pem_name=name,key_load_error=type(exc).__name__));continue
        response=request('/trade-api/v2/margin/enabled',key,key_id)
        attempts.append(dict(pem_name=name,status=response.status_code))
        if response.status_code==200:
            chosen=key;enabled=response.json();break
        if response.status_code not in (401,403):break
    if chosen is None:
        # Public inventory still useful if margin entitlement cannot be authenticated.
        response=requests.get(HOST+'/trade-api/v2/margin/markets',timeout=30,allow_redirects=False)
    else:response=request('/trade-api/v2/margin/markets',chosen,key_id)
    status=dict(retrieved_at=dt.datetime.now(dt.UTC).isoformat(),environment='production',
        auth_attempts=attempts,enabled=enabled,markets_status=response.status_code,
        authenticated=chosen is not None,method='GET only')
    if response.status_code==200:
        body=response.json();(OUT/'markets.json').write_text(json.dumps(body,indent=2))
        rows=body.get('markets',[])
        print(json.dumps(dict(status=status,markets=[{k:r.get(k) for k in ('ticker','title','asset_class','status','contract_size','underlying_multiplier','tick_size','leverage_estimate')} for r in rows]),indent=2))
    else:
        try:status['api_error']={k:v for k,v in response.json().items() if k in ('code','message')}
        except Exception:status['api_error']='non-JSON response'
        print(json.dumps(status,indent=2))
    (OUT/'status.json').write_text(json.dumps(status,indent=2))

if __name__=='__main__':main()
