# Remote executor diagnostics

The terminal can inspect the actual VPS over SSH, independently of its local archived state. It uses Paramiko with the configured private key, a key-decryption passphrase, and a verified OpenSSH host key. It does not fall back to agent keys or password login. Automatic checks are owned by the local API lifecycle and use cached observations independently of browser visibility. It does not restart or trade. The shared transport lives in the optional `executor.remote` module.

## Configuration

See the complete [private configuration guide](../../docs/remote-configuration.md). Host, port, username, key path and known-hosts path are mandatory private settings. The loader has no operator-specific fallback; a missing/incomplete file blocks SSH. The public template is illustrative only.

Create a private local configuration at `frontend/.runtime/remote.toml` from the placeholder template, using your own infrastructure:

```toml
host = "your-vps.example.com"
port = 22
username = "your-operator"
key_path = "~/.ssh/amat-vps"
key_passphrase = ""
known_hosts = "~/.ssh/known_hosts"
container = "amat-executor"
state_root = "/var/lib/amat-executor"
docker_command = ["docker"]
connect_timeout = 10
command_timeout = 20
max_output_bytes = 131072
```

Enter the key's decryption passphrase in `key_passphrase`. This is the passphrase for the encrypted private key, not the remote user's login password. Use valid TOML string escaping if the passphrase contains quotes or backslashes. This file must be owned by the local user and have mode `600`. The runtime folder is Git-ignored. The API/UI never returns the passphrase or key contents, and configuration errors do not echo the file contents.

Configuration is reloaded on every run, so entering or changing the passphrase does not require a service restart. Set `TERMINAL_REMOTE_CONFIG=/absolute/path/private.toml` before starting the API to use another private config file. The tracked `config/remote.example.toml` is a secret-free template; put the real passphrase only in the private file.

## Trust the VPS identity once

The runner rejects unknown or changed SSH host keys. Establish the trusted host entry with OpenSSH if it is not already present:

```bash
ssh -p 22 -i ~/.ssh/amat-vps your-operator@your-vps.example.com true
```

Compare the presented host fingerprint with the VPS's fingerprint obtained through its trusted console before accepting it. OpenSSH stores the verified entry as `your-vps.example.com` in `~/.ssh/known_hosts`. The runner never silently accepts a new key. This authentication behavior follows the [Paramiko client documentation](https://docs.paramiko.org/en/stable/api/client.html).

## Run from the terminal

Open **Remote → Check now** to wake the API-owned collector. Automatic scheduling continues while the API runs, independently of tab visibility. Select a single diagnostic or run the complete suite. The button starts a background check, and the page updates its result on its normal observation refresh. Remote check/command start and finish events appear in the persistent event tray.

Each result includes the remote check name, status, numeric exit code, duration, observation timestamp, stdout, stderr, parsed JSON when available, and a truncation indicator. Select a row to inspect the result. A failed connection is reported separately from an unhealthy executor. A readiness failure can be an intentional dry-run/disabled/interlock state; its native reasons remain visible. A successful SSH connection alone does not mean the executor is healthy.

Remote results remain separate from local account and health views. Always inspect the remote result's timestamp. The most recent complete suite is retained separately when a later connection fails. API restart interrupts an unfinished diagnostic run, which is labeled interrupted; it is not automatically retried.

| Check | Remote observation |
|---|---|
| `host` | Host identity, clock, uptime, load averages, memory and root-disk capacity. |
| `container` | Running/status, image, restart count and Docker health state; excludes container environment/credentials. |
| `resources` | One Docker CPU/memory/network/block-I/O/PID sample. |
| `health` | Native `executor health`: durable daemon progress and submission interlock. |
| `readiness` | Native `executor health --ready`: local configuration/readiness issues; does not place orders or contact the broker. |
| `status` | Native `executor status --json`: observed order registry, portfolio reconciliation and operational state. |
| `performance` | Latest persisted report statistics/warnings and last 120 equity observations, with their source dates. |
| `logs` | Last 100 persisted executor journal entries, with source timestamps; these can be historical even when the daemon is healthy. |

The default checks do not submit orders, refresh broker state, send notifications, clear interlocks, or restart processes. Persisted account/performance observations can still be stale even when SSH and the process are healthy.

`your-operator` needs permission to use Docker. If the VPS uses a reviewed noninteractive sudo rule instead of Docker-group access, configure `docker_command = ["sudo", "-n", "docker"]`. The runner does not send a sudo password or request a PTY. Missing permissions are returned as failed checks with stderr. Update `container`/`state_root` if the deployment uses different names.

## Command line and API

From the workspace root:

```bash
./frontend/scripts/remote-check --list
./frontend/scripts/remote-check --check health --check status
cd frontend
uv run python -m server.remote --list
uv run python -m server.remote
uv run python -m server.remote --check health --check status --check performance
```

The executable `frontend/scripts/remote-check` also works from another working directory. It uses the frontend virtual environment when installed, or `uv run` otherwise. Both entry points support the same arguments and JSON exit results. The Operations panel is available in both the WebUI and the Electron shell because they use the same authenticated API. To use the production server in Electron, launch with `AMAT_TERMINAL_URL=http://127.0.0.1:8765 npm start` from `frontend/desktop` after starting the terminal service.

`--config /absolute/path/private.toml` selects another file. Exit status is 0 for passing, non-truncated checks and 1 for connection/check failures or incomplete results. CLI results are JSON. Timeouts close the SSH channel and return a timed-out result; SSH channel closure cannot guarantee termination of an arbitrary custom remote process.

Authenticated endpoints:

- `GET /api/v1/remote`: public connection metadata, registered check names, running state and last results. No SSH connection is made by this GET.
- `POST /api/v1/remote/run`: `{}` runs the built-in suite; `{"checks":["health","status"]}` runs selected named checks. Returns 202 with run ID. The existing local session/CSRF boundary applies.

You may register additional operator-owned diagnostic commands in the private config:

```toml
[commands]
disk = "df -h /"
```

Only registered names can be requested by the browser. No endpoint accepts arbitrary shell text, executable paths, environment dictionaries or SSH targets. Custom commands are trusted local operator configuration; they should remain bounded read-only diagnostics. They cannot replace built-in checks, and at most eight checks run per request. Commands use a single connection with individual wall-clock/output limits and no automatic retries.

## Historical verification

Earlier operational receipts describe observations made on October 20, 2026.
They do not configure or verify your host, establish independent fingerprint
verification, or certify current engine state. Keep actual host identifiers and
connection receipts in private operations evidence. Verify your server identity
through a trusted independent channel before first use.

## API-owned automatic health checks · October 23, 2026

Automatic checks run in the local FastAPI process when `TERMINAL_PERPS_TARGET=remote`.
They begin with the API lifespan and stop scheduling at shutdown. Opening, closing,
or switching browser tabs does not start SSH work. The Remote workspace polls cached
observations every five seconds and offers **Check now** to wake the same single
collector. The existing VPS paper-session timer, native watchdogs, trade alerts and
backup schedules keep their independent lifecycles.

The new fixed helper operation is `amat-vps-control health`. Its versioned
`remote-health/1` response declares `execution_authority=none`. The registered
`health_summary` diagnostic invokes it with `python3 -I` under a 30-second remote deadline, with
pinned SSH transport, redacted output and a 35-second client command deadline.
Deploy the updated helper before restarting the updated local API. An old helper
produces an explicit unavailable observation; it does not trigger a fallback or a
lifecycle operation.

The normal interval is 60 seconds plus up to five seconds of jitter. Transport
failures use capped backoff (120, 240, then 300 seconds). Cached GET requests never
connect to SSH. Last-success measurements retain their original timestamp; more
than 150 seconds old, future-dated, or unavailable measurements are marked stale.
Only issue transitions and recoveries create health events. No new notification
channel, automatic restart, or broker/order operation is introduced.

Observations distinguish host availability, native process/data health, executor
readiness, resource pressure, backup age and collector freshness. The permitted
running application inventory is `amat-executor`, `kalshi-perps`, and
`kalshi-perps-trade-alerts`. Retained stopped containers are listed as inactive;
unexpected running containers, UI processes/services and application listeners
require attention. This application inventory does not prohibit SSH, Docker, DNS
or ordinary OS services. Probe failures remain unknown rather than reporting a
healthy system.

Host counters include available/total physical RAM, used/total swap, memory
pressure, CPU count/load, disk space and inode availability. Container observations
include current memory/CPU/PIDs, configured resource ceilings, image identity,
health, restarts and OOM flags. Resource ceilings are not reservations or proof that
aggregate peak consumption fits physical RAM. Completed executor/perps backups
older than eight hours and paper-session collector progress older than three
minutes are flagged separately.

Extended read-only `status`, `performance` and recent-activity diagnostics run no
more often than every five minutes automatically. Optional diagnostics defer when
available RAM is below the greater of 128 MiB or 15% of host RAM, one-minute load
exceeds 1.5 times the CPU count, or swap usage reaches 75%. An explicit extended
check remains available. Health observations report low headroom at the greater
of 96 MiB or 10% of physical RAM; disk below 10%, free inodes below 5%, and container
memory at 85% of its limit also require attention. No diagnostic overrides an
intentional operator stop or starts a transient engine container.

Authenticated cache endpoints are `GET /api/v1/remote/health` and
`GET /api/v1/remote/engines`. `POST /api/v1/remote/health/refresh` uses the existing
session/CSRF protections, returns HTTP 202, and wakes the API-owned collector.
Extended diagnostics retain `POST /api/v1/remote/run` and their existing bounded,
registered-name contract. On API shutdown an already-running bounded read-only
probe may finish; the scheduler starts no further checks and launches no detached
process.

Regression coverage is in `server/tests/test_health_scheduler.py`,
`server/tests/test_health_lifecycle.py`, the existing remote/cache tests, and
`executor/tests/test_vps_control.py`. It covers cadence/backoff, shutdown and
single-flight collection, cache-only reads, stale/unknown data, resource deferral,
inventory checks, paper-mode/authority rejection, session/CSRF boundaries, and
absence of order/lifecycle operations from monitoring.

A successful host check does not make every engine measurement current. When
Kalshi is stopped or its native status cannot be read, the shared cache preserves
its last account/position measurements with their original `observed_at` and an
engine-level `stale=true` marker. New container status is still displayed. Previous
native health/readiness is not merged into that stopped-engine result.
