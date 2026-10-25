# P13 Docker paper integration audit — research only

This audit used read-only Docker metadata, an explicit nonsecret environment-variable allowlist, selected fields from existing local JSON artifacts, and source inspection. It did not call broker APIs, run executor commands, refresh monitoring, create plans, submit orders, restart containers, or change deployment configuration. No account identifiers, credentials, balances, or holdings are reproduced here.

## Observed deployment

`amat-executor` was running and Docker reported `healthy`, using image `amat-executor:phase-b-20260920`, with approximately two days of uptime. Docker health is not a research or execution-quality result: the compose healthcheck invokes the account connectivity command [docker-compose.yml](/home/supasuge/Documents/amat-workspace/executor/docker-compose.yml:67).

The process has both `EXECUTOR_DISABLED=true` and `DRY_RUN=true`. Its mounted strategy is `/opt/amat/configs/strategy/amat_v13_return_target.toml`; its base snapshot is `/opt/amat/data/snapshots/alpaca-sip-core-2026-09-19-verified`. The strategy checkout is a read-only bind mount of `/home/supasuge/Documents/amat-workspace/amat`; persistent executor state is in the `executor_executor-state` volume at `/var/lib/amat-executor` inside the container. The executor source is in the image, whereas strategy code is supplied through the mount: the image tag alone does not identify all executing strategy code.

SHA-256 hashes of the deployed `plan_source.py`, `config.py`, and `daemon.py` matched their corresponding local executor files. This confirms those inspected interfaces, not an exhaustive deployment attestation.

Latest archived plan, `plans/2026-09-29.json`:

- Plan schema `1.0.0`, engine `v1-frozen`.
- Strategy `amat-return-target-growth_rotation4_20_weekly`, version `13.0.0`.
- Generated `2026-09-29T13:20:04.954125+00:00`, using closes through `2026-09-28`.
- Earliest execution session `2026-09-29`; opening window `09:30–09:35 America/New_York`.
- Base snapshot identifier remains `alpaca-sip-core-2026-09-19-verified`; six completed sessions were appended for planning. This is not the same data identity as the P11/P12 reconciled research snapshot.

Latest inspected operations observation, `2026-09-29T23:21:41.777886-04:00`:

- Config authorized; deployment label `paper_approved`; submission mode `disabled`.
- `matched_fill_observed=false`, monitoring mode `shadow`, `research_validation=not_imported`, and execution authority `none`.
- Zero active incidents and zero omitted flags.
- Operational observation window: **1 of 20 required sessions**, status `collecting`; at least 1,800 observed healthy seconds are required per counted session.
- Statistical monitoring: `not_ready_no_frozen_performance_baseline`.
- Portfolio monitor at that same timestamp: `reconciled`, exact match true, scoped to cash/share changes since the observed baseline.

The archived daily report at `2026-09-29T20:15:03.501772+00:00` recorded seven planned instructions, zero broker orders, zero fills, and `executed=false`. Its execution and portfolio reconciliation flags were false. The later reconciled portfolio observation is a different timestamp and scope; it does not retroactively prove that the earlier report's plan executed or reconciled.

`paper_approved` means membership in the configured digest allowlist when no matched fill or active incident changes that state. The operations implementation explicitly reports `research_validation=not_imported` [operations.py](/home/supasuge/Documents/amat-workspace/executor/src/executor/operations.py:335). The 20-session counter is explicitly `operational_reliability_only`, with no execution authority [observation_window.py](/home/supasuge/Documents/amat-workspace/executor/src/executor/observation_window.py:44). These labels must not be presented as successful P11/P12 validation or paper trading performance.

## What the current planning boundary supports

The executor forms a subprocess command for `amat plan` using the configured strategy, snapshot, supplied current equity and share counts, and optional news-assets artifact [plan_source.py](/home/supasuge/Documents/amat-workspace/executor/src/executor/plan_source.py:241). The executor owns account access; the strategy receives supplied state. This separation is useful and should remain intact.

The strategy planner computes ordinary causal core inputs and calls the allocator once at the latest completed session [planning/__init__.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/planning/__init__.py:91). It supports configuration-driven core selection/allocation, current-position sizing, and forced-zero news-universe retirements. Its sole declared engine is `v1-frozen` [planning/__init__.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/planning/__init__.py:43).

The current boundary does **not** consume a P11 recipe artifact, fold eligibility receipt, genetic expression/MTF score bank, five-timeframe coefficients, risk-scalar overlay, take-profit/stop overlay, annual recipe schedule, entry-basis history, or embargo state. Loading a v13 research configuration does not add these omitted arguments. The CLI explicitly notes that runner overlays are not passed into planning [main.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/cli/main.py:3717).

The executor parses plan major version 1 and keeps additional raw fields, but does not thereby enforce the provenance or state semantics of arbitrary new fields [plan_source.py](/home/supasuge/Documents/amat-workspace/executor/src/executor/plan_source.py:143). Simply adding research JSON metadata or changing a strategy TOML would not establish parity with research.

## Proposed research-to-paper gates

1. **Single-mechanism evidence.** Use the expanded full-available-history, regime/scenario, cost and continuous-account studies to narrow a predeclared mechanism. Preserve all evaluations and uncertainty. P11/P12 findings alone did not identify a consistently superior replacement. Synthetic results should have separate source/scenario labels and should never be mixed into operational observation counts.
2. **One shared decision implementation.** Factor or extend the authoritative decision construction so offline replay and plan generation use the same validated candidate configuration, MTF feature definitions/alignment, scalar and exit logic. Do not implement a separate approximation inside the executor. Preserve its subprocess boundary.
3. **Point-in-time data parity.** Compare research and read-only plans at identical decision cutoffs and input identities. Native 15-minute/hourly data need completed-bar and exchange-session alignment, gap/availability neutrality, immutable source hashes, and a declared fetch/cache contract. Base snapshot identifiers and appended-session counts alone do not identify those inputs.
4. **Inherited-state reconciliation.** Start from the actual reconciled paper account and open-order registry rather than assuming an empty portfolio. Reconstruct adjusted first-entry bases from complete corporate-action-aware fills; do not substitute average broker cost or switch-day price for the research first-entry rule. Carry pending instructions and preserve recipe/embargo ownership across restarts. If basis or ownership is unavailable, declare the mechanism inapplicable to that holding or require an explicitly modeled migration policy; never fabricate history.
5. **Artifact identity and state persistence.** Bind recipe, allowed configuration, feature/data inputs, source/dependencies, selection cutoff, eligible activation time, and a versioned state digest into a plan extension whose consumer enforces them. Validate fresh/expired/replayed plans and mismatched states. Existing portfolio checkpoints reconcile cash/shares; they do not currently carry research entry bases or embargoes [portfolio_monitor.py](/home/supasuge/Documents/amat-workspace/executor/src/executor/portfolio_monitor.py:134).
6. **Read-only parity and operational evidence.** For identical supplied account state, compare target weights, withheld intents, order quantities, exit reasons and earliest execution times to the authoritative replay. Include splits/dividends, missed/partial fills, pending orders, stale bars, out-of-universe holdings and restarts. Continue disabled shadow observation while that evidence is collected; report data quality, research performance and operational reliability separately.
7. **Separate deployment decision.** A later controlled paper enablement must be a concrete reviewed change to the authorized artifact/configuration and deployed code, with rollback and position-state migration recorded. This audit and the requested research do not flip either current execution switch. Research outputs must not silently mutate the Docker-mounted planner path during an active deployment.

The endpoint host is hard-coded to the paper trading host [config.py](/home/supasuge/Documents/amat-workspace/executor/src/executor/config.py:40), and submission checks enforce the disable switch and configuration allowlist [submit.py](/home/supasuge/Documents/amat-workspace/executor/src/executor/submit.py:170). Keep those existing controls while developing any future integration.
