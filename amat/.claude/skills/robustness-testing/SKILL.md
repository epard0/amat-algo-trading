---
name: robustness-testing
description: Apply AMAT cost, delay, missed-fill, parameter-neighborhood, concentration, ablation, regime, and block-bootstrap stress tests as falsification rather than optimization.
user-invocable: false
---

Run stresses against a frozen canonical configuration:

- 0/baseline/2x/3x/5x costs;
- one/two-session delays and alternative next-open/next-close fills;
- deterministic 5%/10% missed fills and missing execution bars;
- nearby horizons, deadbands, covariance windows, volatility targets/floors, and rebalance thresholds;
- removal of top trades, best symbol, and best year;
- signal magnitude ablation, constant-positive signal, covariance/deadband/rebalance ablations;
- regime slices and block-bootstrap uncertainty.

Look for broad stability and breakpoints. Do not choose the best stress outcome as a new canonical parameter without registering a new research cycle and preserving the original failure.

## Implemented so far (2026-08-13)

- Regime slices: `amat walkforward` (causal SPY trend/vol labels,
  `src/amat/validation/regimes.py`).
- Block-bootstrap uncertainty: `amat montecarlo` — stationary joint-row
  bootstrap, per-metric percentiles, exceedance probabilities, 3-page PDF
  (`src/amat/validation/montecarlo.py`). Conservative for trend (blocks
  break long memory); read probabilities as within-resampling frequencies.
- Volatility-target neighbourhood: `amat sweep-target-vol` (experiment 0001).
- Cost ladder: synthetic only so far (`amat backtest-synthetic --cost-ladder`).

Everything else on the list above is still unimplemented on real data and is
tracked in the registry's "Not yet run".
