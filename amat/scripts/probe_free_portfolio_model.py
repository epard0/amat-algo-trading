#!/usr/bin/env python3
"""Bounded free-only structured-output capability probe; no portfolio authority."""
from __future__ import annotations
import argparse, asyncio, datetime as dt, fcntl, json, os
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen
from llmrouter.contracts import ChatRequest
from llmrouter.transports import OpenAICompatibleTransport

MODEL='nvidia/nemotron-3-super-120b-a12b:free'

def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--cycle-id',default='initial-foundation');a=p.parse_args()
    root=a.campaign.resolve();root.mkdir(parents=True,exist_ok=True)
    key=os.getenv('OPENROUTER_API_KEY')
    if not key:
        try:
            from dotenv import dotenv_values
            repo=Path(__file__).resolve().parents[1]
            for filename in ['.env.local','.env','.env.production.local','.env.production']:
                key=dotenv_values(repo/filename).get('OPENROUTER_API_KEY')
                if key:break
        except ImportError:pass
    if not key:raise SystemExit('OpenRouter credential unavailable; no fallback')
    with urlopen('https://openrouter.ai/api/v1/models',timeout=15) as r:models=json.load(r)['data']
    model=next((r for r in models if r['id']==MODEL),None)
    if not model or not MODEL.endswith(':free') or any(Decimal(str(model.get('pricing',{}).get(k,'-1')))!=0 for k in ['prompt','completion']) or any(Decimal(str(v))!=0 for v in model.get('pricing',{}).values()):
        raise SystemExit('Pinned model not verified zero-priced; no fallback')
    schema={'type':'object','properties':{'action':{'type':'string','enum':['hold']},'authority':{'type':'string','enum':['research_only']},'reason':{'type':'string'}},'required':['action','authority','reason'],'additionalProperties':False}
    request=ChatRequest(system='You are testing an AMAT research-only portfolio proposal interface. You have no tools, broker access or execution authority. Return only the supplied JSON schema.',user='Synthetic capability test only: no market or portfolio data is supplied. The only valid proposal is hold with research_only authority. This is not a trading decision or backtest.',schema_json=json.dumps(schema),schema_name='portfolio_probe',max_output_tokens=512,timeout_seconds=45,reasoning_effort='none',persistence='metadata_only')
    with (root/'free-model-budget.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        ledger=root/'free-model-calls.jsonl';lines=ledger.read_text().splitlines() if ledger.exists() else []
        if len(lines)>=24:raise SystemExit('Campaign free-model request budget exhausted')
        if sum(json.loads(line).get('cycle_id','initial-foundation')==a.cycle_id for line in lines)>=2:raise SystemExit('Cycle free-model request budget exhausted')
        record={'cycle_id':a.cycle_id,'attempt':len(lines)+1,'at':dt.datetime.now(dt.UTC).isoformat(),'model':MODEL,'purpose':'synthetic structured capability probe','request_sha256':request.digest(),'max_output_tokens':512,'timeout_seconds':45,'paid_fallback':False}
        with ledger.open('a') as f:f.write(json.dumps(record)+'\n')
    response=asyncio.run(OpenAICompatibleTransport('chat').generate(request,model=MODEL,api_key=key,base_url='https://openrouter.ai/api/v1'))
    parsed=None
    if response.ok and response.text:
        try:
            value=json.loads(response.text)
            if set(value)=={'action','authority','reason'} and value['action']=='hold' and value['authority']=='research_only' and isinstance(value['reason'],str):parsed=value
        except (ValueError,TypeError):pass
    result=dict(record,completed_at=dt.datetime.now(dt.UTC).isoformat(),error_class=response.error_class,served_model=response.served_model,requested_model=response.model,input_tokens=response.input_tokens,output_tokens=response.output_tokens,elapsed_seconds=response.elapsed_seconds,structured_contract_passed=parsed is not None,proposal=parsed,execution_authority='none',performance_evidence=False)
    (root/f'free-model-probe-{record["attempt"]}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':main()
