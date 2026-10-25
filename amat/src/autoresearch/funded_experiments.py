"""Controlled eight-experiment RSI/Kalman research; no live trading."""
import argparse
import json
from pathlib import Path

import numpy as np

from autoresearch.funded import digest, evaluate, load_sessions, trade_day, write
from autoresearch.funded_external import load as load_external
from autoresearch.funded_robustness import COSTS, activity_eligible, stability
from autoresearch.funded_state import record_study


def candidates():
    plain = {'strategy': 'rsi_reversal', 'atr_multiplier': 1.5, 'take_profit_r': 1.,
             'risk': 100, 'entry_cutoff_minute': 180}
    filtered = {**plain, 'strategy': 'rsi_kalman', 'kalman_gate': 'countertrend',
                'kalman_threshold': .15, 'kalman_process_variance': .001}
    result = [('plain', 0, plain), ('countertrend', 0, filtered),
              ('plain_one_contract', 3, {**plain, 'fixed_contracts': 1}),
              ('countertrend_one_contract', 3, {**filtered, 'fixed_contracts': 1})]
    for threshold in (2., 4.):
        result.append((f'confidence_{threshold:g}', 4, {**filtered, 'kalman_min_tstat': threshold}))
    result.append(('deceleration_two_bars', 5, {**filtered, 'deceleration': True}))
    for name, policy in [('plain', plain), ('countertrend', filtered)]:
        for minutes in (30, 60):
            result.append((f'{name}_hold_{minutes}', 6,
                           {**policy, 'take_profit_r': 0., 'max_holding_minutes': minutes}))
        result.append((f'{name}_rsi50', 6, {**policy, 'take_profit_r': 0., 'exit_rule': 'rsi_midpoint'}))
    return [{'id': name, 'experiment': experiment, 'policy': policy} for name, experiment, policy in result]


def replay(sessions, policy, cost, delay):
    rows = []
    for day, bars in sessions:
        if bars is None:
            continue
        day_cost = cost
        if 'quotes_by_day' in cost:
            if day not in cost['quotes_by_day']:
                continue
            day_cost = {**cost, 'quotes': cost['quotes_by_day'][day]}
        rows.append({'date': day, **trade_day(bars, policy, day_cost, 50000., 48000., 'eod', delay=delay)})
    return rows


def max_statistic(matrix, resamples=2000):
    """Shared five-day block bootstrap under centered null, max t over all recorded policies.

    Approximate research diagnostic, not a proof under nonstationarity or adaptive search.
    """
    values = np.asarray(matrix, dtype=float)
    n, trials = values.shape
    if n < 15 or trials < 1 or not np.isfinite(values).all():
        raise ValueError('Insufficient or invalid multiple-testing matrix')
    standard_error = values.std(axis=0, ddof=1)/np.sqrt(n)
    valid = standard_error > 1e-12
    means = values.mean(axis=0)
    t = np.divide(means, standard_error, out=np.zeros(trials), where=valid)
    centered = values-means
    rng = np.random.default_rng(20261014)
    maxima = []
    for offset in range(0, resamples, 100):
        batch = min(100, resamples-offset)
        starts = rng.integers(0, n-4, (batch, (n+4)//5))
        indices = (starts[:, :, None]+np.arange(5)).reshape(batch, -1)[:, :n]
        simulated = centered[indices].mean(axis=1)
        normalized = np.divide(simulated, standard_error, out=np.zeros_like(simulated), where=valid)
        maxima.extend(normalized.max(axis=1))
    maxima = np.array(maxima)
    return [{'mean': float(means[i]), 't': float(t[i]),
             'maxT_adjusted_p': float((1+(maxima >= t[i]).sum())/(resamples+1)) if valid[i] else 1.}
            for i in range(trials)]


def policy_key(policy):
    return json.dumps({k: v for k, v in policy.items() if k != 'research_group'}, sort_keys=True)


def history(outputs):
    policies, inputs = {}, {}
    for file in sorted(outputs.glob('mnq-*/batch-*/results.json')):
        for row in json.loads(file.read_text()).get('measured', []):
            policy = row['policy']
            policies[policy_key(policy)] = policy
        inputs[str(file.resolve())] = digest(file)
    for file in sorted(outputs.glob('mnq-*/trials/*.json')):
        row = json.loads(file.read_text())
        policy = row.get('policy', row.get('candidate'))
        if policy is not None:
            policies[policy_key(policy)] = policy
            inputs[str(file.resolve())] = digest(file)
    return policies, inputs


def subgroup(rows):
    result = {}
    for name, predicate in [('long', lambda r: r.get('direction') == 1),
                            ('short', lambda r: r.get('direction') == -1),
                            ('before_11', lambda r: r.get('signal_minute', 10000) < 90),
                            ('11_to_1230', lambda r: 90 <= r.get('signal_minute', 10000) < 180)]:
        trades = [r for r in rows if r['contracts'] > 0 and predicate(r)]
        result[name] = {'entries': len(trades), 'total_pnl': sum(r['pnl'] for r in trades),
                        'mean_trade_pnl': float(np.mean([r['pnl'] for r in trades])) if trades else None,
                        'enough_observations': len(trades) >= 20}
    return result


def run(root, source, external):
    if root.exists():
        raise ValueError('Use a fresh output directory')
    manifest = json.loads((source/'data-manifest.json').read_text())
    if digest(Path(manifest['path'])) != manifest['sha256']:
        raise ValueError('Original data hash changed')
    sessions, audit = load_sessions(Path(manifest['path']), manifest['timezone'])
    train = [x for x in sessions if x[0] < '2026-03-02']
    validation = [x for x in sessions if x[0] >= '2026-03-02']
    grid = candidates()
    past, inputs = history(root.parent)
    for row in grid:
        past[policy_key(row['policy'])] = row['policy']
    keys = list(past)
    protocol = {'experiments': {
        '1': 'Previously unused contract-labelled MNQ June2025 observations, frozen rules; public provenance unverified',
        '2': 'Bid/Ask minute OHLC side-price replay; event-level synchronized bid/ask unavailable, explicitly partial',
        '3': 'Fixed one-contract cap vs risk sizing, same risk ceiling',
        '4': 'Kalman model-standardized slope thresholds2/4; not calibrated confidence probabilities',
        '5': 'Two completed bars of opposing-slope deceleration after RSI recross, wait at most10 minutes',
        '6': '30/60-minute holding limits and RSI50 exit, with unchanged protective stop',
        '7': 'Post-trade long/short and before/after11ET attribution; no subgroup restriction selected',
        '8': 'Record historical policies and shared five-day centered max-statistic bootstrap'},
        'candidates': grid, 'historical_policy_count': len(past), 'historical_inputs': inputs,
        'data_sha256': manifest['sha256'], 'external_manifest_sha256': digest(external/'manifest.json'),
        'code_sha256': {name: digest(Path(__file__).with_name(name)) for name in
                       ('funded.py', 'funded_signals.py', 'funded_experiments.py', 'funded_external.py')},
        'selection': 'Original training only: at least10 entries each scenario, positive worst block/bootstrap mean and maxT-adjusted p<=.05',
        'scenarios': 'Base/adverse/stress costs × 1/2 minute delays. Quotes add same adverse residual slippage on observed side-price.',
        'validation': 'Original validation previously inspected. External2025 newly used exploratory; one day inspected for timestamp audit.',
        'timestamp_sensitivity': 'Primary naive NY minute-end shifted-1; alternative NY minute-start shift0. Both unverified.',
        'model_reviews': 0, 'model_reason': 'Previously confirmed zero Gemini generation quota', 'deployment': 'DISABLED'}
    write(root/'protocol.json', protocol)
    write(root/'data-audit.json', audit)
    matrix = []
    adverse = {'commission_side': 1., 'slip_ticks': 2}
    for index, key in enumerate(keys, 1):
        matrix.append([r['pnl'] for r in replay(train, past[key], adverse, 1)])
        if index % 50 == 0:
            print(f'Historical policies replayed {index}/{len(keys)}', flush=True)
    adjusted = dict(zip(keys, max_statistic(np.array(matrix).T), strict=True))
    write(root/'multiple-testing.json', {'policies': [{'policy': past[k], **adjusted[k]} for k in keys],
            'scope': 'Observed archived policies only; earlier unrecorded attempts not covered; approximate stationary centered-block maxT, not formal DSR',
            'resamples': 2000, 'block_length': 5, 'seed': 20261014})
    training = []
    for row in grid:
        scenarios = []
        for name, (commission, ticks) in COSTS.items():
            for delay in (1, 2):
                cost = {'commission_side': commission, 'slip_ticks': ticks}
                scenarios.append({'cost': name, 'delay': delay, **stability(replay(train, row['policy'], cost, delay))})
        score = min(min(x['worst_block_mean'], x['bootstrap_mean_pnl_p05']) for x in scenarios)
        testing = adjusted[policy_key(row['policy'])]
        training.append({**row, 'scenarios': scenarios, 'score': score,
                         'activity_eligible': activity_eligible(scenarios), 'multiplicity': testing,
                         'promotion_gate_passed': activity_eligible(scenarios) and score > 0 and testing['maxT_adjusted_p'] <= .05})
        print(f'Training {row["id"]}: {score:.2f}', flush=True)
    eligible = [x for x in training if x['activity_eligible']]
    selected = max(eligible, key=lambda x: x['score']) if eligible else None
    write(root/'selection.json', {'training_selected': selected, 'training': training,
                                  'promotion_allowed': bool(selected and selected['promotion_gate_passed'])})
    fresh, quotes = load_external(external)
    shifted, shifted_quotes = load_external(external, 0)
    datasets = [('prior_validation', validation, None), ('external_last', fresh, None), ('external_quotes', fresh, quotes)]
    rows = []
    for row in grid:
        measurements = []
        for label, sample, book in datasets:
            for name, (commission, ticks) in COSTS.items():
                for delay in (1, 2):
                    cost = {'commission_side': commission, 'slip_ticks': ticks}
                    if book is not None:
                        cost['quotes_by_day'] = book
                    daily_rows = replay(sample, row['policy'], cost, delay)
                    measurement = {'dataset': label, 'cost': name, 'delay': delay,
                                   'daily': daily_rows, **stability(daily_rows)}
                    # Account simulation for controls and the training-selected policy only.
                    if row['id'] in ('plain', 'countertrend', selected['id'] if selected else ''):
                        measurement['accounts'] = [{'mode': mode, **evaluate(sample, row['policy'], cost, mode, delay=delay)}
                                                   for mode in ('eod', 'intraday')]
                    if row['id'] in ('plain', 'countertrend'):
                        measurement['subgroups'] = subgroup(daily_rows)
                    measurements.append(measurement)
        for label, book in [('external_last_timestamp0', None), ('external_quotes_timestamp0', shifted_quotes)]:
            cost = {**adverse, **({'quotes_by_day': book} if book is not None else {})}
            daily_rows = replay(shifted, row['policy'], cost, 1)
            measurements.append({'dataset': label, 'cost': 'adverse', 'delay': 1,
                                 'daily': daily_rows, **stability(daily_rows)})
        result = {**row, 'measurements': measurements}
        rows.append(result)
        write(root/'trials'/f'{row["id"]}.json', result)
        print(f'Evaluated {row["id"]}', flush=True)
    summary = {'experiments': protocol['experiments'], 'policies': len(grid), 'training_scenario_replays': len(grid)*6,
               'diagnostic_replays': sum(len(x['measurements']) for x in rows),
               'historical_policies_replayed': len(keys), 'training_selected': selected,
               'promotion_gate_passes': sum(x['promotion_gate_passed'] for x in training),
               'external_common_sessions': len(quotes), 'results': rows, 'deployment': 'DISABLED',
               'event_level_bid_ask_test': 'BLOCKED_NO_SYNCHRONIZED_TICK_QUOTES; minute quote approximation executed',
               'fresh_verified_data_test': 'PARTIAL: new public contract-labelled observations; timezone and provenance unverified'}
    write(root/'summary.json', summary)
    report = ['# Eight controlled MNQ research experiments', '',
              f'{len(grid)} variants; {len(grid)*6} training scenario replays; {summary["diagnostic_replays"]} diagnostic replays.',
              f'{len(keys)} archived/current policies included in multiplicity diagnostic.',
              f'{len(quotes)} complete external sessions. Promotion gate passes: {summary["promotion_gate_passes"]}.', '',
              '| Variant | Prior adverse P&L | External adverse Last | External adverse quote proxy |',
              '|---|---:|---:|---:|']
    for row in rows:
        values = [next(x for x in row['measurements'] if x['dataset'] == label and x['cost'] == 'adverse' and x['delay'] == 1)['total_pnl']
                  for label in ('prior_validation', 'external_last', 'external_quotes')]
        report.append('| '+row['id']+' | '+' | '.join(f'${v:.2f}' for v in values)+' |')
    report += ['', 'P&L above covers each full period, not overlapping account windows. External2025 was not used for selection.', '',
               'Experiments1/2 are PARTIAL: public contract labels and timestamp assumptions are unverified; Bid/Ask minute OHLC is not synchronized tick data. No actual tick replay or verified-data claim.', '',
               'Original validation is previously inspected. Multiplicity diagnostic covers observed archives only; approximate block-bootstrap assumptions do not establish significance after all human/adaptive search.', '',
               'Sources: https://github.com/mbytes21/MNQ_DATA/tree/fc5508e2c152938d6d9eb70a36b888ae26107176 ; https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf']
    (root/'REPORT.md').write_text('\n'.join(report)+'\n')
    record_study(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--external', type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.source, args.external)
