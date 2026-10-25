# P10 multi-timeframe parameter research — 2026-09-29

P10 adds native 15m and session-open hourly views to the authoritative offline
portfolio runner, together with daily, weekly and monthly inputs. Implementation
used two subagents and independent cross-review. The extended study freezes its
runtime, data, objective, domains and chronological selection boundaries.

## Implementation and verification

The new `native_views.py` requires full four-component native 15m coverage for an
hourly bar, preserves missing calendar rows, handles exchange-session boundaries,
and excludes incomplete final hours. `mtf_replay.py` aligns only completed views,
prevents stale intraday carry, preserves zero entry gates, and keeps absent view
contributions neutral. Its zero-effect replay matches the existing simulation,
including costs, delayed decisions, risk scaling and selection behavior.

`parameter_search.py` provides deterministic global sampling, shrinking local
refinement, recipe deduplication, frozen phase selections and atomic checksummed
checkpoints. Interrupted batches reuse completed receipts. Invalid evaluations
are recorded; unexpected failures abort. Changed specifications refuse resume.
`parameter_study.py` binds source/dependency/data identity, training-only feature
eligibility, feature-cache integrity and writer locks to the real portfolio engine.
The CLI adds `autoresearch patterns search-parameters`.

**564 regression tests passed in 38.41 seconds**, including the existing exit-rule
suite. MyPy passed 35 genetic source modules. Ruff lint and format checks passed
all 72 genetic source/test files. The unrelated full application and executor
suites were not rerun. No new dependency was installed.

## Retained data

The native15m receipt contains **1,041,746 records across 11 assets**, January 2,
2019 through September 25, 2026, including extended hours in the source receipt.
Only full regular-session intervals enter feature computation. All **46,627**
overlapping records through September 25 match P9 exactly in symbol, timestamp
and OHLCV, with no missing, extra or changed overlap records. All **983** files
covered by the P4–P9 manifests and **35** external references remain unchanged.

Native15m receipt payload SHA-256:
`054c5a975aff049b334105091dddcb03d4d523bcba45e868cbab34a258ff6e96`.
The reconciled daily snapshot, its manifest and all source partitions are separately
bound. Native features and daily execution prices remain distinct data contracts.

## Study contract

The budget is 2,048 global proposals, 256 local proposals and one exact baseline.
The objective is mean four-fold training Sharpe minus half the fold standard
deviation minus twice absolute maximum drawdown, with at least 20 fills and no
runner warnings. Training returns cover January 3, 2020 through December 29, 2023;
validation covers 2024; the final reporting period covers January 2, 2025 through
September 25, 2026. The training top 12 plus baseline enter validation. Validation
selects one; that frozen recipe and baseline enter final test reporting.

All 27 registered symbolic features at 21/63 bars and the retained GP expression
are included, giving 55 feature variants. Search spans five timeframe weights,
both polarities, portfolio trend/volatility/risk/rebalance settings, exit modes,
stop thresholds, profit/stop ratios and re-entry embargoes. Costs remain fixed at
2 bps one way during selection. Exact domains and limitations are in
[parameter-search.md](parameter-search.md).

Post-selection controls remove MTF, exits, or both; evaluate 2/5/10/20 bps costs;
and run 5,000 paired realized-return bootstrap paths at each of 5/20/60-session
block lengths. A plan registered before validation selection also removes each
individual timeframe, both intraday views, or all higher-timeframe views while
preserving other signal coefficients. Those controls cannot select a new recipe.

## Interpretation boundary

This is retrospective research on history accessed by earlier phases, not an
untouched prospective test. Search and return resampling do not prove an ideal
parameter set or correct for all selection effects. The bootstrap resamples
realized returns; it does not recompute the strategy on synthetic price paths.

Stops and take profits trigger on adjusted daily closes and execute in the next
window. They do not assume intrabar touch fills or implement partial ladders.
Training eligibility counts daily decision rows; independent source-bar counts
are retained separately, especially for slowly changing weekly/monthly values.
Fresh GP evolution, fitted routers/entry models and learned exit policies require
separate nested fit adapters and are outside this first parameter study.

Existing historical rejections and the suspended P7 campaign remain intact.
No live orders, campaign reset, prospective observations or promotion were created.

The post-selection ablations and cost controls replay their recipes over the
full history, then measure the test slice. They can change inherited holdings,
entry bases, embargo state and capital at the test boundary. They are
full-history counterfactual comparisons, not interventions initialized from an
identical test-start portfolio.

## Measured results

Completed **2,305 unique training evaluations**, with 0 invalid, 2 ineligible and 0 exact duplicate recipes. The main study and post-selection controls took 32.3 minutes.

| Final test | Total return | Annualized return | Sharpe | Maximum drawdown |
|---|---:|---:|---:|---:|
| Baseline | 40.36% | 21.76% | 1.235 | -15.01% |
| Validation-selected | 40.36% | 21.76% | 1.235 | -15.01% |

Validation selected the unchanged baseline, with no active MTF or exit overlay. Stored stop/profit settings are inactive placeholders, and the paired-return bootstrap is an identity check with all differences zero.

Stored method label: `genetic_pattern`, polarity 1, exit mode `none`. Its exact recipe, normalized timeframe weights, all validation finalists, ablations, cost stress and return-bootstrap intervals are retained in `results-summary.json` and `performance-report.md`.

Real completed-study resume verified **2,323 unchanged JSON receipts** and zero evaluator callbacks. All 176 source hashes still match the study context. The source/configuration/test archive contains 218 files. `git diff --check` passed.

Evidence: `outputs/genetic-patterns/2026-09-29-p10/`. Start with `performance-report.md`, `results-summary.json`, `postselection-controls.json`, `timeframe-controls.json`, `resume-verification.json` and `evidence-manifest.json`. Reproduction scripts, original test logs, data references, immutable trial receipts, feature metadata and source archive are retained. The interrupted preflight feature-preparation directory is explicitly excluded from the final evidence manifest.
