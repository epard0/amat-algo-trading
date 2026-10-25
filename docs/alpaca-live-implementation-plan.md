# Alpaca live executor implementation plan

Date: October 22, 2026. Scope: implement and test locally; deployment and real-money order submission are excluded.

## Intended result

Run the existing paper executor (`amat-executor`) and a separate live executor (`amat-executor-live`) on the same server. Both use the AMAT plan artifact contract and the same durable execution safeguards. The dashboard identifies each account and execution environment separately. Paper remains the default. Normal Compose startup excludes the live service.

## Architecture and decisions

| Concern | Paper | Live |
| --- | --- | --- |
| Compose service | `executor` | `executor-live`, profile `live` |
| Container | `amat-executor` | `amat-executor-live` |
| Trading host | `paper-api.alpaca.markets` | `api.alpaca.markets` |
| Credentials | `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET` | `ALPACA_LIVE_API_KEY`, `ALPACA_LIVE_API_SECRET` |
| Environment file | `.env` | `.env.live` |
| State volume | `executor-state` | `executor-live-state` |
| State directory | `/var/lib/amat-executor` | `/var/lib/amat-executor-live` |
| Strategy environment/cache | Existing paper volumes | Separate live volumes |
| Dashboard | Existing paper controls and observations | Read-only live observations |

Use the same Dockerfile with distinct image tags, containers and volumes. Mount the strategy checkout read-only into each container. Do not share mutable virtual environments, journals, submission receipts, account baselines, STOP_SUBMISSIONS files, heartbeat files, locks, plans, reports or deployment registries.

Alpaca documents separate trading domains and credentials for paper and live: [authentication](https://docs.alpaca.markets/us/v1.4.2/docs/authentication). Its [paper trading documentation](https://docs.alpaca.markets/us/docs/paper-trading) explains simulation limitations. Live uses the same supported order protocol and execution windows as the current executor; this change adds no options, crypto, short-selling, overnight or extended-hours strategy support.

## Implementation sequence and completion criteria

### 1. Explicit broker selection — implemented

- Add `TRADING_MODE=paper|live`, defaulting to paper. Reject unknown values.
- Keep each host pinned in code. Confine the live hostname to the live adapter. Do not accept arbitrary broker URLs.
- Route daemon and every broker-using CLI command through the same client factory.
- Reuse the existing broker parsers, calendar, positions, activity pagination, request throttling and uncertain-order classification.
- Reject HTTP redirects for trading requests so credentials and order bodies cannot be forwarded to another host.
- Preserve the subprocess boundary: only runtime plumbing and market-data credentials reach `amat plan`; trading credentials and live account identity do not.

### 2. Live authorization and state isolation — implemented

- Require separate live credentials and `ALPACA_LIVE_ACCOUNT_NUMBER`; never fall back to paper credentials.
- Verify the returned account matches the configured identity, including immediately before every order POST; reject blocked accounts.
- Require finite positive session notional, per-symbol notional and order-count limits. Operators must size these for the funded account; no invented live capital limits are supplied.
- Require `LIVE_TRADING_ENABLED=true`, `DRY_RUN=false` and `EXECUTOR_DISABLED=false` before live order transport is available. Existing runtime interlocks still apply.
- Persist a mode binding under a process lock. Reject paper/live reuse of a bound state root. Reject adoption of existing unlabelled evidence into live mode.
- Retain account binding, reconciled portfolio freshness, authorized strategy digests, exchange clock/windows, equity drift checks, durable pre-POST receipts, bounded attempts, singleton daemon ownership, watchdog and STOP_SUBMISSIONS behavior.
- Never blindly retry an ambiguous POST. Resolve the original client order identity using the existing reconciliation workflow before considering further action.

### 3. Separate container on the paper server — implemented, not deployed

- Define the profile-gated live service and distinct live image/container identity.
- Use a separate private environment file and live-only state/cache/environment volumes.
- Retain unprivileged execution, read-only root filesystem, dropped capabilities, no-new-privileges, bounded logging, tmpfs, health checks, resource limits and graceful shutdown.
- Supply `.env.live.example` with dry run enabled, executor disabled and live authorization disabled. Missing required account, strategy digest and finite caps fail closed.
- Default budgets are 640 MiB RAM, 1 GiB RAM/swap and one CPU **per executor**. On a host running both executors plus Kalshi and the dashboard, capacity must cover aggregate usage and concurrent planning. No assertion of spare server capacity is made by local testing.

### 4. Dashboard and operational evidence — implemented locally

- Add a distinct “AMAT · Alpaca live · Real money” row, showing the fixed container identity, observed deployment state, timestamp, dry-run/disabled/live-enabled switches, readiness failures, and expandable native account/order observations.
- Extend the VPS overview helper with read-only live inspection, readiness and native status retrieval. A missing container reports `not_deployed`; permission/transport failures are not fabricated as absence.
- Keep paper and live evidence in separate JSON fields and separate state volumes. Mark cached live observations stale on SSH failure. An older remote helper yields “Not observed” until it is separately updated.
- Require readiness evidence to identify live mode. Do not offer live deploy/start/restart controls in the dashboard.
- Reject use of existing paper lifecycle controls against a container configured for live mode.
- Label live daily reports and deployment observations as live; use `live_authorized` rather than `paper_approved`. Authorization is distinct from readiness and observed fills.

The modified remote helper must be installed in a future deployment before remote live observations appear. This task does not install it or change the running dashboard/server.

### 5. Validation — completed locally

Use fake credentials and mocked transport for all trading tests. No funded account access is needed.

- Run the executor regression suite, including live mode selection, credential separation, default authorization state, finite caps, wrong/blocked account refusal, disabling gates, state isolation, redirect refusal and ambiguous POST single-attempt behavior.
- Run dashboard remote observation/control tests, including stale evidence and absent live controls.
- Run executor Ruff and strict mypy checks, and TypeScript/Vite production build.
- Parse Compose with the live profile enabled; assert distinct state volumes and explicit trading modes without creating containers.

The existing execution regression suite exercises the shared journal, reconciliation, staged batches, restart recovery, interlocks and sizing path. New live tests verify adapter routing and live authorization boundaries. Local mocks do not establish funded-account eligibility, actual fills, live slippage, broker permissions or server capacity.

## Future deployment procedure — intentionally not executed

1. Confirm a dedicated Alpaca live account, trading permissions, funded/settled buying power and strategy suitability. The planner consumes actual account holdings; unrelated discretionary positions in the same account would participate in its account-wide allocation calculations.
2. Review aggregate server RAM, swap, disk, CPU and API usage. Keep the live planning environment independent and budget concurrent market-open activity.
3. Prepare the private `.env.live` using the example, verify the expected account identity and strategy digest, choose finite capital/order caps and configure notifications. Keep all three submission gates in their disabled/default state.
4. Build the reviewed live image, initialize its own volumes, install the updated remote status helper and dashboard, and start only in observation/dry-run mode following a separate deployment instruction.
5. Verify account identity, plan sizing from live equity/positions, calendar, portfolio reconciliation, logs, dashboard mode labels, staleness, notification routing and persistent state across restart. Never seed live state by copying paper state.
6. Rehearse STOP_SUBMISSIONS and uncertain-order recovery. Stopping submissions does not liquidate existing positions or cancel broker-held orders.
7. Any later activation of funded orders requires a separate explicit operator decision and intentionally changing the three submission gates. Start with operator-selected finite caps and observe the first session. No activation, order or deployment is authorized by this implementation task.

## Local verification record

- Executor suite: 385 tests passed after the `batch.py` lint/type correction.
- Full dashboard backend suite: 105 tests passed; one dependency deprecation warning from Starlette/httpx.
- Executor strict mypy: all 30 source files passed. `BatchPolicy.payload()` now explicitly declares its mixed-value dictionary, resolving the string/boolean assignment error.
- Ruff source lint and formatting: all 30 source files passed, including `batch.py`.
- Directory-wide Ruff: 57 lint errors remain outside `batch.py`, in `scripts/backup-state.py`, `scripts/vps-control.py`, and tests. Directory-wide formatting reports four files: `scripts/vps-control.py`, `tests/test_batch.py`, `tests/test_config.py`, and `tests/test_state_snapshot.py`. These broader issues were reported without changing those files.
- `git diff --check`: passed.
- Dashboard TypeScript/Vite production build: passed.
- Compose profile schema/configuration: passed without environment-file expansion or container startup.

These checks validate implementation locally. Deployment, funded-account connectivity and real-money orders remain unperformed.
