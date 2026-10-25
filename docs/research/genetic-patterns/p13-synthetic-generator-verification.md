# P13 coherent synthetic generator verification

Completed October 1, 2026 in the isolated P13 worktree. **17 registered worlds**
completed successfully, one fixed seed for each declared scenario family. Each
retains 2,698 sessions from January 4, 2016 through September 25, 2026: 1,006
unchanged observed-prefix sessions and 1,692 generated sessions from January 2,
2020. Calibration ends December 31, 2019. No failed final worlds or redrawn seeds.

The 17 families are flat, bull, bear, range, whipsaw, crash/recovery, correlation
break, Gaussian, Student-t(5), Student-t(3), Laplace, skewed shocks, volatility
mixture, volatility clustering, gap down, gap up and liquidity crisis. They are
experimental stresses with common random numbers, not a probability forecast or
17 independent historical observations.

## Retained data

Every world contains **1,446,984 generated five-minute asset bars** for 11 ETFs;
the completed matrix contains **24,598,728 bars**. Five-minute observations are
generated only for the suffix; older five-minute history is not invented.

| Daily/view grid | Rows per asset per world |
|---|---:|
| Daily sessions | 2,698 |
| Fifteen-minute intervals | 69,896 |
| Full session-open hours | 16,125 |
| Complete calendar weeks | 560 |
| Complete calendar months | 128 |

Grids include explicit missing native-feature values before January 2019. All
views respect exchange closures, half days and DST. The canonical five-minute
trace supplies raw prices and the opening-five-minute execution window. Derived
signal prices use the fixed boundary adjustment factor; native-view volume uses
its separately declared pre-anchor empirical scale. Daily/execution volume stays
in modeled raw shares.

The final generation batch took **553.68 seconds** (9.23 minutes),
including data export and report generation. Retained world files occupy
**1.308 GiB**. This is measured generator throughput, not a forecast
for nested GP fitting or the full policy/cost replay campaign. A sampled main
process RSS during generation was about 1.37 GiB; this is not a peak-memory claim.

- [World charts and diagnostics](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-01-p13-generator/analysis/report.html)
- [Generation report](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-01-p13-generator/study/report.html)
- [Calibration table](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-01-p13-generator/calibration.csv)
- [Registration](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-01-p13-generator/study/registration.json)
- [All world receipts/statistics](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-01-p13-generator/study/summary.json)
- [Implementation and assumptions](p13-synthetic-generator.md)

## Verification

**253 scoped tests pass:** 67 generator tests, 14 evidence/resume tests,
3 diagnostic-report tests and 169 related regressions. Type checks pass for both
new runtime modules and the diagnostic script. All seven owned source/script/test
files pass lint and formatting.

The generator independently reconciles canonical OHLCV/VWAP against daily,
15-minute, hourly, weekly and monthly exports before publication. Adversarial tests
cover future-observation poisoning, horizon stability, all 17 families, invalid
calibration, missing bars, event shock magnitude, volume-scale continuity,
calendar gaps, reordered symbols and tampered evidence. Extreme numerical paths
would retain failures; no clipping or redraw selects a more convenient outcome.

Two separate baseline integration replays, bull and flat at 2 bps, cover all
2,698 sessions. In both, the first **1,006 complete account records match the
historical baseline exactly**, including cash, holdings, pending orders and entry
state. Neither credits suffix corporate actions. The flat world's largest
absolute market-P&L residual is **2e-23**, below the engine's 1e-8 accounting
tolerance. These are bounded software-integration checks, not the 544-run campaign.

The immutable-resume check disables generation and verifies all retained worlds
without numerical regeneration, preserving per-world hashes and modification
times. The recorded proof and evidence manifest bind the final files. Earlier
P11/P12/foundation/sensitivity evidence and all 184 prior runtime files remain
unchanged. The two new modules have a new registration and frozen runtime archive;
old studies retain their original source gates and archived runtimes.

## Audit corrections retained transparently

The initial partial benchmark is retained separately under
`superseded-calibration-attempt` with its exact source and registration. An audit
found event-day gap shocks partly counted twice and an intraday volume-scale
jump for split-adjusted assets. Both were corrected before the fresh final matrix:
event shocks enter once, and generated native-view volume uses the median
pre-anchor native-session/raw-daily volume ratio. For USO that factor is about
0.1151. It includes feed/session coverage and is not asserted to be an exact split
factor. Added tests isolate both effects while confirming unchanged raw prices
and execution capacity.

Initial integration-helper checks also exposed an incorrectly ordered validation
Program, an omitted baseline context label, and an overly strict exact-zero
Decimal assertion. These were corrected in the evidence helper; no shared runner
was changed. The completed prefix comparison is byte-exact and the flat residual
uses the existing accounting tolerance. Earlier diagnostic logs remain retained.

## What this does and does not complete

This completes the coherent generator and a one-seed/all-family generation
benchmark. It does not complete the proposed 544 policy/cost replays, the 17,408-run
extended campaign, or an adaptive optimizer. The current non-daily symbolic
evaluator deliberately rejects synthetic 15-minute/hourly source identifiers.
The next step is an explicit validated synthetic research-view adapter, then
recompute all five-timeframe features inside each world, preserve frozen-selection
provenance, and replay all four policies and costs against identical world bytes.
No source is relabeled as Alpaca and no observed suffix feature cache is reused.

Within-five-minute structure and VWAP remain declared assumptions because no
pre-2020 fine-resolution observations were available in the retained inputs.
The calibrated mask models independent per-asset/15-minute observation gaps;
correlated feed outages require a separate stress. Calibration sigma bounds,
correlation shrinkage, fixed bridge amplitudes and volume coupling are model
choices, not estimates of future scenario probabilities. Volume is scaled by
session duration and a bounded return-surprise multiplier; prices are not clipped.

The registered historical endpoint remains September 25, 2026 because later
receipts lack the verified opening-window execution data. No new market download
or live-paper change occurred. Docker was observed healthy with DRY_RUN=true and
EXECUTOR_DISABLED=true. Paper readiness still requires research/executor parity.
