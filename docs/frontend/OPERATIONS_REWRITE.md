# Operations workspace rewrite · 2026-10-20

Visual thesis: a quiet charcoal trading workspace with warm gold actions, report typography, and broad chart surfaces.
Content: Markets (5-minute candles), Performance (existing evidence and strategy reports), Kalshi perps (paper controls and account comparison), Execution (executor workflows), Research (autoresearch), Remote (live SSH diagnostics), with data and settings retained as supporting tools.
Interaction: short workspace transitions; explicit command preview and result states; live observations update without moving the reading position. Reduced-motion preferences apply.

## Implementation sequence

1. Preserve existing native engine gates, idempotency, interlocks and independent workers.
2. Open the operational shell by default; extract remote presentation and Kalshi controls; reuse the saved performance report.
3. Aggregate real minute history into 5-minute OHLCV. Keep historical provenance and empty states; never fabricate intraday bars from daily observations.
4. Move bounded SSH transport into an optional executor module, retaining strict pinned host keys, key-only auth, deadlines, combined output bounds and secret redaction at the presentation boundary.
5. Reuse durable action dispatch for registered paper-only Kalshi status/run-once/stop commands. Continuous runners require native process supervision; no unbounded browser-spawned daemon.
6. Check the existing VPS and repair observed resource/recovery issues without resetting state or enabling trading. Provision a replacement only if the current server is unusable.
7. Verify backend regressions, frontend build, actual browser navigation, real SSH health and Docker configuration. Record limits and deployed state.

## Reliability boundaries

One authoritative executor owns each state root. Restart on process failure must honor an intentional operator stop. Health probes and watchdogs detect stalled progress; resource and log limits bound failures. Durable state and submission reconciliation survive restarts without retrying unknown order outcomes. A single VPS cannot cover provider failure; off-host backups and a tested standby are required for that failure class. Never run two active executors against the same account/state as a redundancy mechanism.

## Delivered

The focused rewrite preserves the existing engines and splits the new Remote and Kalshi surfaces into dedicated components. It makes performance evidence accessible from the operational shell, simplifies workflow layout, adds typed paper control adapters, moves SSH transport into the optional executor package, and adds bounded state recovery. Deployment and validation evidence is maintained in OPERATIONS_VERIFICATION.md.
