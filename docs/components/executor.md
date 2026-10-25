# Alpaca executor

The `executor/` project owns the Alpaca account boundary: scheduling, account observations, plan parsing, order admission, broker submission, reconciliation, interlocks and durable receipts. It consumes AMAT artifacts through an explicit subprocess boundary. Research code never becomes the order owner.

## Source map

| Module | Responsibility |
| --- | --- |
| `config.py`, `safety.py` | Environment validation, authorized configuration and independent safety policy |
| `daemon.py`, `observation_window.py` | Session lifecycle, timing, durable progress and watchdog |
| `plan_source.py`, `intraday_adapter.py` | Versioned plan parsing and explicit capability-aware policy adaptation |
| `submit.py`, `batch.py` | Submission boundary and staged sell-confirm-budget-buy |
| `broker/alpaca_paper.py`, `broker/alpaca_live.py` | Separate paper/live broker transports |
| `order_registry.py`, `journal.py`, `portfolio_checkpoint.py` | Order ownership, receipts, append-only observations and recovery |
| `operations.py`, `health.py`, `portfolio_monitor.py` | Operational evidence, liveness/readiness and reconciliation views |
| `report/`, `mailgun.py`, `telegram.py`, `alerts.py` | Reports and optional notification delivery |
| `remote.py`, `scripts/vps-control.py` | Pinned SSH and registered remote helper operations |

## Setup sequence

Install `uv sync --project executor --locked --extra remote`. Copy the blank environment template without replacing existing credentials:

```sh
cp -n executor/.env.example executor/.env
chmod 600 executor/.env
uv run --project executor executor --help
uv run --project executor --env-file executor/.env executor health --help
```

Configure real paper and market-data keys, AMAT root, snapshot, state path, authorized strategy SHA-256 and risk caps. The template's `/opt/amat` and `/var/lib/...` paths describe the container mount layout; use valid local paths for workstation operation. Keep dry run and disablement active until account binding, readiness and independent policy are reviewed. A daemon launch is a separate operational decision from installing dependencies.

The checked-in Compose file has older image defaults and an optional live profile. It is a template, not evidence of the image/configuration currently deployed. Compare the actual deployment before any selective release. Never activate the live profile as part of paper installation.

## Execution guarantees to preserve

Plans bind completed signal sessions, expiry, allowed configuration and sizing inputs. The executor rechecks broker/account identity, timing, order ownership, positions, caps and restrictions at the native boundary. Staged execution confirms sells, budgets buys from observed available cash, and preserves the original execution window. Pending/unresolved submissions cannot be blindly retried.

Receipts, registry state, interlocks, equity history and checkpoints survive code rollback. Restore testing uses isolated state. Monitoring reads evidence and cannot clear stops, restart engines or place orders. “Healthy” reports durable process progress; “ready” adds configured operational conditions and is still not proof of a fill.

See [native README](../../executor/README.md), [batch contract](../../executor/docs/execution-batch-v1.md), [backup contract](../../executor/scripts/BACKUPS.md) and [operations](../operations.md). The [VPS runbook](../../executor/VPS_RUNBOOK.md) contains dated deployment context; adapt it to your own infrastructure and current source identity.
