"""Firm-aware diagnostics; comparisons do not substitute for full firm replay."""
from __future__ import annotations

import argparse
import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import numpy as np

from autoresearch.funded import evaluate, load_sessions, write

PROFILES = {
    'apex_50k_eod_pa': {'firm':'Apex','stage':'performance','consistency_limit':.5,'strict':True,
        'qualifying_day_profit':250.,'qualifying_days':5,'minimum_request_balance':52600.,
        'safety_net_balance':52100.,'pnl_basis':'net',
        'source':'https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-payouts/'},
    'topstep_50k_combine': {'firm':'Topstep','stage':'evaluation','consistency_limit':.55,'strict':False,
        'profit_target':3000.,'dynamic_target':True,'pnl_basis':'net',
        'source':'https://help.topstep.com/en/articles/8284208-consistency-at-topstep'},
    'tradeify_select_evaluation': {'firm':'Tradeify','stage':'evaluation','consistency_limit':.4,'strict':False,
        'pnl_basis':'excluding_commission',
        'source':'https://help.tradeify.co/en/articles/10468320-rules-consistency-rule'},
}


def diagnostics(window: dict, profile: dict) -> dict:
    daily=window.get('daily')
    if daily is None:
        return {'status':'missing_daily_path','actual_payout_eligibility':None}
    observed=[x for x in daily if x['status']=='observed']
    pnl=np.array([x['pnl'] for x in observed],dtype=float)
    traded=np.array([x['pnl'] for x in observed if x['contracts']>0],dtype=float)
    net=float(pnl.sum())
    equity=np.r_[50000.,50000.+np.cumsum(pnl)]
    base=pnl if profile['pnl_basis']=='net' else None
    ratio=float(max(0.,base.max())/base.sum()) if base is not None and len(base) and base.sum()>0 else None
    limit=profile['consistency_limit']
    consistent=None if ratio is None else (ratio<limit if profile['strict'] else ratio<=limit)
    best=float(max(0.,pnl.max())) if len(pnl) else 0.
    required_profit=float((Decimal(str(best))/Decimal(str(limit))).quantize(Decimal('.01'),rounding=ROUND_CEILING))
    adjusted=max(profile.get('profit_target',0),required_profit) if profile.get('dynamic_target') else None
    threshold=profile.get('qualifying_day_profit')
    qualifying=int(sum(x['contracts']>0 and x['pnl']>=threshold for x in observed)) if threshold is not None else None
    end=50000+net
    result={'status':'diagnostic_only','pnl':net,'trade_count':len(traded),
        'net_expectancy_per_trade':float(traded.mean()) if len(traded) else None,
        'win_rate':float((traded>0).mean()) if len(traded) else None,
        'profit_factor':float(traded[traded>0].sum()/-traded[traded<0].sum()) if (traded<0).any() else None,
        'maximum_eod_drawdown':float(np.max(np.maximum.accumulate(equity)-equity)),
        'maximum_intraday_drawdown':None,'minimum_observed_eod_floor_buffer':min((x['equity']-x['floor'] for x in observed),default=None),
        'worst_day_loss':float(max(0.,-pnl.min())) if len(pnl) else None,
        'active_days':int(sum(x['contracts']>0 for x in observed)),
        'qualifying_days_against_profile':qualifying,
        'qualifying_day_shortfall':max(0,profile['qualifying_days']-qualifying) if qualifying is not None else None,
        'consistency_fraction':ratio,'consistency_satisfied':consistent,'consistency_basis':profile['pnl_basis'],
        'consistency_profit_shortfall':max(0.,required_profit-net) if base is not None else None,
        'strict_consistency_requires_additional_positive_increment':bool(profile['strict'] and ratio is not None and ratio==limit),
        'adjusted_evaluation_target':adjusted,
        'minimum_payout_balance_shortfall':max(0.,profile['minimum_request_balance']-end) if 'minimum_request_balance' in profile else None,
        'profit_above_safety_net':max(0.,end-profile['safety_net_balance']) if 'safety_net_balance' in profile else None,
        'actual_payout_eligibility':None,'net_rewards_after_program_fees':None,
        'missing_measurements':['tick-level drawdown','complete selected-firm account replay','program fees and paid rewards']}
    if base is None: result['missing_measurements'].append('commission-excluded daily P&L required by this profile')
    return result


def run(campaign: Path, source: Path):
    saved=json.loads((campaign/'summary.json').read_text())
    manifest=json.loads((source/'data-manifest.json').read_text())
    from autoresearch.funded import digest
    if digest(Path(manifest['path']))!=manifest['sha256']: raise ValueError('data hash changed')
    sessions,_=load_sessions(Path(manifest['path']),manifest['timezone'])
    cutoff=json.loads((campaign/'campaign.json').read_text())['split_cutoff']
    validation=[x for x in sessions if x[0]>=cutoff]
    policy=saved['selected_training']['policy']
    replay=evaluate(validation,policy,{'commission_side':1.,'slip_ticks':2},'eod',include_paths=True)
    prior=next(x for x in saved['validation_selected']['results'] if x['cost']=='adverse' and x['mode']=='eod')
    if not np.isclose(prior['mean_pnl'],replay['mean_pnl']): raise ValueError('metric replay differs from frozen selection')
    out={'checked_date':'2026-10-14','discovery_source':'https://propfirmrated.com/best-prop-firms',
         'discovery_status':'affiliate comparison; primary sources govern rule diagnostics',
         'selection_unchanged':True,'profiles':PROFILES,'windows':[]}
    for window in replay['outcomes']:
        out['windows'].append({'start':window['start'],'evaluation_outcome':window['status'],
             'diagnostics':{name:diagnostics(window,p) for name,p in PROFILES.items()}})
    write(campaign/'firm-metrics.json',out)
    lines=['# Additional funded trading metrics','',
           'Selected policy unchanged. Diagnostics on the same Apex evaluation validation paths, not full Topstep/Tradeify/PA simulations. Actual payout eligibility and net rewards remain unknown.','',
           '| Window | Net P&L $ | Trades | Win rate | Profit factor | EOD DD $ | Apex $250 qualifying days |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for row in out['windows']:
        d=row['diagnostics']['apex_50k_eod_pa']
        lines.append(f"| {row['start']} | {d['pnl']:.2f} | {d['trade_count']} | {d['win_rate']} | {d['profit_factor']} | {d['maximum_eod_drawdown']:.2f} | {d['qualifying_days_against_profile']} |")
    lines+=['','Tracked: consistency and shortfall, qualifying days, target adjustments, payout-buffer shortfall, expectancy, profit factor, win rate, EOD drawdown and remaining capacity. Missing: actual funded-stage replay, measured intraday drawdown, costs of evaluation/activation/subscription and rewards. Tradeify commission-basis comparison remains unknown rather than using the wrong P&L basis.','',
            'These extra metrics were added AFTER the 20-batch campaign and did not influence its selection. A future campaign must freeze them into its objective before optimization.']
    (campaign/'FIRM-METRICS.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    a=parser.parse_args()
    run(a.campaign,a.source)
