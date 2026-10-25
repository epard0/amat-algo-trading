"""Small preregistered indicator-family comparison using shared funded replay."""
import argparse
import json
from pathlib import Path

from autoresearch.funded import digest, evaluate, load_sessions, write
from autoresearch.funded_robustness import COSTS, activity_eligible, daily, stability
from autoresearch.funded_signals import FAMILIES
from autoresearch.funded_state import record_study


def run(root, source, candidates=None, title='MNQ indicator strategy study', group_field='strategy', context=None):
    if root.exists():
        raise ValueError('Use a fresh study directory')
    manifest = json.loads((source/'data-manifest.json').read_text())
    path = Path(manifest['path'])
    if digest(path) != manifest['sha256']:
        raise ValueError('Data changed')
    sessions, audit = load_sessions(path, manifest['timezone'])
    train = [x for x in sessions if x[0] < '2026-03-02']
    validation = [x for x in sessions if x[0] >= '2026-03-02']
    candidates = candidates if candidates is not None else [{'strategy': family, 'atr_multiplier': atr, 'take_profit_r': target,
                   'risk': 100, 'entry_cutoff_minute': 180}
                  for family in FAMILIES for atr in (1.5, 2.5) for target in (1., 2.)]
    definition = {'candidates': candidates, 'data_sha256': manifest['sha256'],
                  'engine_sha256': digest(Path(__file__).with_name('funded.py')),
                  'signals_sha256': digest(Path(__file__).with_name('funded_signals.py')),
                  'study_sha256': digest(Path(__file__)), 'costs': COSTS,
                  'selection': 'best per family by minimum chronological block mean/bootstrap 5th percentile across 3 costs and 2 entry delays; at least 10 training entries each scenario',
                  'warmup_minutes': 40, 'max_contracts': 4, 'one_entry_per_day': True,
                  'stop': 'signal-time ATR multiple, tick-rounded away from entry',
                  'exit': 'fixed R target or protective stop; flat at 15:59; stops first on ambiguous bar',
                  'split_cutoff': '2026-03-02', 'deployment': 'DISABLED',
                  'reference': 'https://github.com/pietrobelli26/sharpe_edge_code',
                  'reference_commit': 'afda167339d296c1c9fc89ea157fe5f70862c339',
                  'implementation': 'Independent implementation; Kalman and Bollinger research inspired by reference. Upstream notebooks inspected as text, never executed.',
                  'limitations': ['Short previously researched data, not a pristine holdout',
                                  'Minute OHLC cannot resolve intrabar ordering; costs assumed',
                                  'Unverified roll identity; bootstrap descriptive, multiplicity uncorrected',
                                  'Intraday reset and one entry/day are deliberately constrained first-pass implementations'],
                  'model_reviews': 0, 'model_reason': 'Previously confirmed zero Gemini generation quota',
                  'study_context': context, 'group_field': group_field}
    write(root/'protocol.json', definition)
    write(root/'data-audit.json', audit)
    trials = []
    for i, candidate in enumerate(candidates, 1):
        rows = []
        for name, (commission, ticks) in COSTS.items():
            for delay in (1, 2):
                cost = {'commission_side': commission, 'slip_ticks': ticks}
                rows.append({'cost': name, 'delay': delay, **stability(daily(train, candidate, cost, delay))})
        row = {'candidate': candidate, 'activity_eligible': activity_eligible(rows),
               'score': min(min(r['worst_block_mean'], r['bootstrap_mean_pnl_p05']) for r in rows),
               'scenarios': rows}
        trials.append(row)
        write(root/'trials'/f'{i:02d}.json', row)
        print(f'Training {i}/{len(candidates)} {candidate["strategy"]}: {row["score"]:.2f}', flush=True)
    selected = []
    groups = tuple(dict.fromkeys(x[group_field] for x in candidates))
    for family in groups:
        eligible = [x for x in trials if x['candidate'][group_field] == family and x['activity_eligible']]
        if eligible:
            selected.append(max(eligible, key=lambda x: x['score']))
    write(root/'selection.json', selected)  # Frozen before validation.
    comparisons = []
    for row in selected:
        results = []
        for name, (commission, ticks) in COSTS.items():
            cost = {'commission_side': commission, 'slip_ticks': ticks}
            diag = stability(daily(validation, row['candidate'], cost))
            for mode in ('eod', 'intraday'):
                results.append({'cost': name, 'mode': mode, 'daily_diagnostics': diag,
                                **evaluate(validation, row['candidate'], cost, mode)})
        comparisons.append({**row, 'validation': results})
    summary = {'trials': len(trials), 'training_scenario_replays': len(trials)*6,
               'activity_eligible': sum(x['activity_eligible'] for x in trials),
               'families_selected': len(selected), 'comparisons': comparisons,
               'deployment': 'DISABLED', 'limitations': definition['limitations']}
    write(root/'summary.json', summary)
    lines = ['# '+title, '',
             f'{len(candidates)} predefined configurations; {len(candidates)*6} training scenario replays. Group choices frozen before validation.', '',
             '| Group | Training robustness $/day | Base 30-day P&L | Adverse | Stress | Full-period adverse P&L | Adverse passes |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for row in comparisons:
        r = row['validation']
        lines.append(f"| {row['candidate'][group_field]} | {row['score']:.2f} | {r[0]['mean_pnl']:.2f} | {r[2]['mean_pnl']:.2f} | {r[4]['mean_pnl']:.2f} | {r[2]['daily_diagnostics']['total_pnl']:.2f} | {r[2]['passed']}/{r[2]['windows']} |")
    missing = sorted(set(groups)-{x['candidate'][group_field] for x in selected})
    lines += ['', f'Families failing minimum training activity: {missing}.', '',
              'Displayed account metrics use EOD drawdown; intraday results are saved in summary.json.', '',
              *['- '+x for x in definition['limitations']], '',
              'Deployment disabled. Gemini not invoked because of previously confirmed zero quota.', '',
              'Reference: https://github.com/pietrobelli26/sharpe_edge_code/tree/afda167339d296c1c9fc89ea157fe5f70862c339']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')
    record_study(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.source)
