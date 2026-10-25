# Implementation ledger — updated October 23, 2026

This ledger distinguishes delivered application behavior from the larger terminal roadmap. The earlier PLAN.md and API/CONTRACT.md remain design requirements; their example endpoints and future controls are not proof of an implemented adapter.

## Current architecture

The React application runs against a loopback FastAPI gateway. The six current desks (Markets, Performance, Kalshi perps, Tasks, Operations and Remote) are implemented, with historical market data, source health, artifact inspection, typed capability forms, previews and real action events. Registered executor files and AMAT artifacts remain owned by their source engines. Application events and jobs are persisted in a separate SQLite WAL database. Independent worker processes hold kernel leases and continue across HTTP restarts; orphaned unresolved work becomes outcome_unknown and is never blindly retried. Heavy research/backtest concurrency is bounded. The terminal does not start an executor daemon when opened. Native journal events retain their source payload and are distinct from application action lifecycle events.

A local deployment is not automatically the VPS deployment. The selected executor root must contain the state actually being monitored. An old report or local snapshot remains historical, even when the API connection and event stream are live.

## Source inventory

The refreshed inventory discovers 93 leaf commands and records 243 engine source hashes. This is discovery coverage. Executable adapters are enumerated at runtime by the API; unsupported controls retain a reason. New adapters must validate typed inputs, filesystem effects, source freshness and lifecycle semantics before they can be advertised as supported.

## Release checks

Backend regression tests, launcher checks, TypeScript/Vite builds and real browser journeys passed. The browser dispatched an actual AMAT configuration validation and observed requested, started, output and succeeded events. Same-origin production serving was verified on a separate port. No broker order or notification was sent. See ../tests/VERIFICATION.md for the specific browser evidence. Native Electron launch remains unverified.

## Remaining roadmap

Full genetic research/committee/news workflow editing, every CLI advanced form, live entitled market streaming, statistical strategy lifecycle gates, remote multi-user authentication, and packaged desktop installers require their own backend and acceptance work. These must not be represented as finished by a navigation label or a disabled button. The optional Electron shell is a wrapper around the same locally running application and does not add trading authority.

## Integrated adapters and remaining limits

Registered configuration tools, synthetic engine checks, historical backtests and bounded built-in research are executable. Actual offline synthetic and inert-plan jobs succeeded through the API/worker path, and a v1 historical backtest for 2019–2020 produced its native run evidence and PDF report in temporary application storage. Executor source observations are always read-only by default. Configured broker reads and optional existing paper submit/report commands have explicit runtime flags, typed inputs, previews and native preflight; they were tested at the contract boundary without broker effects. Executor stop/resume (via the `STOP_SUBMISSIONS` interlock, resume gated by `TERMINAL_ENABLE_EXECUTOR_ACTIONS=1` and typed confirmation), newsdesk actions, AMAT scan/walk-forward/Monte Carlo, and cooperative cancellation of read-only research/news jobs were added October 14; order-affecting jobs are deliberately not cancellable from the browser. Live quote/depth feeds are unavailable without a provider adapter; snapshot prices stay historical.

The chart preserves source prices and reports suspicious OHLC data rather than silently correcting it. An inherited SPY low-price anomaly appears in the local snapshots; the source requires review before those bars are used as reliable evidence.

## Final verification scope

The backend suite covers independent worker survival across service reconstruction, stale configuration previews, explicit opt-in executor adapters (without dispatching them), dated broker read models, event watermarks beyond 10,000 events, source parquet quality warnings, historical command bounds and streaming redaction across pipe chunks. The web build and dedicated Python lint checks pass. UI integration used actual historical source files and an actual configuration validation. Full engine suites were not rerun because no existing AMAT or executor implementation files were changed.

The final application suite passed 21 tests. Daily/weekly chart resolution, searchable command discovery, authenticated report downloads and measured local gateway runtime statistics are integrated. Development and production static browser views were verified; the built terminal is available on the running loopback API.

## October 23 delivery

The approved UI/operations release replaces modal task preview with a guided task CLI, automatic inline validation, one Run action and recoverable live output; replaces wide technical tables with responsive labeled values and expandable details; removes Operations reconciliation presentation and workspace-wide Tasks evidence; adds per-snapshot metadata summaries and a brighter 14px dark theme. The local API now schedules bounded cached remote checks for both engines and the retained alert watcher while running. Remote resource/archive/backup work preserves economic semantics and account evidence, and the optional shadow runtime is retired with its state retained.

Current evidence, tests, images, continuity, rollback and limits are in [the dated implementation report](../../operations/ui-remote-2026-10-23/REPORT.md). Older results above remain historical receipts.
