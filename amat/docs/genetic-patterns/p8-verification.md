# P8: drift investigation and intraday coverage — 2026-09-29

P8 adds reusable explanations of prevalence alerts and an immutable intraday
coverage audit. Two implementation subagents completed separate modules; an
independent cross-review found no material issues. No inference, aggregation,
monitor threshold, execution, or promotion behavior was changed.

## IEF: valid alert, no reset

The frozen daily reference is the final 252 sessions of the original calibration:
2017-12-29 through 2018-12-31. IEF had 72 positive pattern values (28.571%). The
observed 20-session window, 2026-08-31 through 2026-09-28, had 15 positives (75%).
The absolute difference is **46.429 percentage points**, exceeding the frozen
**35-point** threshold by **11.429 points**. The mean-shift z-score is 0.940,
which is below its separate threshold of 4.

The expression is `tanh(-1 + efficiency_z - volatility_shock_z)`. The investigation
retains all 20 values and the two normalized feature terms. Recomputed reference
prevalence and the latest output match the P7 artifact/report exactly. The
prevalence monitor implements a distribution-shift heuristic; it is not a
significance test or evidence of profitability. Overlapping rolling windows are
not independent observations. Nothing here justifies relaxing the threshold or
resetting the suspension.

`explain_prevalence_drift` in `drift_diagnostics.py` exposes positive/finite/missing
counts, signed and absolute shifts, threshold margins, and unavailable comparisons.
The P7 ledger remains suspended. No new campaign or prospective observation was
created in P8.

## Real intraday extension

Downloaded **557,838 SIP all-adjusted minute records**, 2026-06-01 through
2026-09-28, for the same 11 assets. The source receipt is approximately 63 MiB:
`data/research/genetic-patterns-p8-2026-09-29/minutes.json`.
All **201,094** overlapping records from P6 are present and exactly unchanged.
The intraday calibration cutoff stays **2026-09-11** and observation ends
**2026-09-28 20:00 UTC**. This is a historical coverage experiment, not validation
or selection on an untouched holdout.

| Metric | Earlier history from August 17 | Extended history from June 1 |
|---|---:|---:|
| 15m calibration grid bars | 494 | 1,872 |
| Hourly calibration grid bars | 114 | 432 |
| 15m assets with usable calibration | 8/11 | 8/11 |
| Hourly assets with usable calibration | 5/11 | 7/11 |
| 15m assets with calibration and latest value | 7/11 | 7/11 |
| Hourly assets with calibration and latest value | 5/11 | 6/11 |

The hourly usable set now includes **EEM**, alongside SPY, QQQ, IWM, GLD and SLV.
EFA gained usable hourly calibration but its latest value remains unavailable.
The 15m usable set is SPY, QQQ, IWM, EFA, EEM, GLD and SLV. A finite calibration
summary does not establish representative coverage or predictive usefulness.

## Why more history is insufficient for some assets

The expression requires 84 bars. Source gaps invalidate the entire aggregate and
can break subsequent rolling-feature lookbacks. The audit counts only closed full
XNYS intervals: extended hours, partial current bars, and the final hourly
30-minute tail do not count as gaps. Runs span adjacent grid bars across sessions.

| Asset | Longest calibration run, 15m | Longest calibration run, hourly | Incomplete bars in latest 84, 15m / hourly |
|---|---:|---:|---:|
| EFA | 601 | 138 | 0 / 1 |
| IEF | 50 | 11 | 0 / 6 |
| DBC | 9 | 1 | 74 / 83 |
| USO | 240 | 55 | 1 / 2 |
| UUP | 9 | 1 | 82 / 84 |

IEF has a current 15m value but no usable 15m calibration. DBC and UUP lack
continuous input history at both scales. USO has usable 15m calibration but a
recent source gap. The receipt alone cannot distinguish an absent no-trade minute
from a vendor omission. No minute is synthesized, carried forward, or treated as
zero-volume by this implementation.

## Reusable command

Run from `amat`; use a new output path for each immutable report:

```sh
uv run --locked autoresearch patterns shadow-audit-intraday \
  --intraday-data data/research/genetic-patterns-p8-2026-09-29/minutes.json \
  --intraday-calibration-end 2026-09-11 \
  --as-of 2026-09-28T20:00:00+00:00 \
  --output outputs/my-audit/coverage.json
```

The report includes per-asset expected/present/missing minutes, complete intervals,
calibration coverage, longest uninterrupted runs, missing timestamps in the latest
interval, and up to ten source-gap examples. Source file and payload hashes bind
the receipt. This audit covers the receipt's range; freshness beyond that range
is assessed by the existing spotting workflow.

## Evidence, validation, and runtime binding

Evidence lives in `outputs/genetic-patterns/2026-09-29-p8/`. Start with
`spotting-report.md`, `coverage-comparison.json`, `extended-coverage-audit.json`, and
`ief-drift-analysis.json`. Reproduction scripts, source archive, input hashes,
prior evidence verification, and test logs are retained.

The new diagnostic artifact binds the P8 runtime. Candidate, original daily
calibration panel, reference statistics, monitor policy, registry and numerical
probes exactly match P7. The earlier campaign is not rebound or reset. P7 artifacts
remain usable with their archived runtime; current runtime validation continues
to refuse stale source bindings. The P8 artifact is for historical diagnostics,
not a new unsuspended campaign. All prior P4–P7 evidence remains unchanged.

**425 genetic-pattern tests passed in 38.28 seconds**, including 35 additional
cases (19 drift, 15 coverage, one CLI route). The focused subset passed 40 tests.
MyPy passed all 29 genetic source modules. Ruff lint and formatting passed all
59 genetic source/test files; whitespace checks passed. The full unrelated
application/executor suites were not rerun for these additive diagnostic changes.

## Next engineering work

Use the retained gap timestamps to investigate provider/native aggregate coverage
for sparse assets. Any alternative source or no-trade-minute policy must be an
explicit data contract with independent alignment and price-basis checks, followed
by fresh calibration and validation. Do not silently weaken strict aggregation.
Continue the existing suspension policy while collecting actual prospective
observations under a compatible runtime. A campaign migration, if needed, must
preserve the latched suspension and prior ledger rather than erase them.
Monthly calibration remains unchanged at 36 bars against an 84-bar requirement.
