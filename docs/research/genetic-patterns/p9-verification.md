# P9 native-bar investigation — 2026-09-29

Two implementation subagents added native aggregate receipts and a comparison
audit. A separate cross-review found no material defects. Parent integration
added both CLI routes and retained the real provider experiment.

## Data and exact parity

Captured **47,202 native 15Min bars** and **12,976 native 1Hour bars** for all 11
assets, June 1 through September 28. These totals include extended hours. The
separate typed receipts preserve the original timestamps and collection times;
normalization replays the captured OHLCV without another download. The original
captured bar records, including additional provider fields, remain retained.

Calibration cutoff remains September 11; observation cutoff remains September 28
at 20:00 UTC. P8's 557,838 minute records were not changed. Only closed full
regular-session intervals enter the comparison.

| Measure | Native 15m grid | Native clock-hour grid |
|---|---:|---:|
| Expected intervals per asset | 2,158 | 498 |
| Expected asset/interval pairs | 23,738 | 5,478 |
| Native bars present | 23,734 | 5,478 |
| Intervals with every constituent minute present | 19,511 | 4,242 |
| Native bars with sparse minute constituents | 4,223 | 1,236 |
| Exact OHLCV parity with available constituents | 23,734 | 5,478 |
| Parity violations | 0 | 0 |
| Native absent despite available minutes | 0 | 0 |
| Native present with no available minutes | 0 | 0 |

Every compared open, high, low, close and volume was exactly equal. All four
absent native 15m intervals belong to UUP and had no emitted minute constituents.
This establishes same-provider aggregation consistency over the observed sample.
It does not independently establish the reason for absent minutes.

Native hourly timestamps all land on the clock hour. Their comparison grid starts
at 10:00 ET and ends at 16:00 ET on normal sessions. Strict P8 hourly views start
at 09:30 and end at 15:30. The 4,242 strict-minute-complete intervals above are on
the **native clock-hour grid**, not P8's session-open grid.

## A viable separate 09:30-aligned contract

Four consecutive native 15m bars can cover each existing 09:30-aligned hourly
interval without inventing a minute price. P9 counts component presence only;
it does not enable these bars as inference inputs.

| Assets | Native 15m calibration run | Four-component hourly intervals present | Longest hourly calibration run |
|---|---:|---:|---:|
| SPY, QQQ, IWM, EFA, EEM, IEF, GLD, SLV, DBC, USO | 1,872 | 498/498 each | 432 |
| UUP | 1,535 | 494/498 | 354 |

Every asset has a native-data calibration run longer than the expression's
84-bar requirement. This is **input continuity**, not a guarantee that the
expression will yield a finite value or pass validation. Four missing UUP native
components remain missing and must propagate to derived hours.

## Targeted minute refetches

Selected the latest missing regular-session minute in September 1–28 for IEF,
DBC and UUP, then queried raw minute bars for that interval:

| Asset | Minute, UTC | Raw minute bar returned |
|---|---|---:|
| IEF | September 21, 13:54 | 0 |
| DBC | September 28, 19:42 | 0 |
| UUP | September 28, 19:49 | 0 |

Native 15m and hourly bars covering these timestamps are retained in the sample
report. IEF's covering native hour crosses the session open and is excluded from
the regular-session comparison. Samples were targeted, not random. Trade-level
classification was not performed: the project's existing market-data client
allows only bars and corporate actions. Its endpoint restriction was preserved.
Neither refetch absence nor native parity proves that no trading occurred.

## Runtime and evidence

The existing strict aggregation, pattern evaluator and monitor policy were not
modified. The new diagnostic artifact binds the P9 runtime while retaining
exact candidate, original daily calibration, reference statistics, registry,
monitor policy and numerical probes. The P7 campaign remains suspended. No new
campaign, prospective observation, scheduler, trading authority or promotion was
created. Archived earlier artifacts retain their own runtime binding.

Evidence is under `outputs/genetic-patterns/2026-09-29-p9/`. Start with
`spotting-report.md`, `native-summary.json`, both native comparison envelopes,
and `gap-samples-summary.json`. The bundle includes raw/source data hashes,
reproduction scripts, source archive, prior evidence verification, and test logs.
The separate contract and runnable commands are in
[native-intraday-evidence.md](native-intraday-evidence.md).

## Next implementation

Implement an explicitly selected native-15m research view and compose four native
15m bars for session-open hourly views. Bind that source/aggregation contract into
fresh per-timeframe calibration, preserve absent native components, and evaluate
the unchanged expression against the strict baseline. Verify timing, prefix
invariance, feature availability and output differences before considering a
campaign migration. Such a migration must preserve the existing suspended state
and historical rejection. Do not silently replace the current minute contract or
reuse its calibration statistics.

## Verification results

**479 genetic-pattern regression tests passed in 66.19 seconds**, including
54 added cases: 33 native-receipt, 19 native-comparison, and two CLI routes.
MyPy passed all 31 genetic source modules; Ruff lint and formatting passed all
63 source/test files. `git diff --check` passed. The full unrelated application
and executor suites were not rerun for these additive research changes.
All **955 prior P4–P8 evidence files** still match their manifests. The P9 archive
contains 172 runtime Python files and 208 source/configuration/test files total.
