# Operations, backup and selective release

The UI/API stays on the workstation and binds loopback. The approved remote application inventory is `amat-executor`, `kalshi-perps`, and `kalshi-perps-trade-alerts`. Alerts observe the main account. The optional shadow experiment is retired with its original state/protocol/results retained. Source publication does not change that topology.

## Observe without acting

Use the registered pinned SSH transport and remote helpers. Keep host reachability, native process progress, fresh data, reconciliation/readiness and resource pressure as separate observations. Retain dated last-success values with stale labels when collection fails. Missing measurements stay unknown. Health monitoring never starts/stops engines, clears interlocks or places orders.

Automatic diagnostic scheduling belongs to the local API lifecycle, independently of browser visibility. Existing watchdogs, backup schedules and paper-session evidence collection keep separate lifecycles. Do not add a remote health timer to replace the local scheduler.

## Before a release

1. Capture `git status` and a source/hash baseline including untracked application source. A clean worktree from HEAD can omit substantial current components.
2. Identify actual deployed images, source/config digests, mounted volumes and active writers. Older Compose tags and historical runbooks do not establish the current deployment baseline.
3. Run local gates for affected packages, including relevant strategy/accounting parity and restart/late-funding tests.
4. Create a fresh coordinated backup, verify checksums/transfer limits, and restore into isolated temporary state. Capture source/volume identity and restore evidence. Never exercise broker/notification writes during restore verification.
5. Build a selective release manifest. Do not copy the whole working checkout, its credentials, databases or old state into a deployment.

## Backup and state continuity

Coordinate SQLite online backup, source/transfer budgets, temporary disk requirements and isolated restore verification. Copying a live database file independently of its WAL is not a substitute for a consistent backup. Preserve order/funding deduplication, accepted-order receipts, uncertain outcomes, equity, account/event ordering, checkpoints and authorization/interlock files.

The [backup contract](../executor/scripts/BACKUPS.md) describes installed mechanisms and limits. [The archive contract](../frontend/deploy/paper-monitor/ARCHIVES.md) covers completed collector detail. These are separate retention policies. Do not use repository cleanup as authorization to delete account state, archives or immutable research evidence.

## Install sequentially and verify

One owner performs remote release steps sequentially. Ensure no overlapping writer can access the same account. Update the installed paper collector's top-level `manifest.json["files"]` SHA-256 map for every changed/new helper before collector startup; preserve unchanged entries. Additional release metadata does not satisfy enforced attestation.

After release, verify advancing ordinary observations, correct source/config/image/volume identities, no remote UI process/listener, the intended three-container inventory, and absence of unexpected restarts/OOM outcomes. Measure aggregate host available memory, swap/load and disk headroom. Container limits are ceilings; their sum does not prove capacity.

A code/image rollback must keep newer account state and accepted-order receipts. Do not restore an older state snapshot over a currently advancing account. Any uncertain broker effect stays subject to native reconciliation rather than automated resubmission.

## Evidence and publication

Store real release/repair receipts privately under `operations/` or your durable evidence store. Record actual test commands/results, known baseline failures, deployment identity, backup/restore proof, continuity/rollback observations and measured limits. Never substitute an old receipt or fixture for new remote verification. A documentation-only repository cleanup requires no remote deployment.

The detailed approved [UI/remote operations design](ui-remote-operations-plan-2026-10-23.md), [executor runbook](../executor/VPS_RUNBOOK.md), and [remote diagnostics](../frontend/docs/REMOTE.md) provide context. Their dated observations must be recaptured for an actual release.
