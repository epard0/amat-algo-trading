"""Paired, diagnostic-only follow-up of frozen RSI/Kalman selections."""
import argparse
import json
from pathlib import Path

from autoresearch.funded import digest, evaluate, load_sessions, write
from autoresearch.funded_robustness import COSTS, daily, stability
from autoresearch.funded_state import record_study


def paired(filtered, plain):
    delta = []
    for a, b in zip(filtered, plain, strict=True):
        if a['date'] != b['date']:
            raise ValueError('Unaligned replay dates')
        delta.append({'date': a['date'], 'pnl': a['pnl']-b['pnl'],
                      'contracts': int(a['contracts'] > 0 or b['contracts'] > 0)})
    return {'daily_differences': delta, 'difference_diagnostics': stability(delta),
            'filtered_full_period_pnl': sum(x['pnl'] for x in filtered),
            'plain_full_period_pnl': sum(x['pnl'] for x in plain),
            'filtered_pnl_without_largest_winning_day': sum(x['pnl'] for x in filtered)-max(0., max(x['pnl'] for x in filtered)),
            'filtered_entries': sum(x['contracts'] > 0 for x in filtered),
            'plain_entries': sum(x['contracts'] > 0 for x in plain)}


def run(root, source):
    if (root/'paired-diagnostics.json').exists():
        raise ValueError('Diagnostics already frozen')
    selection = json.loads((root/'selection.json').read_text())
    manifest = json.loads((source/'data-manifest.json').read_text())
    path = Path(manifest['path'])
    if digest(path) != manifest['sha256']:
        raise ValueError('Data changed')
    sessions, _ = load_sessions(path, manifest['timezone'])
    validation = [x for x in sessions if x[0] >= '2026-03-02']
    protocol = {'selection_sha256': digest(root/'selection.json'), 'data_sha256': manifest['sha256'],
                'diagnostic_controller_sha256': digest(Path(__file__)),
                'engine_sha256': digest(Path(__file__).with_name('funded.py')),
                'signals_sha256': digest(Path(__file__).with_name('funded_signals.py')),
                'comparisons': 'Selected filters vs identical target/risk/stop plain RSI; three cost scenarios and 1/2 minute delays',
                'bootstrap': 'Paired five-session moving-block bootstrap, descriptive lower-tail mean, not selection-corrected',
                'deployment': 'DISABLED', 'selection_updates_permitted': False}
    write(root/'paired-protocol.json', protocol)
    results = []
    for row in selection:
        candidate = row['candidate']
        if candidate['research_group'] == 'plain_rsi':
            continue
        plain = {k: v for k, v in candidate.items() if not k.startswith('kalman_')}
        plain.update(strategy='rsi_reversal', research_group='matched_plain_rsi')
        for name, (commission, ticks) in COSTS.items():
            cost = {'commission_side': commission, 'slip_ticks': ticks}
            for delay in (1, 2):
                a, b = daily(validation, candidate, cost, delay), daily(validation, plain, cost, delay)
                accounts = [{'mode': mode, **evaluate(validation, candidate, cost, mode, delay=delay)}
                            for mode in ('eod', 'intraday')]
                results.append({'candidate': candidate, 'plain_control': plain, 'cost': name, 'delay': delay,
                                **paired(a, b), 'account_results': accounts})
    write(root/'paired-diagnostics.json', {'results': results, 'selection_changed': False,
                                         'limitations': 'Previously researched short sample; no fresh observations; no significance claim'})
    record_study(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.source)
