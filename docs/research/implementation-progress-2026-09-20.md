# Implementation progress after Phase A

This is a delivery record, not a claim that Phases B–G have all passed their gates.
The original audit and its source archive remain unchanged. No Git commit has
been made, and pre-existing working-tree changes have been preserved.

## Confirmed decisions

- Reliable paper operations and defensible research lead the roadmap.
- Paper deployment now uses the user-authorized v13 strategy. The default
  research score remains exposure-matched risk-adjusted improvement; v13 belongs
  to the separate return-target program.
- Execution notional and order-count limits are explicitly `unlimited`, as
  requested. Missing/invalid limits remain errors. Strategy allocation caps,
  the configuration allowlist, equity-drift guard and kill switch remain.
- The first data implementation targets 2016 onward. Pre-2016 data is deferred.
- Dollar spending has no hard ceiling; research iteration/context/timeout
  bounds and existing frozen-program token budgets remain applicable.
- LangGraph plus the existing router remains the approved research architecture.
  No Google ADK or treesitting/jev integration is planned.

## Operational repairs implemented

The old container was stopped and its state preserved privately under
`executor/state/phase-b-recovery/`. Its original environment file is backed up
with restricted permissions. The original journal, including malformed lines,
was not rewritten.

The rebuilt `amat-executor:phase-b-20260920` image has been deployed. Installed
Python source hashes match the checkout. The strategy file is
`configs/strategy/amat_v13_return_target.toml`, SHA-256
`fd08c1d114a4ee563944a8fd9e6fcc7afa6d47a5de4722f6676f9665227c5792`.
`v1-frozen` is the engine identifier; the emitted strategy version is `13.0.0`.

Fresh-container DNS and broker connectivity work. The original container's
resolver retained upstream DNS from an earlier host network. Recreating the
container resolved the observed DNS failure without changing other containers.

The initial deployed probe formed a v13 plan from the full verified snapshot plus
five appended sessions, through September 18. It recognized all existing
holdings and emitted five intents. This was a plan-only probe; it placed no
orders. The UTC cutoff previously admitted the current exchange-local date
during the US evening. The cutoff now uses the previous exchange-local date;
weekend/holiday dates are not presented as verified exchange sessions.

The final deployment uses the newly built immutable snapshot
`alpaca-sip-core-2026-09-19-verified`, through September 18, with 2,693 sessions
and 80,790 opening-window bars across 30 stored symbols. Daily raw/adjusted
history and corporate actions were refreshed; verified historical execution
windows were reused. The final in-container v13 probe emits five intents with
zero appended sessions, no unknown holdings, and matching configuration
authorization. The container is healthy and installed source matches the
checkout. Its image is
`sha256:8c531c688705178c139def915432f35a1ab7fe8e116e2e0dd392c52e71379a1d`.

Additional repairs:

- Submissions serialize through a state-directory lock.
- Retries reuse the archived session plan, preserving intent IDs after partial
  fills. Archival writes are flushed, fsynced and atomically replaced before
  submission.
- The broker calendar must confirm that the plan uses the immediately preceding
  exchange session. A closed or missing session fails closed.
- Open orders outside the session plan block submission.
- Plan-to-order reconciliation matches deterministic client order IDs, symbols
  and sides. Missing plans and failed order retrieval cannot appear reconciled.
- Reports state the limited reconciliation scope and expose unreadable history
  counts. They do not claim complete portfolio/cash reconciliation.
- `DRY_RUN` and `EXECUTOR_DISABLED` reject malformed boolean values.
- The Docker build context excludes credentials, state backups and unrelated
  files. The healthcheck documentation accurately describes a connectivity probe.
- The pre-existing AMAT formatting failure is fixed.

## Broker state and containment

The six orders from the last recorded v6 plan matched its six intents. Current
broker positions matched starting positions plus those fills within the probe's
share tolerance. This is an observation at one time, not continuous monitoring.
Cash and account-activity reconciliation remain unimplemented.

A non-AMAT SPY buy order for 100 shares, submitted August 3 with zero fills,
was found. The user authorized cancellation, and a DELETE for that exact
freshly rechecked order was accepted. The latest observed state is
`pending_cancel`, not `canceled`. No other order was canceled.

The container is running and healthy with v13, but `EXECUTOR_DISABLED=true`
and `DRY_RUN=true` remain in place while the broker order is unresolved. These
flags distinguish operational availability from permission to submit orders.
No stock purchase/sale was submitted to validate these changes.

## Phase C work delivered

`scripts/audit_data_coverage.py` verifies immutable partition hashes and exports
symbol/field coverage for a specified strategy. The v13 inventory covers all
11 symbols, 132 field/partition rows, and finds no null or nonfinite field
values in the inspected snapshot. The snapshot runs from January 4, 2016 through
September 18, 2026. It is not a claim of corporate-action or exchange-calendar
completeness, nor evidence of publication-time availability.

Research exports now include `analysis/periods.csv`, linked from the existing
HTML report. It contains full-period, calendar-quarter and four contiguous
historical-block summaries from the same continuous run. Blocks divide return
observations as evenly as possible. Equity anchors are excluded from sample
counts; first returns at boundaries are retained; quarter and block returns
compound to the full-period result. Fields include compounded/cumulative return,
drawdown, turnover, exposure and observation counts. Calendar completeness is
explicitly unverified; observed date endpoints are retained. No backtest state
is reset at a reporting boundary.

Benchmark-matched period panels, explicit exchange-calendar completeness,
additional null controls and the new training-through-2025 protocol remain.
Historical 2026 data must not be relabeled as an untouched holdout.

## Early Phase D audit repairs delivered

- Schema downgrade retries moved from inside the compatible transport into the
  router. Each HTTP attempt now has its own reservation and settlement.
  Unknown usage on a rejected request remains conservatively charged.
- Compatible chat and OpenAI Responses results expose `served_model` separately
  from the requested model identifier when the provider supplies it.
- Groq is a supported explicit route using the documented fixed endpoint and
  `GROQ_API_KEY`. It has not been made a default or live-quality certified.
- New calls can request `persistence="metadata_only"` in the research ledger.
  Prompt/response bodies are replaced by hashes and accounting metadata before
  SQLite writes. The regression test checks the database bytes for a raw-page
  sentinel. Legacy default serialization remains unchanged.

These changes do not establish repo-wide RAM-only retrieval: the committee
store, graph checkpoints, tracing and future evidence admission must also be
handled before enabling web research. The committee still owns separate model
transports and durable accounting. No Tavily/LLM call was made in this batch.

## Remaining acceptance gates

| Phase | Status and next work |
|---|---|
| A | Complete; original audit and baseline retained |
| B | Repairs validated; broker cancellation pending, cash/activity reconciliation, lifecycle registry, shadow statistical monitoring and operational observation window remain |
| C | Coverage inventory and period exports implemented; causal data alignment, complete supported-period validation, matched comparisons, statistical protocol and 2025 training program remain |
| D | Router accounting/identity/persistence foundations implemented; committee adapter, global cost metering, policy/catalog, evidence contracts, memory admission, Tavily, bounded LangGraph and evaluation remain |
| E | Regime/signal experiments wait for the evaluation protocol; no new signal promoted |
| F | Existing HTML report gets period links; full operations UI and evaluated adaptive news are not implemented |
| G | Scanner and crypto arms remain deferred until shared data/evaluation/risk gates pass |

## Evidence

Final quality checks: AMAT formatting, Ruff and mypy passed; 2,001 tests passed
and one existing undeclared-layer test was skipped (353.45 seconds). Executor
formatting, Ruff and mypy passed; 186 tests passed. All 12 Phase A candidate,
objective and daily-series fixture artifacts matched their original SHA-256
digests exactly. Tests did not enable live model providers or open historical
holdout results for selection.

- `phase-bc-evidence-2026-09-20/deployment-proof.json`: image identity,
  configuration authorization and installed-source parity.
- `phase-bc-evidence-2026-09-20/broker-validation.json`: sanitized broker and
  v13 plan observations, including pending cancellation.
- `phase-bc-evidence-2026-09-20/v13-data-coverage.json`: field coverage inventory.
- Adjacent AMAT and executor test logs record full regression results.
- `baseline-regression.json` records all twelve exact artifact comparisons.
- `data-refresh.log` records the immutable snapshot refresh.

Provider references used during implementation:
[Alpaca data timeline](https://alpaca.markets/support/alpaca-data-timeline),
[Alpaca historical bars](https://docs.alpaca.markets/us/v1.4.2/reference/stockbars),
[Groq OpenAI compatibility](https://console.groq.com/docs/openai).

The documentation directory is ignored by this repository's existing Git
configuration. These artifacts exist locally and need deliberate inclusion in
a future review; none was silently force-added.

## September 21 continuation

Earlier test counts and image identity above describe the earlier delivery.
See [pipeline-continuation-2026-09-21.md](pipeline-continuation-2026-09-21.md) for
portfolio reconciliation, compact CLI and shared free-first/Luna routing.
Cash/share changes now reconcile from an observed baseline; historical account
reconciliation and remaining Phase B lifecycle gates remain open. Phase D provider
integration has advanced; graph/evidence/privacy gates remain open.
