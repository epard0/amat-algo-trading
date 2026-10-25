# Data and research-loop implementation memo

Status: source-grounded planning, October 8, 2026. No strategy, provider, broker, or runtime configuration changed. Companion to the consolidated PLAN.md and three delegated reviews.

## Current implementation and reuse

- `amat/src/newsdesk/schema.sql` and `store.py` already implement append-only news versions, distinct publication/ingestion timestamps, revision history, and point-in-time queries. `features.py:attention_scalars` exports a bounded attention-based risk multiplier; it does not provide historical earnings/macro/carry data.
- `newsdesk/features.py` intentionally uses the end of the UTC decision date, not necessarily the market close. Its documented next-session execution rationale is not permission to reuse those observations in a new close-time decision. Any new feature must bind to its actual decision timestamp.
- `amat/src/autoresearch/web_evidence.py:retrieve/admit/persist_admitted` admits bounded exact excerpts and source hashes, explicitly without asserting truth. Research citations are not a tradable numerical feature feed.
- `amat/src/amat/data/snapshot.py` freezes/checks market partitions, corporate actions and panel inputs; `autoresearch/workspace.py:initialize` copies runtime, dependencies and contracts. Extend these interfaces rather than fetching new features inside a backtest.
- `amat/src/amat/domain/instruments.py:Instrument` records a first-available session. News discovery has retirement handling. Neither is a complete historical investable-universe membership and delisting-return contract.
- `amat/src/autoresearch/controller.py` and `controller_context.py` now provide bounded inspect/run/stop decisions and development-only ledger context. Reuse them. The controller is not an isolated code evaluator.
- `amat/src/autoresearch/extensions.py` preserves the baseline signal and entry model and permits only a bounded component change. Structural alpha-review work cannot be squeezed into this ordinary extension program by weakening validation.
- `amat/src/autoresearch/engineering.py:stage` checks allowlisted source/base hashes/syntax, persists patches, and stops at `awaiting_isolated_validation`. It deliberately never executes generated code. The missing work is isolated evaluation, not another proposal generator.

## D1: Point-in-time numerical observation contract

Proposed modules: `amat/src/amat/data/observations.py`, `feature_snapshot.py`, with provider-specific ingest adapters kept outside the simulation layer. These names are proposed, not existing capabilities.

Define immutable observations with stable entity/security ID, field/unit, effective period, provider publication time, provider availability/vintage evidence, local ingestion time, revision ID, source digest and licensing/provenance reference. Define `availability_basis` explicitly:

1. Locally observed mode: numerical decisions may consume only rows ingested before the actual decision cutoff.
2. Archived-vintage research mode: a separately declared historical contract may use authenticated vendor release/vintage evidence. Today's download timestamp alone cannot establish historical availability. Mark the basis in every manifest.

Use UTC-aware timestamps, actual exchange calendars, per-field freshness limits and missingness masks. Preserve revisions as new observations. As-of joins must not substitute a later revision or silently inner-join away missing sessions. Distinguish missing data from a neutral economic value. Test publication delays, after-close events, DST, holidays, withdrawal/revision, duplicate ingestion and future-row mutation.

Freeze an exported feature snapshot before research: observation IDs, calendar, universe map, as-of policy, masks, data/source hashes and transform parameters. Integrate at the frozen worker/panel preparation boundary, not by allowing feature functions network access. Fit normalization only on each training prefix and pass exact feature snapshot identity through candidate cache and evaluation contracts.

Initial delivery should support one information family and one economic hypothesis. Earnings signals need a documented mapping to the current ETF universe or a separately registered equity universe; they are not automatically appropriate for an eleven-ETF portfolio. Macro release surprises require both actual and contemporaneous expectation data; revised macro history alone is insufficient. Provider/entitlement selection is a later procurement decision, not a reason to block implementing and testing the adapter contract with fixtures.

Acceptance: forward-mutation invariance, exact known-at joins, reproducible exports, old price-only recipes unchanged, and feature removal controls. New data availability does not automatically justify strategy promotion.

## D2: Universe membership and instruments

Propose `universe_history.py` plus immutable security identifiers and effective membership intervals. Include inception, delisting, symbol changes, cluster classification known at the time, tradability and instrument type. Implement ETF/equity long-only research first; shorting/futures require separate financing, borrow, margin/roll and executor contracts.

Integrate with `domain/instruments.py`, snapshot availability masks, `newsdesk/discovery.py`, and candidate universe validation. Keep the old static universe reproducible. Test symbol reuse, delisting proceeds, missing final prices, late classifications and unavailable instruments. Do not change an old snapshot or inject today's survivors into old dates.

## R1: Corrected evidence in research context

Use the metrics memo's shared append-only correction index and resolver. All consumers—`controller_context.read_research_ledger`, `memory`, `failures`, `summaries.evidence`, proposer and engineering context—should receive bounded resolved receipts with original ID, correction ID, evidence cutoff and digest. A historical as-of view retains what was known then; a current view displays the verified correction without rewriting the original receipt. Never leak later validation outcomes into development proposals through a correction.

Implement this before expanding automated research. Existing controller inspect/run/stop, budgets and ledger ownership remain intact. Add controller evaluation fixtures for stale control failures, conflicting corrections, unsupported IDs, duplicate hypotheses, insufficient evidence and proper abstention. Measure validity/novelty/useful experiments, not quality of prose.

## R2: Isolated engineering validation

Proposed files: `autoresearch/engineering_validation.py` (trusted coordinator and contract), a version-pinned disposable worker image/build recipe under `amat/tools/engineering-worker/`, and trusted harness tests outside candidate-editable paths. Exact directory naming can follow repository conventions at implementation time.

Input: frozen parent/runtime/data identity, allowlisted patch, declared API contract, mandatory tests and resource limits. Output: an immutable evaluation receipt binding image/dependency/patch/harness hashes, exit status, bounded logs, tests, causal invariants and matched development measurements.

The worker has no model/broker credentials, network, Docker socket or writable checkout mounts; only declared read-only inputs and a bounded output directory. Time, memory, process and disk budgets are enforced by the launcher. A timeout or crash is a durable failed attempt; retries/repairs consume a finite registered budget. Artifact readers verify hashes, size limits and expected output types. Do not import candidate Python into the trusted controller to inspect it.

State transitions: `awaiting_isolated_validation → rejected | mechanically_validated → development_evaluated`. Passing tests does not grant statistical or deployment approval. Evaluator/risk/cost changes are trusted implementation changes in a new program, never edits authorized by the feature engineer's four-module allowlist.

Tests extend `test_research_engineering.py` with hostile/invalid output paths, network/secret/mount isolation, hanging processes, resource exhaustion, corrupted receipts, syntax-valid semantic failures, causal future-data mutation and bounded retry accounting. Retain the existing tests proving staged code does not execute on the host.

## R3: Prospective observation and lineage

Reuse `genetic/shadow_artifact.py`, `shadow_campaign.py`, `shadow_ledger.py`, and executor observation/checkpoint machinery. Bind observations to exact source/model/data contracts and real registration times. Migration preserves predecessor IDs, existing suspension state and parity receipts; do not reset a suspended campaign to gain a new clean-looking history. Distinguish backfill, prospective shadow, paper fills and live fills in all reports.

This plan does not schedule observations or activate an executor. The engineering release can be completed with offline fixtures, while prospective economic qualification requires actual subsequent observations under the frozen policy.

## Dependencies and ownership

- D1 and R2 can be implemented independently once versioned contract identities are agreed.
- D2 follows D1 and the portfolio team's availability/risk interfaces.
- R1 depends on the metrics/evidence correction index and should have one owner across all consumers.
- R3 depends on executable plan/state parity and a frozen, qualified candidate.
- Numerical features depend on provider-specific historical evidence; the infrastructure and tests do not require selecting or buying a provider now.
- Shared high-conflict files (`contracts.py`, `workspace.py`, `worker.py`, `controller_context.py`, CLI and configuration models) are integrated serially under the master plan. Parallel workers own separate modules/test files until those interfaces stabilize.
