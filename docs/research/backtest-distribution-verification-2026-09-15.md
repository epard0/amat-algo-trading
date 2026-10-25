# Backtesting distribution and scenario verification

Verified on 2026-09-15 UTC (September 14 in America/Detroit).

## Implemented coverage

The extended research suite now has 17 synthetic scenarios, six historical
execution stresses, and two joint bootstrap paths by default: **25 paired cases,
50 candidate/baseline accounting runs**. Each synthetic recipe supports 1–8
independently seeded repetitions.

The suite covers Gaussian, Student's t (df 3 and 5), Laplace, negatively skewed
factor shocks, and Gaussian variance mixtures; causal GJR-GARCH volatility;
bull/bear, range, whipsaw, crash/recovery, and correlation-break markets; upward
and downward gaps; flat markets; and combined liquidity, missed-fill, delay, and
cost stresses. Existing walk-forward, holdout, stress, Monte Carlo, and separate
Pareto/Markov tests remain part of the full repository suite.

New diagnostics record empirical 95%/99% VaR and expected shortfall, shape
moments, quantiles, and squared-return autocorrelation. Incomplete scheduled-event
coverage fails integrity. Bootstrap execution prices now share the total-return
basis across corporate actions, preventing an extra split/dividend gap.

See [assumptions and usage](backtest-market-conditions.md) for the complete
scenario table, probability conventions, seed controls, and limitations.

## Automated checks

| Check | Result |
|---|---|
| Full `pytest -q` suite | **1,975 passed, 1 skipped**, 360.17 seconds |
| Focused checks after the final export/review integration | **135 passed**, 113.86 seconds |
| `ruff check .` | Passed |
| `mypy src` | Passed, 160 source files |
| Formatting of all 15 changed Python files | Passed |
| `git diff --check` | Passed |
| Repository-wide formatting | One pre-existing string-wrap issue in `scripts/analyze_session_patterns.py:69`; that file was not modified |

The existing skip is the package `__init__.py` outside the declared dependency
layers. The full test run preceded the final changes exposing already-computed
metrics in CSV exports and model-review inputs; the focused run covers those
changes and the complete scenario suite again.

New tests verify statistical mean/variance/correlation, tail frequencies, skew,
volatility persistence, negative-shock response, deterministic prefixes, future
input isolation, gap and halt mechanics, short-window refusal, strict JSON,
fractional/tied tail mass, repeated-seed pairing, accounting identities, and
corporate-action normalization. Statistical tests use fixed seeds and up to
200,000 draws, with tolerances on the properties being tested.

## Frozen historical-data verification

The verification used `alpaca-sip-core-2026-09-14-verified`, restricted to the
11-asset v6 universe. The source panel contained 1,762 sessions from 2016-01-04
through **2022-12-30**. Measured performance began **2018-01-02**. The suite paired
the frozen v6 baseline with a built-in router proposal and recomputed every world.

- 25 expected cases, 25 completed; no incomplete scenario events.
- 50 complete accounting records saved, including daily fills and equity.
- No candidate or baseline accounting warnings; integrity passed.
- Runtime: 135.81 seconds.
- The seven current mathematical/configuration source files match the hashes
  recorded in the frozen program used for this verification.
- Frozen program SHA-256:
  `88f221e23067bd09083d152f46738cff0d1db9ba5551abf08c86d4c5668f44cb`.

Passing these checks establishes execution and coverage integrity. The strategy
still incurs substantial losses in some declared hypothetical worlds:

| Hypothetical case | Router max drawdown | Baseline max drawdown |
|---|---:|---:|
| Range | -39.94% | -55.07% |
| Liquidity crisis | -30.26% | -35.10% |
| Correlation break | -27.10% | -14.39% |
| Crash/recovery | -24.23% | -21.62% |
| Gap down | -22.52% | -19.46% |

These are conditional outcomes under the written processes. Counts and losses
across this suite are diagnostics, not estimated probabilities of future success.

## Saved evidence

- [Full paired report](../../amat/outputs/validation/market-distributions-2026-09-15/results/robustness.json)
- [50-row scenario comparison CSV](../../amat/outputs/validation/market-distributions-2026-09-15/scenario-comparison.csv)
- [Verification metadata](../../amat/outputs/validation/market-distributions-2026-09-15/results/verification.json)
- [Frozen program and source hashes](../../amat/outputs/validation/market-distributions-2026-09-15/program.json)
- [Replay script](../../amat/outputs/validation/market-distributions-2026-09-15/verify.py)

To replay, set `AMAT_SCENARIO_AUDIT_OUT` to a new output directory and execute
the saved script with this project's Python environment. It freezes a separate
workspace under the system temporary directory and uses the cached snapshot.
The original run's frozen source and snapshot remain at
`/tmp/amat-market-validation-firgc81f/frozen` for inspection.
