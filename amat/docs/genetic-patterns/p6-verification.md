# P6 verification — 2026-09-29

Implemented with two subagents and an independent integration review. Scope is
completed-session shadow campaigns plus 15m, hourly, daily, weekly and monthly
pattern diagnostics. Runtime source was frozen before the retained experiments.

## Real inputs and outputs

Evidence: `outputs/genetic-patterns/2026-09-29-p6/`.
Human-readable result: `spotting-report.md`; machine-readable summary:
`summary.json`. The immutable source archive contains 166 runtime Python files
and 196 source/config/dependency/test files in total.

Downloaded **201,094** all-adjusted SIP minute records for all 11 incumbent ETFs,
2026-08-17 through 2026-09-28. The approximately 23 MiB receipt is retained at
`data/research/genetic-patterns-p6-2026-09-29/minutes.json`; its file and payload
hashes are in `data-reference.json`. Extended-hours records remain in the receipt;
views use only the independently scheduled regular session.

Intraday calibration ends 2026-09-11. The observed views through September 28
contain 780 completed 15-minute intervals and 180 full hourly intervals. Calibration
contains 494 and 114 respectively. The expression requires 84 bars at each scale.

| View | Latest value available | Latest value plus usable calibration | Complete-universe status |
|---|---:|---:|---|
| 15m | 8/11 | 7/11 | Some inputs/calibration unavailable |
| 1h | 6/11 | 5/11 | Some inputs/calibration unavailable |
| Daily | 11/11 | 11/11 | Ready on September 11 snapshot |
| Weekly | 11/11 | 11/11 | Ready on September 11 snapshot |
| Monthly | 11/11 | 0/11 | 36 calibration months; 84 required |

These are pattern-expression diagnostics, not buy/sell instructions or measures
of profitability. The daily/weekly/monthly source ends September 11, while the
minute receipt ends September 28. A view's `ready` status means data/calibration
availability, never validation or promotion. Intraday full-universe readiness is
withheld even though several individual assets have usable values.

## Registry compatibility

The expanded timeframe contract changes the registry source hash. Ordinary
`shadow-freeze` correctly refused to treat the old P4 candidate as an identical
current-runtime candidate. Explicit `--allow-registry-rebind` produced a new
source-bound diagnostic artifact retaining the original historical candidate
hash, rejection evidence, and a no-validation-transfer declaration.

The archived P5 runtime was extracted and all **162 original runtime hashes**
verified. Old and new runtimes independently evaluated the complete 2,688-session,
11-asset snapshot. Pattern outputs, baseline scores and modified scores were
**exactly equal, including NaNs**. See `registry-rebind-parity.json` and the retained
reproduction scripts. This verifies numerical compatibility; it does not rerun
P4 selection or validate another timeframe.

## Live boundary and prospective evidence

Campaign registration: **2026-09-29 17:10:00.290480 UTC**. The registration occurred
after the day's exchange open, so the earliest eligible prospective daily session
would be September 30. No scheduler was created and no future session was awaited.

The real SIP daily poll through September 28 failed closed:

> shadow feed history revision in adjusted_closes; recalibration required

The CLI exited 2, with **no observation and no ledger created**. See
`poll-attempt.json`. Appending the refreshed prices to this frozen snapshot would
mix adjustment bases. Resolving that difference requires a new reconciled data
and calibration artifact; the implementation does not overwrite the prior data
or weaken parity checks to make the poll pass.

**Prospective observations: 0. Execution authority: none. Promotion authority: none.**
The historical pattern remains rejected. Intraday spotting and daily/weekly/monthly
snapshot diagnostics are labelled historical, regardless of their registration.

## Verification coverage

Tests cover calendar completion, holiday/early-close/DST alignment, partial-period
omission, missing minutes, duplicate bars, malformed OHLCV, calibration boundaries,
future-prefix invariance, stale inputs, and explicit timeframe bindings. Campaign
tests cover registration boundaries, immutable receipts, changed baseline/prior
history, publication recovery, same-session idempotence, and latched suspension.
The CLI routing and explicit registry-rebind/no-approval behavior are tested.

Integration review found and fixed stale intraday readiness and revised previously
observed intraday history. These cases have regression coverage. The final check
results are recorded in `checks.json` and the evidence bundle's test logs.

## Next operational work

Reconcile the provider's revised daily history and produce a new calibration
artifact while preserving the original P4/P5 evidence. Improve intraday calibration
coverage before relying on a full 11-asset view; monthly symbolic calibration
needs a longer declared history or a separately evaluated shorter-lookback pattern.
Then register a new campaign before an eligible session opens and collect actual
prospective observations. Automatic intraday polling and trading remain outside P6.

The supplementary two-session anchor audit found **0 raw-close changes and 6 adjusted-close changes out of 22 asset/date pairs**. This narrows the observed discrepancy; it is not a full corporate-action reconciliation. See `adjustment-anchor-audit.json`.

Final checks: **2476 passed, 1 skipped in 745.71 seconds**; MyPy passed for 25 genetic source modules; Ruff and formatting passed for 51 source/test files; `git diff --check` passed. The existing skipped dependency-direction case remains skipped. No executor source changed, so its separate suite was not run.
