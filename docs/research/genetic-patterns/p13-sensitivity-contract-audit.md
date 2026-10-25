# P13 sensitivity extension contract audit

This extension adds 60 registered historical paths to the 16 frozen foundation paths: 24 additional-latency paths and 36 component-removal paths. These are paired diagnostics of selected policy histories, not 76 independent observations and not a new parameter-selection exercise.

## Independent foundation checks

Before implementation of the new study module, the isolated runtime contained all 182 source entries frozen by the foundation, with every hash unchanged. All 20 registered foundation input paths existed. The 16 checksummed run receipts exactly matched their entries in the foundation summary; all 48 registered CSV, Parquet, and exact state/execution trace hashes matched. The historical coverage was 2016-01-04 through 2026-09-25.

These checks describe the inspected state. The executable sensitivity registration must repeat and persist the checks when it runs; this note is not a substitute for a run receipt.

## Required source and receipt boundaries

A new registration may add new module entries while requiring every old source entry to match the foundation exactly. It must also match dependencies, frozen data/selection/action identities, and the established full-history account contract. Do not change the foundation's exact-source-map resume gate to accommodate the new files.

Keep imported foundation receipts byte-for-byte as separate provenance objects. A new sensitivity receipt may wrap a foundation run, but must bind both the original registration hash and original receipt file hash, as well as its own new registration and job identity. Copy the three data artifacts byte-for-byte and verify their hashes. Reject missing, unexpected, duplicated or misidentified primary runs; each of the four policies must have one run at each of 2, 5, 10 and 20 bps. Match run name, policy, cost, receipt and summary rather than relying on row ordering or a count of 16.

Resume must check registered files even when it makes zero replay calls. Reject edits to either the copied source receipt, new wrapper receipt, data artifacts, current numerical runtime, registered inputs, or source foundation. A cached successful status alone is insufficient.

## Timing and component contracts

Activation dates are indexed in the observed panel session calendar, not weekdays or calendar-day arithmetic. The primary decision is the first observed close after its selection cutoff; additional delay adds one or five observed sessions to that decision, retaining the same decision-to-execution lag. January 9, 2025 is absent because the exchange was closed: the 2025 +5 decision occurs January 10 and earliest execution January 13. Reject negative, boolean and noninteger delays, unobserved cutoffs, invalid lags, and activation/execution beyond the panel.

Delay applies to each annual recipe activation, not to all subsequent daily signals. Until the delayed activation, the actual outgoing context and portfolio remain active. All paths begin with baseline prehistory and the same initial cash for their cost scenario.

Component removal applies to every incoming recipe in the selected policy's complete history. `no_mtf` changes only `max_effect` to zero; `no_exits` changes only `exit_mode` to `none`; `neither` changes both. All portfolio parameters, timeframe weights, polarities, recipe placeholders, dates, eligibility masks and selection identities remain unchanged. Deep-copy recipes so nested lists cannot mutate the frozen parent artifacts. The established continuous embargo contract still applies: disabling new exits does not erase an embargo already incurred by actual prior decisions.

Keep every policy/control/delay/cost job identity, including policies that eventually select the same 2026 recipe. Their earlier orders, cash, holdings, entry bases and embargo state can differ. Do not deduplicate by final recipe hash, terminal configuration or coincidentally equal final equity. The matrix is exactly 16 primary + 24 delay + 36 component = 76 registered paths.

## Expected validation evidence

The new assembler should reproduce the four primary 2-bps trajectories against the frozen foundation before computing additional paths. Check complete daily equity/returns and exact state/execution traces, not just endpoint CAGR. Confirm same-cost pre-activation baseline parity and record actual activation/first-execution dates. Tests should exercise malformed schedules, the 2025 closure, nested recipe mutation, stale or swapped receipts, source subset drift, data-file tampering, and complete resume with zero evaluation calls.

Reports should retain full-history, tradable-history, post-selection and actual calendar-year slices of each single continuous run; their metrics have different denominators and must not be interchanged. Display costs and paired differences without choosing a best delay or control as a newly validated winner. No deployment, promotion, synthetic-path validation or untouched holdout claim follows from completing this matrix.
