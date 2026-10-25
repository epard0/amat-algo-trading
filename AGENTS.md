# AMAT workspace operating instructions

## Ownership and environments

- `amat/` owns research/strategy computation and immutable research evidence; `executor/` owns execution, order safety and receipts; `kalshi-perps/` owns its persistent paper accounts; `frontend/` owns the local browser UI/API and named adapters.
- Keep the Web UI and FastAPI gateway on the local workstation, bound to loopback. Remote engines are accessed through the existing pinned SSH transport and registered remote helpers.
- The approved application inventory is `amat-executor`, `kalshi-perps`, and `kalshi-perps-trade-alerts`. The optional shadow experiment is retired without deleting its account volume, protocol or results. Trade alerts watch the main account.
- New automatic health checks run only during the local API lifecycle, independent of browser visibility. Existing native watchdogs, backups and paper-session evidence collection have separate lifecycles.

## Preserve existing work

- Inspect Git status before changes. This checkout contains substantial modified and untracked source; a clean worktree from HEAD may omit the application. Preserve a source/hash baseline including untracked files before substantial edits.
- Never reset, clean, overwrite unrelated changes, or deploy a blanket copy of this checkout. Use selective release manifests and compare with the actual deployed source/configuration/image baseline.
- Assign one owner at a time to shared files (`App.tsx`, shared styles/renderers, remote helper/collector contracts). Parallel agents should own disjoint components/tests and coordinate interfaces before integration. Remote deployment has one sequential owner.

## Execution and observations

- Keep the server's exact-input/session/revision/expiring preview and idempotency contracts even when changing preview presentation. Use registered executable capabilities, independent workers and existing event streaming; the browser must not supply arbitrary shell commands.
- Preserve native order ownership, interlocks, paper/live boundaries, account binding, uncertain-outcome recovery and singleton writers. Monitoring is read-only and must not initiate lifecycle or order actions.
- Keep transport availability, process responsiveness, data freshness, reconciliation/readiness and resource pressure distinct. Missing data stays unknown; retain dated last-success observations with stale labels.
- Load private SSH configuration through existing permission-checked code; verify pinned host keys, bound time/output, and redact secrets. Never print environment dictionaries, credentials or private configuration contents.

## Resource and archive correctness

- Measure memory/CPU and aggregate host headroom; container limits are ceilings rather than capacity guarantees. Preserve strategy history/indicator seeding and account/event ordering when optimizing memory. Prove equivalent signals/accounting with regressions, including late funding and restart recovery.
- Compress only allowlisted completed inactive generated detail after seven days, with checksums/manifests, atomic publication, archive-aware readers and bounded low-priority work. Retain compressed archives and summaries; do not add automatic archive deletion.
- Exclude active databases/WALs, order/funding deduplication, receipts, unknown outcomes, interlocks, equity history, checkpoints, authorizations and immutable evidence from generic cleanup. Preserve retired shadow state.
- Coordinate SQLite online backup, source/transfer limits, temporary disk space and isolated restore verification. Rollback code/images without replacing newer account state or accepted-order receipts.

## Verification and release

- UI build: `npm run build --prefix frontend/web`.
- API/launcher regressions: `uv run --project frontend pytest frontend/server/tests frontend/tests -q`.
- Run affected executor/Kalshi tests, lint/type/format gates and full applicable package regressions after integration. Use isolated temporary state and mocked broker/notification boundaries; browser smoke actions must be inert (for example config validation).
- Check each table/record wrapper as well as document overflow at desktop/tablet/mobile widths. Keep meaningful labels/values readable, selectors keyboard-accessible and output escaped/redacted.
- When updating the installed paper-session collector, update the top-level `manifest.json["files"]` hash map for every changed/new helper before starting the collector; auxiliary release metadata does not replace this enforced attestation. Keep unchanged source attestations intact.
- Deploy remote-relevant changes after local gates, with fresh backup/restore evidence, image/source/config/volume identity, no overlapping writers, and post-release advancing ordinary observations. Verify no remote UI process/listener and the intended three-container inventory.
- Document actual tests, baseline failures, deployment identity, continuity/rollback evidence and measured limits. Never present old receipts or fixtures as new live verification.

The approved implementation contract is `docs/ui-remote-operations-plan-2026-10-23.md`.
