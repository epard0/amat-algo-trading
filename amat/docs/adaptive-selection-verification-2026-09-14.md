# Adaptive ML and asset selection — verification, 2026-09-14

The research capability is implemented and execution checks passed. Neither ML
candidate passed the development performance gates, so the deployed strategy
configuration was not changed and no broker order was submitted.

The main improvement discovered during this iteration is to use liquidity and
ranking to approve **new purchases and increases**, while leaving existing
holdings subject to their normal signal and risk exits. Treating every loss of
relative-volume eligibility as a forced sale created unnecessary turnover.

## Historical comparison

All measurements below use January 2, 2018 through December 30, 2022: 1,259
session observations, the same eleven-ETF universe, the same corrected data
snapshot, 2 basis points of cost per side, and the same fixed 1% limit on buys
relative to the actual execution-window share volume. This is adaptive
development evidence. The existing universe has hindsight-selection limitations,
and these results are not an untouched holdout or measured paper performance.

| Recipe | Annualized return | Sharpe | Maximum drawdown | Average gross exposure | Fills |
|---|---:|---:|---:|---:|---:|
| Unchanged v6 baseline under the new execution protocol | 3.91% | 0.745 | -7.94% | 50.43% | 1,487 |
| ML signal without asset selection | 2.29% | 0.322 | -14.41% | 77.25% | 396 |
| ML with selection forcing exits | -1.81% | -0.255 | -18.23% | 44.66% | 1,995 |
| ML with selection governing additions | 1.90% | 0.268 | -15.45% | 73.16% | 349 |

The correction reduced fills by **82.5%** compared with forced-exit selection.
Modeled annual cost drag fell from **0.751% to 0.070%**. The raw model predictions
were exactly identical between attempts. Replaying the forced-exit mode as a
control in the second run reproduced every original daily record exactly.

The initial selector removed an asset from membership 773 times. Relative volume
caused 577 of those removals, or 74.6%. The correction changed only how selection
controls capital; it did not retune any feature, threshold, model setting, date or
execution limit after seeing the result.

The correction remains inferior to the existing baseline. At retrospectively
matched exposure, its Sharpe was **0.295 versus 0.745** for the baseline. Against
the same ML signal without selection, the matched Sharpe difference was
**-0.035**, and the trial-adjusted bootstrap lower bound was **-0.184**. The
comparison used 1,000 resamples, 60-session blocks and the declared 50-trial
budget. These are research safeguards, not calibrated significance claims.

At 6 basis points per side, the corrected candidate returned **1.79% annually**
with Sharpe **0.252**. It passed nearby-parameter and delay checks, but failed:

- `positive_fold_fraction`
- `no_incremental_exposure_matched_information`
- `selection_has_no_incremental_value`

Both runs also completed ten historical-execution or hypothetical scenarios per
recipe: higher costs, missed fills, bull, bear, range, whipsaw, crash/recovery,
correlation change and a bootstrap path. Scenario integrity passed. Hypothetical
scenario profitability did not contribute to the promotion score. No checkpoint
performance after December 2022 was consumed to rescue the rejected candidate.

## What was implemented

- Optional `signal.selection`, with explicit `entries` and `portfolio` modes.
- Current and trailing dollar-turnover floors, a relative-volume band, and an
  execution-window turnover floor, all available at the decision close.
- Deterministic ranking, a preference for prior selection, a limit on approved
  assets and a limit within each existing cluster.
- A valid high/low/close candle-location feature, including missing/malformed
  candle handling. No unavailable opening price was inferred.
- A five-session ridge example using normalized momentum, close location,
  signed volume surprise, volatility shock and two interactions.
- Selection traces and reasons, selection-mode controls, removal controls,
  nearby-setting controls and mandatory incremental-value checks.
- A fixed 1% execution-window participation limit for buys in every new version
  7 research run, including all baselines and controls.
- Explicit persistence of control-run warnings so rejection reasons can be
  traced to the actual session and event.

See the [implementation and reproduction guide](adaptive-asset-selection.md)
and the [example recipe](../configs/autoresearch/adaptive-ml.example.json).

## Execution and engineering verification

The corrected candidate produced **169 simulated buy fills and 180 sell fills**.
Every buy was authorized by the preceding session's selected set, every fill
used its recorded execution session, and no buy exceeded **1%** of observed
execution-window volume. The simulator recorded **30 participation-cap misses or
unfilled remainders** and **13 insufficient-cash refusals**. Cash remained
nonnegative. The maximum accounting-identity residual was below **$0.000001**.
The primary and 6-basis-point cost-stress runs both passed the audit, as did the
baseline and the initial forced-exit candidate.

Tests demonstrate that entry eligibility can disappear while a holding remains,
then a later signal exit can still sell it. They also verify unavailable volume,
zero floors, deterministic ties, cluster limits, retention behavior, the shared
warm-up budget, purged label availability, future-data mutation invariance and
prefix invariance of predictions and selection.

- **1,834 AMAT tests passed; one expected architecture-test skip.**
- **160 executor tests passed**, including real offline v6 plan generation and
  plan/submission/report integration against a fake broker.
- **89 targeted selection, signal and router checks passed** before the final
  full suite; these are included in, not additional to, the full count.
- Strict type checking passed for all 156 AMAT source files and 14 executor
  source files; both projects passed lint.
- All changed Python files passed formatting. The repository-wide AMAT format
  check still flags `scripts/analyze_session_patterns.py`, whose bytes were
  verified identical to `HEAD`. That unrelated file was left intact.
- `git diff --check` passed.

The initial run's `nearby_0.8` control reported an informational USO reverse-split
resizing event on April 29, 2020. The existing conservative warning gate rejected
that control. It was not an unexplained loss or accounting mismatch. The final
entry-mode run's controls reported no runner warnings.

The audit is historical simulated execution. The executor tests exercise the
existing v6 plan contract, not a new ML-to-broker adapter. There were **zero broker
submissions and zero model-provider calls** during these studies.

## Preserved evidence

The [machine-readable comparison](../outputs/adaptive-selection-2026-09-14/comparison.json)
and both complete frozen runs are saved in
`amat/outputs/adaptive-selection-2026-09-14/`. This generated evidence is ignored
by Git; source changes and this report remain separate from data artifacts.

| Evidence | Artifact |
|---|---|
| Corrected candidate report | [Entry-mode report](../outputs/adaptive-selection-2026-09-14/entries/report.html) |
| Original attempt report | [Portfolio-mode report](../outputs/adaptive-selection-2026-09-14/portfolio/report.html) |
| Final execution audit | [Entry-mode audit](../outputs/adaptive-selection-2026-09-14/entries/execution-audit.json) |
| First execution audit | [Portfolio-mode audit](../outputs/adaptive-selection-2026-09-14/portfolio/execution-audit.json) |
| Validation logs | [Full research tests](../outputs/adaptive-selection-2026-09-14/checks/final-tests.log), [executor tests](../outputs/adaptive-selection-2026-09-14/checks/executor-tests.log) |

The final program SHA-256 is
`54fd210ebd525b7e349f03c84dfb9e36a2ed6a3e1a8f99d491605a0fb012a5b7`.
The final candidate SHA-256 is
`b676a6f933017a78c83233cc47230fdb226b7a71f72a0d16bc37ee596b820d54`.
The first program SHA-256 is
`c81f1a0446dbf8642f9004675ca701e4cf53091cdda2dd5837cc997589df914d`.
Each folder contains its original snapshot, frozen source, model fits, controls,
manifests, prediction arrays, decision reasons, daily fills and a relocated
`research.sh` launcher. Existing frozen sessions elsewhere were not edited.

## Recommended next experiment

Keep the current deployed configuration. Use the new entry-selection machinery
to study the **incremental quality of the baseline's proposed trades**: learn
when a baseline entry adds value after costs, conditional on regime and asset
structure. The current generic forward-return model has not earned a replacement
role in portfolio construction.

Improve the evidence with historical bid/ask spreads and size-dependent costs,
split-aware volume normalization, point-in-time universe membership and measured
correlation between selected assets. The daily panel needs opening prices before
full candle-body patterns can be tested. A paper deployment would additionally
need the versioned model/inference adapter and parity checks already identified
in the implementation guide.
