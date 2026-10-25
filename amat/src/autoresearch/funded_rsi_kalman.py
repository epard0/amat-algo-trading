"""Controlled RSI14 ablations with causal Kalman trend gates."""
import argparse
from pathlib import Path

from autoresearch.funded import digest
from autoresearch.funded_indicator_study import run


def candidates():
    base = {'atr_multiplier': 1.5, 'risk': 100, 'entry_cutoff_minute': 180}
    policies = [{**base, 'strategy': 'rsi_reversal', 'research_group': 'plain_rsi', 'take_profit_r': target}
                for target in (1., 2.)]
    for mode in ('aligned', 'countertrend', 'calm', 'no_strong_opposition'):
        for threshold in (.05, .15):
            for variance in (.001, .01):
                for target in (1., 2.):
                    policies.append({**base, 'strategy': 'rsi_kalman', 'research_group': mode,
                                     'take_profit_r': target, 'kalman_gate': mode,
                                     'kalman_threshold': threshold, 'kalman_process_variance': variance})
    return policies


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.source, candidates(), 'MNQ RSI + Kalman controlled study', 'research_group',
        {'controller_sha256': digest(Path(__file__)),
         'hypothesis': 'RSI reversal quality depends on causal local trend; compare four gates with plain RSI',
         'kalman': 'Local-linear price model, observation variance4, slope process variance .001/.01; forward filter only',
         'normalization': 'Filtered slope divided by current causal ATR14; thresholds .05/.15',
         'gates': {'aligned': 'direction*slope/ATR >= threshold',
                   'countertrend': 'direction*slope/ATR <= -threshold',
                   'calm': 'abs(slope/ATR) <= threshold',
                   'no_strong_opposition': 'direction*slope/ATR >= -threshold'},
         'controls': 'Fixed RSI14 30/70 recross, ATR1.5 stop, risk100, target1R/2R; no change to costs, sizing or activity gate'})
