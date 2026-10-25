# AMAT performance campaign — final report

Closed October 20, 2026 at 11:07:00 AM EDT, before the hard deadline of October 20, 2026 at 11:17:38 a.m. Detroit time (15:17:38 UTC). The scheduled research heartbeat is paused. No measurement was left running. This final cycle performed verification and publication only; no new experiment or promotion was started.

The current default remains **all groups + volatility-triggered cost-aware switching**. No candidate was promoted. Several challengers improved individual return measures, but none satisfied every required comparison across costs and earlier valid candidates. Historical benchmark coverage and learned-strategy execution parity also remain unresolved.

## Measurement contract

The comparable simulations retain full 2016 signal history, start flat with $100,000 on January 3, 2017, and end October 12, 2026. They use observed next-session 09:30–09:35 VWAP, corporate actions, whole-share purchases, the native 1% opening-volume entry cap, native exits, missed fills, zero cash yield and 2/6/10 bps per side. Corporate-action entitlements may require fractional closing sales. No opening liquidity was fabricated. Reused history is not an untouched holdout.

## Current default

| Assumed cost per side | Full-history net return | 2020-onward net return | 2023 net return |
|---|---:|---:|---:|
| 2 bps | 296.39% | 211.89% | 6.55% |
| 6 bps | 259.80% | 192.72% | 5.74% |
| 10 bps | 221.88% | 177.60% | 5.07% |

At 6 bps, maximum drawdown was 12.84%, cumulative fees were $24,604.44, fills were 1,929, annualized turnover was 23.42 times equity and average exposure was 86.26%. These are simulated outcomes under the fixed contract.

## Leading comparable challengers at 6 bps

| Strategy | Full history | 2020 onward | Maximum drawdown | 2023 | Decision |
|---|---:|---:|---:|---:|---|
| Current default · all groups + volatility switching | 259.80% | 192.72% | 12.84% | 5.74% | Keep current default |
| 40% cap + trend quality | 283.25% | 214.19% | 12.20% | 7.73% | Not promoted: misses other return-frontier cells |
| 40% cap + quality + 3% trade band | 278.46% | 214.38% | 12.52% | 5.96% | Not promoted: misses three return-frontier cells |
| Four-week ranking + cap + quality | 233.75% | 207.32% | 12.33% | 7.94% | Not promoted: misses five return-frontier cells |

Rejected challengers remain in sealed comparison evidence and are excluded from active strategy selection. Their strongest valid results still count toward the promotion frontier; an earlier risk rejection does not erase a return leader.

## Binding return frontier

| Cost | Best valid full-history return | Best valid 2020-onward return |
|---|---:|---:|
| 2 bps | 317.78% | 242.36% |
| 6 bps | 283.25% | 214.38% |
| 10 bps | 249.40% | 199.40% |

A candidate must meet every full-history and forward cell, strictly improve at least one, preserve the incumbent’s 2023 return at each cost and avoid worsening its 6 bps drawdown. Causality, accounting, stress/forward validation, full historical comparison coverage and native planning/execution parity must also pass before verified default promotion.

## Annual, monthly and stress diagnostics

| Year | Current default net return at 6 bps |
|---|---:|
| 2020 | 16.98% |
| 2021 | 28.22% |
| 2022 | 2.42% |
| 2023 | 5.74% |
| 2024 | 11.98% |
| 2025 | 33.89% |
| 2026 through October 12 | 20.20% |

| Stress window | Net return at 6 bps |
|---|---:|
| 2018 Q4 | -7.07% |
| COVID window | -9.54% |
| 2022 | 2.42% |

The default had 43 negative months among 118 observed months, including partial October 2026. Its three weakest months were:

| Month | Net return |
|---|---:|
| 2020-09 | -8.23% |
| 2020-03 | -7.53% |
| 2018-10 | -7.33% |

Monthly, annual, stress and recovery comparisons remain visible in the dashboard. Fixed-rule 2020-onward annual slices are chronological continuing-account evaluations; they are not newly trained rolling folds or untouched tests.

## Reproducibility and learned pipeline

- Seven archived genetic fits reproduced their original checkpoints, ledgers and selected programs. Current checkpoint differences are exactly explained by source-registry diagnostic identities and their derived checksums; raw identities remain different and the original strict failure is preserved.
- All 168 archived feature arrays reproduced under both frozen library versions.
- Annual and fold-boundary inference passed 756 feature/cutoff combinations, each with prefix and later-close-change checks: 1,512 checks.
- All 48 archived recipe choices reconstructed from training/selection records, excluding test-period returns from ranking. This does not prove historical wall-clock write ordering.
- The original scheduled/fractional runtime reproduced 54 logical historical execution cases, or 20 unique histories after deduplication. These outcomes are incompatible with the whole-share campaign frontier.
- The current runner lacks an equivalent annual strategy/exit-context interface. Current execution parity remains blocked. No guessed overlay or late native-engine patch was introduced.

Earlier invalid attempts remain preserved, including the unattested future-fit case and the list-payload reader failure. Corrected checks used explicit new preregistrations or unchanged-case reader repair; failures were not retrospectively relabelled as passes.

## Uncertainty after multiple comparisons

All 293 currently comparable cost/history combinations were jointly resampled using shared circular blocks, 118 paired months, 5,000 draws and fixed one- and three-month block lengths. The following are annualized log-growth gaps against the same-cost incumbent, in percentage points; they are not forecasts of next-year arithmetic returns.

| Challenger at 6 bps | Estimated gap | Adjusted 95% interval, one-month blocks | Adjusted 95% interval, three-month blocks |
|---|---:|---:|---:|
| 40% cap + trend quality | +0.64 pp | -1.81 to +3.09 pp | -1.28 to +2.57 pp |
| 40% cap + quality + 3% trade band | +0.51 pp | -1.35 to +2.38 pp | -1.14 to +2.17 pp |
| Four-week ranking + cap + quality | -0.76 pp | -7.11 to +5.58 pp | -7.40 to +5.88 pp |

Every displayed interval includes zero. This diagnostic does not establish a reliable gain for these challengers. It is conditional on measured comparable histories, excludes unreviewed/incompatible research, and cannot remove all adaptive-research bias or create a holdout. The sealed promotion criteria were unchanged.

## Remaining limitations and next engineering work

Historical coverage still has 15 pending source reviews and 44 legacy registries. The default is retained operationally; this is not a claim that it beats every historical attempt. The next required engineering project is versioned annual mandate scheduling with causal activation, full account and authorized-intent identity, native exit semantics, planning/backtest parity and fair control/frontier replays. Fills, remaining intent, pending cash, expiry and account/policy identity must survive a context change without recreating authorization.

The dashboard preserves the four-strategy comparison and return bars, cost frontier, historical replay table, monthly/annual/stress/recovery views, fit evidence, six pipeline gates and adjusted uncertainty intervals. No live trades were placed; execution remains disabled and dry-run protections remain in force. No hosted model proposal/review was claimed.

Final closure verified the native engine manifest, default profile hash, sealed report inputs, inactive research state and saved PAUSED heartbeat status. The final audit remains incomplete for historical comparison coverage; no overall-best claim or learned execution-parity approval is made. All original failures and incompatible outcomes remain preserved.


## Largest default-strategy recovery episodes at 6 bps

| Peak | Trough | Recovered | Peak-to-recovery sessions | Drawdown |
|---|---|---|---:|---:|
| 2020-02-19 | 2020-03-31 | 2020-07-22 | 106 | 12.84% |
| 2026-01-29 | 2026-02-05 | 2026-05-14 | 72 | 12.60% |
| 2022-06-08 | 2023-03-15 | 2024-03-01 | 433 | 12.38% |

These episodes are historical diagnostics, not a promise of recovery within the same time. The strategy’s exits, exposure limits and authorization rules were not relaxed to meet a recovery quota.

## Final verification

The last measurement reproduced the incumbent at 2/6/10 bps. Six new uncertainty-method tests, Ruff, mypy and the production frontend build passed. Four browser suites verified the comparison tables/charts, adjusted interval values, fit evidence, pipeline blockers, costs, stress/forward views, mobile layout and rejected-selection restrictions. Earlier fit, inference, selection and archived-execution validations retain their sealed receipts and scope limitations. The final publication was separately checked after closure.
