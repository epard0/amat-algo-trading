"""Twenty sequential adaptive research batches; fixed rules/costs and sealed validation."""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path
from typing import Literal, get_args

from pydantic import Field

from autoresearch.funded import digest, evaluate, load_sessions, write
from autoresearch.funded_state import sync_campaign
from autoresearch.ledger import Ledger
from llmrouter.chain import Chain, Router, Target
from llmrouter.contracts import ChatRequest, Record

MODEL = "gemini-4-argon"


class Policy(Record):
    opening_minutes: Literal[5, 10, 15, 20, 30] = 5
    risk: Literal[50, 100, 150, 200] = 200
    stop_fraction: Literal[.25, .5, .75, 1.] = 1.
    take_profit_r: Literal[0., 1., 1.5, 2., 3.] = 0.
    breakout_buffer_ticks: Literal[0, 2, 4, 8] = 0
    entry_cutoff_minute: Literal[60, 120, 180] = 120
    direction: Literal["both", "long", "short"] = "both"


class Advice(Record):
    diagnosis: str = Field(max_length=1000)
    workflow_improvement: str = Field(max_length=800)
    proposals: list[Policy] = Field(min_length=1, max_length=3)
    falsification: str = Field(max_length=800)


def score(results: list[dict]) -> float:
    # Optimize worst scenario mean P&L with a failure penalty, not best-window luck.
    return min((r["mean_pnl"] or 0.) - 2000*r["failed"]/max(1,r["windows"]) for r in results)


def measure(sessions: list, policy: Policy) -> dict:
    rows = []
    for name, commission, ticks in (("base", .75, 1), ("adverse", 1., 2)):
        for mode in ("eod", "intraday"):
            result = evaluate(sessions, policy.model_dump(), {"commission_side":commission,"slip_ticks":ticks}, mode)
            rows.append({"cost":name,"mode":mode,**result})
    return {"policy":policy.model_dump(),"score":score(rows),"results":rows}


def compact(value: dict) -> dict:
    return {**value,"results":[{k:v for k,v in r.items() if k!='outcomes'} for r in value['results']]}


def fallback(incumbent: Policy, batch: int) -> list[Policy]:
    """Recorded deterministic bounded proposals if model fails or repeats policies."""
    axes=[('stop_fraction',[.25,.5,.75,1.]),('take_profit_r',[0.,1.,1.5,2.,3.]),
          ('opening_minutes',[5,10,15,20,30]),('direction',['both','long','short']),
          ('entry_cutoff_minute',[60,120,180]),('breakout_buffer_ticks',[0,2,4,8]),('risk',[50,100,150,200])]
    name, values=axes[(batch-1)%len(axes)]
    return [Policy.model_validate({**incumbent.model_dump(),name:x}) for x in values]


def unseen_proposals(incumbent: Policy, batch: int, seen: set[str], preferred: list[Policy], count: int = 3) -> list[Policy]:
    selected=[]
    known=set(seen)
    for policy in preferred + [p for offset in range(7) for p in fallback(incumbent,batch+offset)]:
        if policy.digest() not in known:
            selected.append(policy)
            known.add(policy.digest())
        if len(selected)==count: return selected
    # Local neighborhood exhausted: bounded deterministic two-axis diversification.
    rng=random.Random(20261014+batch)
    names=list(Policy.model_fields)
    for _ in range(200):
        values=incumbent.model_dump()
        for name in rng.sample(names,2):
            values[name]=rng.choice(get_args(Policy.model_fields[name].annotation))
        policy=Policy.model_validate(values)
        if policy.digest() not in known:
            selected.append(policy)
            known.add(policy.digest())
        if len(selected)==count: break
    if len(selected)!=count: raise ValueError('Bounded proposal space exhausted before a full batch')
    return selected


async def batches(root: Path, source: Path, count: int = 20, resume: bool = False, batch_size: int = 3, seed_policy: Policy | None = None, skip_model: bool = False) -> None:
    if (root/'campaign.json').exists() and not resume:
        raise ValueError('Use a fresh batch directory; existing campaign is retained')
    root.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((source/'data-manifest.json').read_text())
    path=Path(manifest['path'])
    if digest(path)!=manifest['sha256']:
        raise ValueError('Data hash changed')
    sessions,audit=load_sessions(path,manifest['timezone'])
    cutoff='2026-03-02'
    train=[x for x in sessions if x[0]<cutoff]
    validation=[x for x in sessions if x[0]>=cutoff]
    if len(train)<25 or len(validation)<25:
        raise ValueError('Insufficient split coverage')
    definition={'model':MODEL,'batches':count,'batch_size':batch_size,'seed_policy':(seed_policy or Policy()).model_dump(),
          'model_disabled_reason':'Previously confirmed zero quota; no API requests' if skip_model else None,'data_sha256':manifest['sha256'],
          'engine_sha256':digest(Path(__file__).with_name('funded.py')),'controller_sha256':digest(Path(__file__)),
          'target':3000,'calendar_days':30,'drawdown':2000,'risk_max':200,'max_contracts':4,
          'split_cutoff':cutoff,'validation_access':'only after final selection; previously researched sample, not pristine',
          'selection':'maximize minimum base/adverse EOD/intraday training mean P&L minus 2000*failure fraction',
          'max_api_tasks':count,'max_output_tokens_per_attempt':1800,'token_reservation_limit':150000,
          'costs':'scenario assumptions unchanged','deployment':'disabled','schema':Advice.model_json_schema()}
    if resume:
        saved=json.loads((root/'campaign.json').read_text())
        for key in ('model','batch_size','seed_policy','data_sha256','engine_sha256','split_cutoff','selection','risk_max','target','drawdown','calendar_days'):
            if saved[key]!=definition[key]: raise ValueError(f'Resume dependency changed: {key}')
        write(root/f'resume-{time.time_ns()}.json',{'controller_sha256':definition['controller_sha256'],
              'reason':'provider timeout backoff and resumable checkpoints; strategy/scoring unchanged'})
    else:
        write(root/'campaign.json',definition)
    write(root/'data-audit.json',audit)
    target=Target(provider='gemini',model=MODEL)
    chain=Chain(targets=(target,),complex_targets=(target,))
    cache={}
    def cached(policy):
        key=policy.digest()
        if key not in cache:
            cache[key]=measure(train,policy)
        return cache[key]
    incumbent=seed_policy or Policy()
    initial=cached(incumbent)
    best=initial
    history=json.loads((root/'progress.json').read_text()) if resume and (root/'progress.json').exists() else []
    if history:
        for item in history:
            measured=json.loads((root/f'batch-{item["batch"]:02d}'/'results.json').read_text())['measured']
            for value in measured: cache[Policy.model_validate(value['policy']).digest()]=value
        incumbent=Policy.model_validate(history[-1]['incumbent'])
        best=cached(incumbent)
    next_proposals=[Policy(stop_fraction=.5),Policy(stop_fraction=.25),Policy(take_profit_r=2.)]
    if history:
        last_model=json.loads((root/f'batch-{len(history):02d}'/'model.json').read_text())
        next_proposals=Advice.model_validate(last_model['advice']).proposals if last_model['status']=='succeeded' else fallback(incumbent,len(history))
        next_proposals=unseen_proposals(incumbent,len(history)+1,set(cache),next_proposals,batch_size)
    api_blocked_until=time.monotonic()+60 if resume else 0.
    with Ledger(root,150000) as ledger:
        for batch in range(len(history)+1,count+1):
            started=time.monotonic()
            folder=root/f'batch-{batch:02d}'
            if folder.exists():
                folder.rename(root/f'interrupted-batch-{batch:02d}-{time.time_ns()}')
            folder.mkdir()
            before=len(cache)
            # Batch plans are persisted before any simulation or model review.
            proposals=unseen_proposals(incumbent,batch,set(cache),next_proposals,batch_size)
            write(folder/'plan.json',{'batch':batch,'parent':incumbent.model_dump(),'proposals':[p.model_dump() for p in proposals],
                  'workflow_policy':{'deduplicate':True,'reuse_training_results':True,'model_context':'incumbent and recent summaries only','validation_sealed':True}})
            measured=[cached(p) for p in proposals]
            for value in measured:
                if value['score']>best['score']:
                    best=value
                    incumbent=Policy.model_validate(value['policy'])
            evidence={'batch':batch,'target':3000,'incumbent':compact(best),
                      'recent':[compact(x) for x in measured],
                      'previous_diagnosis':history[-1].get('diagnosis') if history else None,
                      'constraints':'short unverified MNQ sample; 30-day windows overlap; no validation data supplied; do not claim robust profit'}
            request=ChatRequest(system='Act as a skeptical quantitative researcher. Diagnose this batch and propose 1-3 bounded policy changes for the NEXT batch. Prefer informative single-factor ablations. Costs/account target/risk caps/data split are immutable. Suggest one efficiency or research-process improvement; no code or tool execution. Avoid repeated policies and never infer deployment readiness.',
                   user=json.dumps(evidence),schema_json=json.dumps(Advice.model_json_schema()),schema_name='funded_batch_advice',
                   task_class='complex',max_output_tokens=1800,timeout_seconds=45,persistence='metadata_only')
            router=Router(chain)
            advice=None
            if not skip_model and time.monotonic()>=api_blocked_until:
                response=await router.complete(request,ledger,validate=Advice.model_validate_json)
                if response and response.ok and response.text:
                    advice=Advice.model_validate_json(response.text)
                    write(folder/'model.json',{'status':'succeeded','model':response.model,'served_model':response.served_model,
                          'input_tokens':response.input_tokens,'output_tokens':response.output_tokens,'advice':advice.model_dump()})
                else:
                    write(folder/'model.json',{'status':'unavailable','model':MODEL,'attempts':router.last_attempts})
                    if any(x.get('status') in ('rate_limit','quota','timeout','auth','transport') for x in router.last_attempts):
                        api_blocked_until=time.monotonic()+60
            else:
                write(folder/'model.json',{'status':'quota_blocked' if skip_model else 'backoff','model':MODEL})
            next_proposals=advice.proposals if advice else fallback(incumbent,batch)
            # Apply an efficiency update after each batch: prune known trials and
            # substitute untested single-axis ablations. Never silently rerun duplicates.
            next_proposals=unseen_proposals(incumbent,batch,set(cache),next_proposals,batch_size)
            record={'batch':batch,'new_candidates':len(cache)-before,'cache_hits':len(proposals)-(len(cache)-before),
                    'incumbent_score':best['score'],'incumbent':incumbent.model_dump(),
                    'model_succeeded':advice is not None,
                    'diagnosis':advice.diagnosis if advice else 'Deterministic ablation; model unavailable',
                    'workflow_improvement':advice.workflow_improvement if advice else 'Prune duplicate policies; test an untried single-factor ablation',
                    'applied_workflow_update':{'unseen_next_candidates':sum(p.digest() not in cache for p in next_proposals),
                           'cache_entries':len(cache),'validation_accesses':0},
                    'seconds':round(time.monotonic()-started,3)}
            write(folder/'results.json',{'summary':record,'measured':measured})
            history.append(record)
            write(root/'progress.json',history)
            sync_campaign(root)
            print(json.dumps({k:record[k] for k in ('batch','new_candidates','incumbent_score','model_succeeded','seconds')}),flush=True)
    # Only now evaluate baseline and training-selected winner on sealed validation.
    final=measure(validation,incumbent)
    baseline=measure(validation,Policy())
    summary={'completed_batches':len(history),'completed_research_iterations':sum(x['new_candidates'] for x in history),
             'batch_size':batch_size,'unique_candidates':len(cache),'model':MODEL,
             'successful_model_reviews':sum(x['model_succeeded'] for x in history),
             'initial_training':compact(initial),'selected_training':compact(best),
             'validation_baseline':compact(baseline),'validation_selected':compact(final),
             'selection_used_validation':False,'deployment':'DISABLED',
             'limitations':['sample previously inspected','short history','unverified roll identity','assumed costs','many adaptive trials; no statistical profitability claim']}
    write(root/'summary.json',summary)
    sync_campaign(root)
    write(root/'candidate-cache.json',list(cache.values()))
    lines=[f'# {len(history)} adaptive MNQ research batches','',f'Model: {MODEL}; completed: {len(history)}; research iterations: {summary["completed_research_iterations"]}; unique candidates including seed: {len(cache)}; successful model reviews: {summary["successful_model_reviews"]}.','',
           'Training selection only; final validation accessed after the last batch. This public sample was previously inspected, so validation is not pristine. Deployment disabled.','',
           '| Batch | New candidates | Training worst-case score $ | Gemini review | Seconds |','|---:|---:|---:|---|---:|']
    for x in history: lines.append(f'| {x["batch"]} | {x["new_candidates"]} | {x["incumbent_score"]:.2f} | {x["model_succeeded"]} | {x["seconds"]:.2f} |')
    lines+=['',f'Initial training score: ${initial["score"]:.2f}; selected: ${best["score"]:.2f}.',
            f'Validation baseline score: ${baseline["score"]:.2f}; selected: ${final["score"]:.2f}.','',
            'Score is worst base/adverse EOD/intraday mean 30-day P&L minus a failure penalty. It is not account return or independent pass probability. See summary.json for all outcomes.',
            '',f'Selected policy: `{incumbent.model_dump_json()}`']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--batches',type=int,default=20)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--batch-size',type=int,default=3,choices=range(1,6))
    p.add_argument('--seed-campaign',type=Path)
    p.add_argument('--skip-model',action='store_true',help='Record quota-blocked model status and use deterministic proposals')
    a=p.parse_args()
    if not 1<=a.batches<=20: p.error('batches must be 1..20')
    seed=Policy.model_validate(json.loads((a.seed_campaign/'summary.json').read_text())['selected_training']['policy']) if a.seed_campaign else None
    asyncio.run(batches(a.root,a.source,a.batches,a.resume,a.batch_size,seed,a.skip_model))


if __name__=='__main__': main()
