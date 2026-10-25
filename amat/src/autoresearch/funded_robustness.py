"""Frozen, training-only robustness ranking of previously measured MNQ policies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from autoresearch.funded import digest, evaluate, load_sessions, trade_day, write
from autoresearch.funded_batches import Policy

COSTS = {'base': (.75, 1), 'adverse': (1., 2), 'stress': (1.5, 4)}


def activity_eligible(scenarios):
    return all(s['entries'] >= 10 for s in scenarios)


def cost_sensitivity(base, adverse):
    rows = []
    for a, b in zip(base, adverse, strict=True):
        if a['date'] != b['date']:
            raise ValueError('Unaligned replay dates')
        rows.append({'date': a['date'], 'base_pnl': a['pnl'], 'adverse_pnl': b['pnl'],
                     'pnl_change': b['pnl']-a['pnl'], 'base_contracts': a['contracts'],
                     'adverse_contracts': b['contracts'], 'base_exit': a.get('reason'),
                     'adverse_exit': b.get('reason')})
    return {'quantity_changed_days': sum(r['base_contracts'] != r['adverse_contracts'] for r in rows),
            'exit_reason_changed_days': sum(r['base_exit'] != r['adverse_exit'] for r in rows),
            'total_pnl_change': sum(r['pnl_change'] for r in rows),
            'largest_losses_of_pnl': sorted(rows, key=lambda r: r['pnl_change'])[:5],
            'interpretation': 'Scenario replay differences include discrete sizing and stop changes, not fees alone'}


def daily(sessions, policy, cost, delay=1):
    # Independent daily replay isolates signal economics from account stopping rules.
    return [dict(date=date, **trade_day(bars, policy, cost, 50000., 48000.,
                'eod', delay=delay)) for date, bars in sessions if bars is not None]


def stability(rows):
    values = np.array([r['pnl'] for r in rows], dtype=float)
    if len(values) < 15:
        raise ValueError('At least 15 complete sessions required')
    blocks = [float(x.mean()) for x in np.array_split(values, 3)]
    # Five-session moving-block bootstrap preserves short local dependence.
    rng = np.random.default_rng(20261014)
    starts = rng.integers(0, len(values)-4, size=(2000, (len(values)+4)//5))
    sampled = values[(starts[:, :, None]+np.arange(5)).reshape(2000, -1)[:, :len(values)]]
    return {'days': len(values), 'total_pnl': float(values.sum()),
            'mean_daily_pnl': float(values.mean()), 'block_means': blocks,
            'worst_block_mean': min(blocks),
            'bootstrap_mean_pnl_p05': float(np.quantile(sampled.mean(axis=1), .05)),
            'entries': sum(r['contracts'] > 0 for r in rows),
            'contract_round_trips': sum(r['contracts'] for r in rows),
            'stop_exits': sum(r.get('reason', '').startswith('stop') for r in rows)}


def run(root: Path, source: Path, campaigns: list[Path]):
    if root.exists():
        raise ValueError('Use a fresh output directory; preserve prior evidence')
    manifest = json.loads((source/'data-manifest.json').read_text())
    path = Path(manifest['path'])
    if digest(path) != manifest['sha256']:
        raise ValueError('Data hash changed')
    sessions, audit = load_sessions(path, manifest['timezone'])
    train = [x for x in sessions if x[0] < '2026-03-02']
    validation = [x for x in sessions if x[0] >= '2026-03-02']
    policies = {}
    inputs = {}
    previous = []
    for campaign in campaigns:
        summary = json.loads((campaign/'summary.json').read_text())
        previous.append(summary['selected_training']['policy'])
        for file in sorted(campaign.glob('batch-*/results.json')):
            inputs[str(file.resolve())] = digest(file)
            for measured in json.loads(file.read_text())['measured']:
                p = Policy.model_validate(measured['policy'])
                policies[p.digest()] = p.model_dump()
        for key in ('initial_training', 'selected_training'):
            p = Policy.model_validate(summary[key]['policy'])
            policies[p.digest()] = p.model_dump()
    definition = {'data_sha256': manifest['sha256'], 'engine_sha256': digest(Path(__file__).with_name('funded.py')),
                  'controller_sha256': digest(Path(__file__)), 'inputs': inputs, 'policies': policies,
                  'selection': 'minimum of three chronological block means and moving-block-bootstrap 5th percentile, across base/adverse/stress and 1/2 minute entry delays',
                  'cutoff': '2026-03-02', 'deployment': 'DISABLED',
                  'activity_gate': 'At least 10 training entries in every scenario; no-trade policies cannot win',
                  'bootstrap': '2000 resamples, length 5 sessions, seed 20261014; descriptive, selection bias uncorrected',
                  'limitations': ['Previously researched sample; validation is not pristine', 'No independent new market data',
                                  'Daily research score differs from account evaluation; selected accounts replayed separately',
                                  'Assumed costs; minute OHLC; unverified contract roll identity']}
    write(root/'protocol.json', definition)
    write(root/'data-audit.json', audit)
    results = []
    for i, (key, policy) in enumerate(policies.items(), 1):
        scenarios = []
        for name, (commission, ticks) in COSTS.items():
            for delay in (1, 2):
                rows = daily(train, policy, {'commission_side': commission, 'slip_ticks': ticks}, delay)
                scenarios.append({'cost': name, 'delay': delay, **stability(rows)})
        score = min(min(x['worst_block_mean'], x['bootstrap_mean_pnl_p05']) for x in scenarios)
        row = {'id': key, 'policy': policy, 'score': score, 'scenarios': scenarios,
               'activity_eligible': activity_eligible(scenarios)}
        results.append(row)
        write(root/'trials'/f'{key}.json', row)
        if i % 25 == 0:
            print(f'Robustness audited {i}/{len(policies)} policies', flush=True)
    results.sort(key=lambda x: (not x['activity_eligible'], -x['score'], x['id']))
    selected = results[0]
    if not selected['activity_eligible']:
        raise ValueError('No policy meets minimum activity; no selection')
    write(root/'selection.json', selected)  # Freeze selection before any validation replay.
    comparisons = []
    for policy in [selected['policy'], *previous]:
        if any(x['policy'] == policy for x in comparisons):
            continue
        rows = []
        for name, (commission, ticks) in COSTS.items():
            cost = {'commission_side': commission, 'slip_ticks': ticks}
            diagnostics = stability(daily(validation, policy, cost))
            for mode in ('eod', 'intraday'):
                rows.append({'cost': name, 'mode': mode, 'daily_diagnostics': diagnostics,
                             **evaluate(validation, policy, cost, mode)})
        sensitivity = cost_sensitivity(daily(validation, policy, {'commission_side': .75, 'slip_ticks': 1}),
                                       daily(validation, policy, {'commission_side': 1., 'slip_ticks': 2}))
        comparisons.append({'policy': policy, 'results': rows, 'cost_sensitivity': sensitivity})
    summary = {'policies_audited': len(results), 'training_scenario_replays': len(results)*6,
               'selected': selected, 'validation_comparisons': comparisons,
               'activity_eligible_policies': sum(r['activity_eligible'] for r in results),
               'training_gate_passed': selected['score'] > 0, 'deployment': 'DISABLED',
               'model_reviews': 0, 'model_reason': 'Previously confirmed zero generation quota',
               'limitations': definition['limitations']}
    write(root/'summary.json', summary)
    write(root/'ranking.json', [{'id': r['id'], 'score': r['score'], 'policy': r['policy']} for r in results])
    report = ['# MNQ robustness audit', '', f"Audited {len(results)} unique existing policies across six training scenarios.",
              f"Best worst-case daily robustness score: ${selected['score']:.2f}.",
              f"Training gate passed: {summary['training_gate_passed']}. Deployment DISABLED.", '',
              'Selection frozen before validation. Prior best runs remain untouched.', '',
              '| Policy | Cost | Mode | Mean 30-day P&L | Passes/windows |',
              '|---|---|---|---:|---:|']
    for i, comparison in enumerate(comparisons):
        for r in comparison['results']:
            report.append(f"| {i}: OR {comparison['policy']['opening_minutes']}m | {r['cost']} | {r['mode']} | ${r['mean_pnl']:.2f} | {r['passed']}/{r['windows']} |")
    report += ['', 'Policies: ```'+json.dumps([x['policy'] for x in comparisons])+'```', '',
               *['- '+x for x in definition['limitations']], '',
               'Sources: https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html',
               'https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf']
    (root/'REPORT.md').write_text('\n'.join(report)+'\n')
    print(json.dumps({'policies': len(results), 'score': selected['score'], 'policy': selected['policy']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--campaign', type=Path, action='append', required=True)
    args = parser.parse_args()
    run(args.root, args.source, args.campaign)
