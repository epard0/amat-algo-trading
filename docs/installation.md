# Installation and setup

These instructions start a fresh local workstation. They do not import another operator's accounts, credentials, datasets or remote deployment. Run commands from the repository root unless a step explicitly changes directories.

## Prerequisites

- Linux is the reference environment for the shell launchers, service templates and deployment helpers. Python packages require Python 3.12 or newer. Other platforms require their own launcher/service verification.
- Install Git, `uv`, and Node.js 22 LTS with npm. Check `git --version`, `uv --version`, `node --version` and `npm --version`.
- Docker Compose and OpenSSH are optional for remote engine operation; neither is needed merely to build the terminal.
- Allocate storage for environments separately from market snapshots and research output. Download sizes depend on the requested universe, history and entitlement. Source checkout size does not estimate research disk usage.

Clone your repository URL, then enter its root. Keep `amat`, `executor`, `kalshi-perps` and `frontend` as siblings. Do not flatten the packages or copy only the top-level tracked files from an old checkout.

## Install locked dependencies

```sh
uv sync --project amat --locked
uv sync --project executor --locked --extra remote
uv sync --project kalshi-perps --locked
uv sync --project frontend --locked
npm ci --prefix frontend/web
```

These create independent `.venv` directories. `--locked` rejects an inconsistent lockfile rather than silently changing the dependency graph. Do not resolve a lock mismatch by deleting the lockfile. Review the changed dependency declaration and deliberately regenerate the affected lockfile if necessary.

The executor core uses the standard library; its optional `remote` extra supplies SSH support. Frontend's dependency on `amat-executor[remote]` is resolved from `../executor`. Kalshi strategy evaluation also requires the AMAT environment at the interpreter path in its configuration.

Check native command availability without running engines:

```sh
uv run --project amat amat --help
uv run --project amat autoresearch --help
uv run --project executor executor --help
(cd kalshi-perps && uv run kalshi-perps --help)
frontend/.venv/bin/python frontend/scripts/doctor.py
```

The doctor checks interpreter, server modules, Node/npm and installed web dependencies. It does not certify datasets, remote setup, account readiness or engine health.

## Start the local terminal

```sh
frontend/.venv/bin/python frontend/scripts/start.py
```

Visit [127.0.0.1:5173](http://127.0.0.1:5173). The launcher starts Vite and the API at `127.0.0.1:8765`, waits for API liveness and stops its children on exit. If those ports are occupied, stop your own previous launcher or use the documented `--api-port` / `--web-port` options after checking `--help`. Do not kill an unknown process.

For a single local production server:

```sh
npm run build --prefix frontend/web
frontend/.venv/bin/python frontend/scripts/start.py --production
```

Visit [127.0.0.1:8765](http://127.0.0.1:8765). Production mode requires `frontend/web/dist/index.html`. Keep the bind address on loopback. The user-service template at `frontend/deploy/amat-terminal.service` requires path review and selects remote engines; install it only after completing remote setup. The optional Electron shell has its own dependencies and packaging verification; browser setup does not verify Electron.

## Configure only the integrations you need

Follow [configuration](configuration.md) for your own keys. You can inspect source, validate configurations, run isolated tests and build the UI without trading credentials. Real provider reads, model calls and notifications require the corresponding account, quota and entitlement. Starting a research campaign can incur model/data costs.

For research, create an ignored `amat/.env` from the example and explicitly load it:

```sh
cp -n amat/.env.example amat/.env
chmod 600 amat/.env
# Edit amat/.env locally, then:
uv run --project amat --env-file amat/.env amat --help
```

The root `./research` wrapper changes into `amat/` and uses its `.env` through `UV_ENV_FILE` when no override was supplied. Ordinary `uv run` commands should use explicit `--env-file` or an already configured process environment. Empty provider keys in the template are placeholders, not functioning credentials.

## Obtain research data

Source publication excludes `amat/data` and `amat/outputs`. You must ingest licensed data or restore your own verified immutable snapshots. Discover supported ingestion flags with:

```sh
uv run --project amat amat --help
(cd amat && uv run python scripts/ingest_market_data.py --help)
```

Then use the [data guide](data-and-evidence.md) and [native ingestion guide](../amat/docs/market-data-and-simulation.md). Historical commands naming a dated snapshot are examples of an existing local artifact, not a dataset included in Git. Preserve provider identity, adjustment policy, timezone, coverage and checksums. Validate configurations before expensive studies:

```sh
(cd amat && uv run amat validate-config configs/strategy/amat_group_selection_volatility.toml)
./research run 5 --plan-only
```

A plan-only campaign inspection may still require configured workflow inputs; it does not supply missing datasets. Start a new output directory for a real study. Do not reuse a historical evidence directory.

## Set up paper engines separately

For Alpaca, copy `executor/.env.example` to ignored `executor/.env` without overwriting an existing file. Supply dedicated paper and market-data keys, a real snapshot path, the strategy hash and independent risk caps. Keep `DRY_RUN=true` and `EXECUTOR_DISABLED=true` during setup. Read [executor setup](components/executor.md) before starting a daemon.

For Kalshi, install both Python environments, review `configs/paper.toml`, and inspect configuration from its package directory:

```sh
(cd kalshi-perps && uv run kalshi-perps status)
```

Status may report that no account exists yet. `run` starts a persistent paper experiment and fetches real public data; it is not an installation smoke test. Review source hashes, history coverage and account identity before authorizing that operation. Never run local and remote writers against the same account state.

## Optional remote setup

Copy the SSH template to private local runtime storage:

```sh
mkdir -p frontend/.runtime
cp -n frontend/config/remote.example.toml frontend/.runtime/remote.toml
chmod 600 frontend/.runtime/remote.toml
```

Edit the host, port, operator, private-key path and known-hosts path for your infrastructure. Verify the server fingerprint through an independent trusted channel before accepting it into `known_hosts`. Do not disable host-key verification. Then start the terminal with `TERMINAL_PERPS_TARGET=remote`; this enables API-owned read-only collection through the registered helper. Remote helpers, containers and backups must already be installed and compatible. See [remote diagnostics](../frontend/docs/REMOTE.md) and [operations](operations.md).

A fresh source clone intentionally lacks original research output archives. Evidence-dependent checks report explicit skips; ordinary source, API, safety and synthetic tests remain available. Restoring an evidence directory re-enables its integrity checks.

Complete [verification](testing.md) before using a modified checkout. Preparation for GitHub ends with [publication checks](github-publication.md); installation does not upload or deploy anything.
