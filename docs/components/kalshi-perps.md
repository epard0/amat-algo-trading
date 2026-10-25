# Kalshi perpetual paper engine and alerts

`kalshi-perps/` is a separate account-owning runtime. It evaluates source-pinned AMAT research functions, obtains completed proxy history and native market observations, admits risk, and writes persistent paper account events. The approved deployment uses its paper path.

## Source map

| Module | Purpose |
| --- | --- |
| `api.py`, `profiles.py` | Public strategy facade, strict profiles and immutable identities |
| `strategy.py`, `strategy_worker.py` | Validated subprocess decision contract to actual AMAT functions |
| `history.py` | Completed external hourly candles, strict coverage, fixed-origin cache and normalization |
| `marketdata.py` | Native Kalshi books, marks, candles and funding observations |
| `runtime.py` | Composition, policy/risk admission and execution coordination |
| `paper.py`, `storage.py` | Simulated native execution and durable account/event ordering |
| `venue.py` | Separate authenticated venue transport for explicitly configured modes |
| `adx_forward.py`, `operations.py` | Forward experiment reports, health and supporting operations |
| `trade_alerts.py` | Main-account event watcher, delivery outbox, cursors and receipts |

## Installation and commands

```sh
uv sync --project amat --locked
uv sync --project kalshi-perps --locked
(cd kalshi-perps && uv run kalshi-perps --help)
(cd kalshi-perps && uv run kalshi-perps status)
```

The CLI has `run`, `status` and `stop`, with `--config` and `--strategy` options. Read help before using `run`, `--all`, or `stop --flatten`: these affect paper account lifecycle, and flatten attempts can leave residual positions when native depth is insufficient. Default source/configuration is in `configs/paper.toml`, with separate presets under `configs/strategies/`.

Paper uses public external history and public native observations without trading credentials. The configured AMAT interpreter and pinned source files must exist. Changing profile/source identity requires a new explicitly configured account, not rewriting the meaning of an old database.

## Signal and accounting boundaries

External perpetual candles supply signals; native Kalshi books, marks and published funding supply paper execution/accounting. Normalization happens once, into USD per contract. The runtime rejects gaps, incomplete candles, stale signals, unsupported instruments and unacceptable proxy/native basis. It does not manufacture a continuous Kalshi history from proxy data.

The paper ledger applies integer holdings, actual sorted book depth, modeled costs, margin and reduce-only behavior. Native funding is deduplicated by event identity/time and late rates are backfilled against historical holdings. Preserve order/funding ordering, fixed-origin strategy history and restart recovery when optimizing resources. The paper margin model is an approximation, and a failed research robustness gate stays failed even if a forward experiment is authorized.

## Trade alerts and retired shadow

`deploy/trade-alerts.compose.yml` mounts the main account read-only and its alert outbox/cursor state separately. `TRADE_ALERT_ACCOUNTS=default` selects the main account. Telegram credentials belong in the private deployment environment, not source. Alerts never trade or restart the engine. Preserve delivery receipts/cursors during release to avoid replaying historical notifications.

The optional shadow experiment is retired. Its protocol, profile, original account volume and results are retained. The presence of `shadow.compose.yml` does not authorize restarting it or mounting it into the active alert watcher. Local multi-account comparison workflows documented historically are distinct from the approved three-container deployment.

Read [strategy API](../strategy-api.md), [native guide](../kalshi-perps/README.md), [native API details](../kalshi-perps/STRATEGY_API.md) and [trade alerts](../kalshi-perps/TRADE_ALERTS.md).
