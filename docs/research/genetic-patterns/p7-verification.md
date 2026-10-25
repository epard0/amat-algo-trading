# P7 verification — 2026-09-29

P7 resolves the P6 adjusted-history blocker with an explicit reconciliation and
refresh command. Two subagents implemented reconciliation and refresh independently;
an additional read-only integration review verified source evidence, unchanged
candidate identity, preserved calibration dates, and snapshot integrity.

## Reconciliation measured on real data

Downloaded raw and all-adjusted SIP daily bars for the 11 incumbent ETFs from
2016-01-04 through 2026-09-28: **29,689 records per adjustment mode**.
Across the **29,568 overlapping asset/session rows**, raw OHLCV was exactly
unchanged. There were **8,064 changed adjusted closes**, all in SPY, QQQ and IWM.

| Symbol | New ex-dividend date | Cash distribution | Evidence |
|---|---|---:|---|
| SPY | 2026-09-18 | $1.888834 | State Street historical distribution workbook, row 9068 |
| QQQ | 2026-09-21 | $0.75143 | Nasdaq dividend API, row 0 |
| IWM | 2026-09-15 | $0.750436 | Retained Alpaca corporate-action receipt |

The original Alpaca action response omitted SPY and QQQ; a focused follow-up query
also omitted them. The provider-only reconciliation correctly refused these two
assets. Separate supplements retain the captured source files, SHA-256 hashes,
URLs and extracted records. The original provider receipt remains unchanged.
Primary sources: [State Street workbook](https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-etf-historical-distributions.xlsx)
and [Nasdaq QQQ dividend endpoint](https://api.nasdaq.com/api/quote/QQQ/dividends?assetclass=etf).

With these independently documented events, every revised adjusted close falls
within the predeclared two-sided cent-rounding bound. There are **zero unexplained
revisions**. This is reconciliation of the observed changes, not a fitted correction
factor or permission to accept arbitrary later revisions. See
[adjustment-reconciliation.md](adjustment-reconciliation.md) for the exact contract.

## Refreshed artifact and successful observation

The new immutable snapshot is `alpaca-sip-shadow-2026-09-29-reconciled`, ending
2026-09-25. It contains 29,678 daily rows, 29,678 execution-window rows and 315
source action rows. Exact repeated actions collapse when loaded. The snapshot
ends one session before the downloaded daily inputs to exercise a real append.

The expression is unchanged. Calibration retains **2016-01-04 through
2018-12-31, 754 sessions**, with prices and reference statistics on the reconciled
basis. The new artifact has **no historical validation transfer**. The prior
historical rejection remains in force.

Campaign registration occurred at **2026-09-29 18:00:35.369321 UTC**, after that
day's exchange open. The earliest eligible prospective session is September 30.
The September 28 poll succeeded with sequence 0 and explicit
`backfill_shadow_diagnostic` classification. A repeat returned the same observation;
ledger, report and receipt hashes remained unchanged. Reference probe parity passed;
reported inference latency was approximately **1,051 ms** (not total download time).

The monitor raised **IEF positive_prevalence_drift** and latched **suspended**.
Every reported pattern contribution is neutral and modified scores equal baseline
scores. Five-timeframe values remain available as diagnostics. A successful data
append does not imply the pattern is suitable for promotion.

| View | Latest completed bar | Assets with value and usable calibration |
|---|---|---:|
| 15m | September 28, 20:00 UTC | 7/11 |
| 1h | September 28, 19:30 UTC | 5/11 |
| Daily | September 28 | 11/11 |
| Weekly | September 25 | 11/11 |
| Monthly | August 31 | 0/11 |

The hourly final 30-minute tail is omitted. Monthly calibration has 36 bars while
the expression requires 84. Missing intraday inputs/calibration remain explicit;
no complete-universe readiness or validation is inferred from partial availability.

**Historical backfills: 1. Prospective observations: 0. Execution authority: none.
Promotion authority: none.** No scheduler or broker loop was started.

## Retained evidence and checks

Evidence is in `outputs/genetic-patterns/2026-09-29-p7/`. Start with
`spotting-report.md`, `summary.json`, and `refresh/refresh.json`. The bundle includes
both reconciliation reports, observation inputs, retry verification, source archive,
reproduction scripts, test logs, and an evidence manifest. External receipt and
snapshot hashes are listed in `data-reference.json`.

All **896 files** in the prior P4/P5/P6 evidence manifests still match their original
hashes. The new runtime archive contains **168 runtime Python files**, and **200
source/configuration/dependency/test files** in total.

Focused checks: **34 tests passed**. Coverage includes complete calendar and symbol
coverage, immutable raw history/actions, independently explained adjustments,
unsupported splits, malformed evidence, calibration identity, new immutable outputs,
and CLI routing. Full regression results are recorded below and in `checks.json`.

## Next work

Investigate the IEF prevalence alert and improve 15m/hourly calibration coverage
before considering any separately reviewed monitor or pattern change. Monthly
calibration requires a longer declared history or a separately evaluated expression
with a shorter lookback. Continue collecting explicitly labelled shadow observations
under the existing suspension policy; do not reset the ledger to erase the alert.

For a later completed session, fetch an updated minute receipt to a new path with
`shadow-fetch-intraday`, then use `shadow-poll` with the P7 `campaign.json`, existing
`ledger.json`, and a new dated output path. The exact successful September 28
command is retained in `poll-attempt.json`. Daily and previously observed intraday
history must still match; later distributions may require another explicit refresh.
Future source changes require a new source-bound artifact/campaign. Prospective
qualification depends on actual registration and session times, not the output name.

Final checks: **2508 passed, 1 skipped in 482.43 seconds**; MyPy passed for 27
genetic source modules; Ruff and formatting passed for 55 source/test files;
`git diff --check` passed. The existing dependency-direction skip remains. No
executor source changed in P7, so its separate suite was not run.
