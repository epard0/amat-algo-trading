# AMAT executor: VPS handoff and unattended operation

This service operates an Alpaca **paper** account. It does not support real-money execution. Source hardening performed October 8, 2026 does not replace validation on the destination VPS.

## Deployment gates

Before allowing paper submissions, confirm all of the following:

- Only one host runs the executor for this account. A filesystem lock cannot coordinate different machines or different state directories.
- Transfer the **current working source**, including required untracked modules/configuration, not merely a clone of the last commit. Keep `amat/` and `executor/` as siblings.
- Transfer the selected immutable snapshot and its manifests, the strategy configuration, and the complete authoritative executor state. In Compose the authoritative state is the named Docker volume, not the checked-out `executor/state/` directory.
- Verify the exact selected configuration digest remains authorized. Do not authorize a changed strategy as a side effect of migration.
- Keep `DRY_RUN=true` and `EXECUTOR_DISABLED=true` during migration and validation. Both were enabled in the locally inspected deployment.
- Preserve `MAX_SESSION_NOTIONAL=unlimited`, `MAX_SYMBOL_NOTIONAL=unlimited`, and `MAX_ORDERS_PER_SESSION=unlimited`, as explicitly requested by the operator on October 8. No additional execution maximums are imposed. The other submission interlocks remain enforced.
- Configure and test Telegram operational alerts with `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, including an external dead-man check that detects an offline VPS. Daily SMTP reports alone cannot notify anyone if the machine itself is down. Mailgun integration is implemented; credentials, sending domain and recipient are required to activate it.
- Synchronize the host clock, test DNS/TLS/broker access, validate backups by restoring them to an isolated location, and perform a host reboot test with submissions disabled.

## Independent protections

| Layer | Enforcement |
|---|---|
| Venue boundary | Broker hostname is a paper-only constant; research and executor remain separate packages. |
| Planner environment | Subprocess receives an allowlist of runtime settings and market-data keys; paper keys, SMTP secrets, model keys, injected Python paths and environment-file settings are not inherited. This is environment separation, not a complete hostile-code sandbox. |
| Safe default | Missing `DRY_RUN` means true; the example configuration also starts submission-disabled. Existing explicitly configured values are preserved. |
| Runtime stop | `STATE_ROOT/STOP_SUBMISSIONS` is checked before the submission phase and between order POSTs; no restart required. |
| Durable order fence | Fsynced per-order receipt is persisted **before** POST; success, rejection and uncertainty are retained outside normal report retention. |
| Unknown outcome | Transport/JSON failures, HTTP 408/5xx, duplicate-ID ambiguity, or invalid acknowledgments halt remaining orders. Pending/unknown receipts block later sessions and survive restart. |
| Reconciliation | `reconcile-submissions` queries original client order IDs. Only matching broker evidence resolves uncertainty. A missing order is not proof that retrying is safe. |
| Idempotency | Original session plan and deterministic client IDs are reused. Completed receipts prevent another POST locally; broker IDs provide a further layer. |
| Process exclusion | One daemon per state directory; separate submission and report locks prevent concurrent local writers. |
| Schedule | Broker calendar, session, preceding exchange session and cutoff checks apply to automatic and one-shot submissions, with checks again after planning and before orders. |
| Clock and freshness | Host/broker skew over 120 seconds refuses submission. Portfolio monitoring must be reconciled and no older than 12 minutes. |
| Account identity | State binds to the observed paper account; different-account reuse fails closed. |
| Sizing | Current holdings bound sells; fresh account equity is checked after planning; non-finite/invalid sizes and duplicate-symbol intents are refused. Caps use the larger of declared notional and quantity × reference price. |
| Failed order | A definite rejection stops the remaining plan, preventing follow-on buys after a rejected sell. |
| Storage | Less than 64 MiB free at the state root blocks new submissions. Critical records are atomically replaced with file and directory fsync. Corrupt scheduler/receipt state is not silently discarded. |
| Bounded retries | Phase attempt counts and backoff survive restarts; capped retries do not reset every time the process restarts. |
| Loop watchdog | A monotonic watchdog exits after 30 minutes without a successfully completed tick; Docker's restart policy then recovers the process. Durable receipts protect uncertain in-flight orders. |
| Health reporting | `executor health` checks durable loop progress; it does not equate successful account connectivity with a working scheduler. |
| Container containment | Unprivileged process, read-only root filesystem and strategy mount, all Linux capabilities dropped, no-new-privileges, bounded processes/memory/CPU, bounded Docker logs, writable named state/cache volumes. |

Limits apply to reference notional, not a guaranteed fill price; market-order slippage remains possible. A runtime stop prevents later requests; it does **not** cancel accepted orders or liquidate positions. A request already in flight may still reach the broker. Monitoring and observation-window completion do not establish strategy profitability.

## Prepare the VPS

Recommended starting capacity for the supplied Compose limits: at least 2 vCPU and 8 GB RAM, SSD space sufficient for the actual selected snapshot, source, Python environment, retained state and backups. This is a starting deployment allocation, not a benchmark guarantee. Measure planning duration and memory on the target host before enabling orders.

Install Docker Engine/Compose, enable Docker at boot, and enable the host's NTP service. Verify SSH access, firewall rules and OS security updates. No inbound application port is published by this service.

On a systemd host, useful validation commands are:

```bash
sudo systemctl enable --now docker
timedatectl status
docker version
docker compose version
df -h
df -i
```

Use a dedicated operator/deployment directory such as `/opt/amat-workspace`. Keep private environment files readable only by the intended administrator (`chmod 600 executor/.env`). Do not put keys in command arguments, Git, images, public archives, or logs. Do not transfer unrelated `.KEYS`, model credentials, research outputs or credential directories into the read-only AMAT mount just because they are present in the working folder.

Transfer the runtime source/configuration and required data through an authenticated channel. Preserve the complete selected snapshot, including its manifest and every referenced data file. The selected snapshot path must exist under the `/opt/amat` mount inside the container.

## Stop-and-copy handoff

First set the runtime stop on the **old** deployment and deliberately stop it. This leaves outstanding broker orders untouched; inspect them before handoff.

```bash
cd /path/to/amat-workspace/executor
docker compose exec -T executor touch /var/lib/amat-executor/STOP_SUBMISSIONS
docker compose stop executor
```

Do not use `docker compose down -v`: it deletes named state volumes. Do not start the VPS daemon before verifying the old host remains stopped. `unless-stopped` respects a deliberate stop; also ensure no separate host automation will bring it back up.

Identify the real state volume rather than guessing its Compose prefix:

```bash
docker inspect amat-executor --format '{{range .Mounts}}{{if eq .Destination "/var/lib/amat-executor"}}{{.Name}}{{end}}{{end}}'
```

With the daemon stopped, use an administrator-owned backup directory and substitute the verified volume name in this example:

```bash
mkdir -p /secure/amat-backups
chmod 700 /secure/amat-backups
docker run --rm --user 0 --read-only \
  -v VERIFIED_STATE_VOLUME:/state:ro \
  -v /secure/amat-backups:/backup \
  --entrypoint tar amat-executor:vps-20261008 \
  -czf /backup/executor-state.tgz -C /state .
sha256sum /secure/amat-backups/executor-state.tgz
```

Preserve all files, especially `daemon_state.json`, archived plans, journal/equity history, `submissions/`, `account-identity.json`, portfolio baselines/checkpoints, operations SQLite data and `STOP_SUBMISSIONS`. Do not copy just the reports. Protect the archive as account-sensitive data and retain an off-host backup.

On the VPS:

1. Verify transferred checksums and configuration/snapshot paths.
2. Prepare `.env` with explicit disabled/dry-run settings and the same authorized strategy digest.
3. Run `docker compose config --quiet` and `docker compose build executor`.
4. Run `docker compose create executor` to initialize volumes without starting a daemon.
5. Find the destination state volume with the same `docker inspect` command.
6. Restore the archive into that volume using a temporary container and `tar -xzf`; retain UID/GID ownership (the executor uses UID 10001). Restore only trusted archives into an empty destination volume.
7. Confirm the runtime stop file exists, the restored state is readable/writable by UID 10001, and the source host is stopped.

The AMAT virtual environment and uv wheel cache can be recreated. Authoritative execution state must be restored, not recreated as empty.

## Validate without submitting

From the VPS `executor/` directory:

```bash
docker compose config --quiet
docker compose run --rm --no-deps executor executor check
docker compose up -d executor
docker compose exec -T executor executor health
docker compose exec -T executor executor status --json
docker compose exec -T executor executor health --ready
```

`check` contacts the paper account and market-data APIs and builds an inert plan. It never submits orders. `health --ready` is expected to fail while stopped/disabled/dry-run, or without a fresh reconciliation. Missing notification configuration produces a warning only. It does not enable anything. A healthy Docker status is process health, not permission to trade.

On a new monitoring baseline, allow a complete subsequent observation cycle before expecting `reconciled`; the initial `baseline_recorded` state is not enough. Older schema/unsupported corporate actions may produce `observation_unavailable` and require investigation.

Test `docker compose restart executor` while still disabled, verify state/receipts and fresh monitoring survive, then perform a VPS reboot test. Verify the image digest and effective configuration after reboot. Test the external alert by stopping the monitored heartbeat/service deliberately while disabled and confirm the intended recipient receives it. Nothing in this repository configures an external monitoring provider automatically.

Enabling paper submissions is a separate deliberate operator action after the checks: preserve the requested unlimited caps, set `DRY_RUN=false` and `EXECUTOR_DISABLED=false`, remove the runtime stop only after reviewing any outstanding orders, recreate the service to apply environment changes, and inspect readiness again. Never run two hosts during this step. Removing a stop file does not automatically rerun a scheduler phase already settled as refused that day.

## Daily and incident operations

```bash
docker compose ps
docker compose exec -T executor executor health
docker compose exec -T executor executor status --json
docker compose logs --tail 100 executor
```

Use a host timer or external monitor to check the process health **and** freshness/portfolio/incident state. A process may be healthy while submissions are intentionally disabled. Alert separately on missing progress, stale observations, reconciliation discrepancies, exhausted phases, storage pressure, and unexpected changes to submission mode.

Emergency stop of future submissions:

```bash
docker compose exec -T executor touch /var/lib/amat-executor/STOP_SUBMISSIONS
```

Uncertain order outcome:

```bash
docker compose exec -T executor executor reconcile-submissions
```

This command only queries the broker and updates confirmed local receipts under the submission lock. It never sends, cancels or replaces an order. If it reports an unresolved ID, keep the stop in place and investigate the broker's full order history/support evidence. Do not delete receipts, change client IDs, or delete the archived session plan to “try again.” If identity mismatches, reconcile the original account/deployment before changing any state.

Disk pressure: stop new submissions, back up state, identify what consumes space/inodes, remove only expendable caches/logs under a reviewed retention policy, and verify storage. Never delete durable order receipts or portfolio proof chains to clear the alarm.

Corrupt scheduler state: preserve the original file, keep submissions stopped, compare archived plans/receipts/broker history, and restore a verified backup. The daemon refuses to interpret corrupt or unsupported state as a fresh empty ledger.

Container “unhealthy”: Docker does not restart a container merely because a health check fails. The internal watchdog exits a stalled loop, allowing `unless-stopped` to restart it. An external monitor is still needed for a frozen interpreter, dead Docker daemon, full host, network outage or offline VPS. See [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/).

Broker timeout: an order may have been accepted even when the client sees an error. Preserve uncertainty and reconcile before further action. See [Alpaca order guidance](https://docs.alpaca.markets/us/docs/working-with-orders).

## Rollback and operating evidence

Save the previously running image ID and a consistent state backup before any upgrade. Rollback must keep submissions disabled and preserve all receipts created after the backup. Never replace newer execution evidence with an older backup and immediately enable an old executor that does not understand durable submission interlocks.

The local October 8 inspection found a v13 deployment with dry-run and disabled mode, reconciled baseline-relative cash/shares, no active operational incidents, and **4 of 20** qualifying operational sessions. This is a point-in-time observation, not a completed reliability qualification or statistical performance baseline. Host reboots, destination resource sizing, off-host alerts, and the remaining observation sessions must be verified on the actual VPS.


## Mailgun setup

The executor supports Mailgun HTTPS delivery using a domain sending key, including US and EU endpoints. It uses only the Python standard library. HTTPS is preferred on VPS providers that block outbound SMTP. The existing SMTP transport also works with Mailgun domain SMTP credentials and TLS.

Set these in the private `executor/.env` (never in a command argument):

```dotenv
MAILGUN_DOMAIN=your-verified-sending-domain
MAILGUN_API_KEY=your-domain-sending-key
MAILGUN_REGION=US
REPORT_SENDER=AMAT <alerts@your-verified-sending-domain>
REPORT_RECIPIENTS=your-operator-address
```

Use `EU` only for a domain registered in Mailgun's EU region. Verify the domain's required DNS records in Mailgun; sandbox domains require authorized recipients. Mailgun API keys and SMTP passwords are different credentials. Selecting MAILGUN_DOMAIN activates Mailgun and requires complete settings; an unrelated host API key alone does not activate it. When both providers are configured, Mailgun takes precedence.

After adding credentials, recreate the container to apply the environment. Use `executor mail-test` only when ready to send an explicit test notification to the configured recipients. Identical test attempts are rate-limited to once per hour; inspect alerts/mail-test.json after a failed or suppressed attempt. Confirm actual delivery in the recipient inbox and Mailgun events; HTTP acceptance is not proof of inbox delivery.

The same provider receives daily reports and operational alerts for degraded daemon ticks, failed scheduled phases, refused submissions, stale/unreconciled portfolio monitoring, and submission interlocks. Identical operational alerts are attempted at most once per hour across restarts; attempts and outcomes are durably recorded. Provider timeout/server failure does not trigger blind email retries or change trading authority. A dead/offline VPS still requires an off-host dead-man monitor because it cannot send its own outage email.

References: [Mailgun HTTP sending](https://documentation.mailgun.com/docs/mailgun/user-manual/sending-messages/send-http), [domain sending keys](https://documentation.mailgun.com/docs/mailgun/user-manual/api-key-mgmt/rbac-mgmt), [Mailgun SMTP](https://documentation.mailgun.com/docs/mailgun/user-manual/sending-messages/send-smtp).

## October 23 resource and backup update

The Web UI/API remains local. New automatic remote health checks are owned by the local API lifetime; native watchdogs and the existing paper-session/backup schedules remain independent. The retained application containers are `amat-executor`, `kalshi-perps`, and `kalshi-perps-trade-alerts`; shadow is stopped with state preserved.

Backup v2 uses bounded host-disk staging, online SQLite snapshots, per-file checksums, 256 MiB source and 272 MiB archive limits, coordinated download verification and isolated restore checks. It avoids the prior 64 MiB source-budget collision with two 32 MiB journals and avoids staging the backup in RAM. See [backup/restore procedures](scripts/BACKUPS.md), [remote diagnostics](../frontend/docs/REMOTE.md) and [actual release evidence](../operations/ui-remote-2026-10-23/REPORT.md). Preserve the current executor image/configuration and newer receipts during rollback; this release did not recreate the executor daemon.
