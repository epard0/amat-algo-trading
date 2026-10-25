# P5 verification — 2026-09-28

P5's report-only infrastructure was implemented with artifact and inference
subagents, plus independent cross-review. The main task added verified P4
packaging, snapshot/CLI integration and the durable observation ledger. No pattern
was promoted, and no live or paper broker process was started.

## Checks and review findings

All 42 new P5 cases passed together in 4.80 seconds:

- 19 artifact cases cover candle/symbolic round trips, native score parity,
  immutable output, checksums, rehashed invalid authority/registry/universe/
  history/policy, changed dependencies and calibration, recomputed reference
  statistics, and formula-to-digest consistency.
- 12 inference cases cover research parity, future-prefix independence, missing
  features/OHLC, direct dictionary tampering, calibration changes outside the
  stored probe rows, exact origin/universe, and constant/missing reference data.
- Six ledger cases cover identical retry, changed observations, suspension
  latching, atomic neutralization, body/chain/truncation corruption, artifact
  changes, rewinds, authority refusal and nested order-instruction refusal.
- Five orchestration cases cover inert snapshot reports, identical repeated
  publication, calibration mutation, conflicting outputs, historical-window
  refusal, P4 provenance/path checks, and protection against overwriting artifacts
  or ledgers with report output.

Review strengthened calibration verification beyond the final five numerical
probes: the entire calibration panel and recomputed reference statistics must
match. Ledger suspension is enforced under the writer lock before observation
identity is calculated, making retries idempotent even when a previously healthy
report must be neutralized by a latched suspension. Record count and head hash
check consistency if a valid-looking tail is removed.

Strict mypy passes 21 genetic source modules. Ruff and formatting pass all 42
checked genetic source/test files. `git diff --check` passes.

The final full AMAT suite passed **2,406 tests with one existing skip** in
379.02 seconds. The skip is the dependency-layer check for an undeclared
`__init__.py`. No executor code changed; its separate suite was not run. The full
log and exact check metadata are retained in the evidence bundle.

## Real-data artifact and observations

The artifact packages genetic shortlist rank zero from P4's observed `outer-2021`
fold. Its inner score was -0.292964, with rejection gates
`no_incremental_exposure_matched_information` and
`no_positive_incremental_score`. The artifact retains these failures, the fact
that inner selection retained the incumbent, and method-level failed support.
It remains diagnostic-only.

Calibration uses the exact P4 fit panel: 754 sessions from 2016-01-04 through
2018-12-31 over the native 11-ETF universe. The entire P4 evidence manifest was
verified before packaging; the calibration panel digest matches P4. The artifact
binds its candidate, current runtime, frozen registry, monitoring thresholds and
numerical reference probes.

Four historical observations use the existing verified snapshot, with only data
through each explicitly requested date:

| Session | Calibration parity | Monitor state | Inference latency |
|---|---|---|---:|
| 2026-09-08 | Exact | Suspended | 1,339.27 ms |
| 2026-09-09 | Exact | Suspended, latched | 1,073.14 ms |
| 2026-09-10 | Exact | Suspended, latched | 1,124.95 ms |
| 2026-09-11 | Exact | Suspended, latched | 1,427.48 ms |

The first observation exceeded the positive-prevalence drift threshold for SPY,
EFA and EEM. Subsequent observations continued to flag SPY/EEM, with the persistent
suspension recorded separately. Every effective contribution was exactly zero
and every reported score matched the native baseline. Candidate scores remain
available in the reports for diagnosis; they are not trade instructions.

Median measured inference/monitoring latency was 1,232.11 ms. These four timings
were collected while the full suite ran and include full-history computation
and calibration verification within inference. They exclude snapshot ingestion
and durable ledger/report publication. They are neither a production latency
SLA nor an incremental streaming benchmark.

Repeating 2026-09-11 preserved both the ledger and JSON report byte-for-byte.
An actual CLI invocation with a missing artifact returned nonzero and created
neither a report nor a ledger. No fallback model or pattern was substituted.

These are historical snapshot observations, not newly collected prospective
paper evidence. The number of prospective observations collected is **zero**.
Monitoring alerts do not establish economic underperformance, and absence of an
alert would not establish profitability or eligibility.

## Evidence and reproduction

[shadow-inference.md](shadow-inference.md) describes commands, frozen semantics,
monitoring thresholds, lifecycle and scope. [p5-smoke-runs.json](../../../amat/docs/genetic-patterns/p5-smoke-runs.json)
contains exact machine-readable observations and identities.

The bundle at `outputs/genetic-patterns/2026-09-28-p5/` contains:

- `artifact.json`: immutable diagnostic pattern, calibration and rejection history.
- `ledger.json`: four hash-chained observations and latched monitor state.
- Per-session JSON and Markdown reports.
- `replay-verification.json` and `missing-artifact-verification.json`.
- `source-runtime.tar.gz`: 188 runtime/configuration/dependency/test files;
  all 162 source identities match the frozen artifact.

Actual live ingestion still needs complete coherent OHLC and completed-bar
availability. Prospective monitoring, portfolio turnover, realized ablation
benefit and incumbent-to-pattern switching costs remain unassessed. The current
P4/P5 evidence supplies no candidate for promotion.

The bundle also retains command/test logs and `checks.json`. Its
`evidence-manifest.json` records a verified SHA-256 identity for every evidence file.
