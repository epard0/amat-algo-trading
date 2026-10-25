# DOGE/SOL paper fill notifications

The user-authorized `kalshi-perps-trade-alerts` VPS service watches the main default SQLite ledger through a read-only mount. `TRADE_ALERT_ACCOUNTS=default` is explicit in the deployed Compose configuration. The optional shadow experiment is retired with its account volume and historical evidence preserved. It uses the existing Telegram bot and destination. Neither runner, image, strategy profile nor account database was changed to add notifications. The watcher has its own persistent outbox volume and restarts automatically.

Alerts fire for committed, nonzero entry fills (`reason=entry`) and take-profit reductions (`take_profit` or `partial_tp`). They do not fire for signals, canceled zero-fill orders, or ordinary rebalances. Each committed order produces one message with volume-weighted actual fill price across its price levels, labeled DEFAULT/max leverage, DOGE/SOL and LONG/SHORT. Separate IOC orders that fill the remainder produce separately identified fill messages. Price is converted from native contract units to USD per underlying coin.

Every message includes filled quantity, account funding, account net P&L including unrealized marks/fees/funding, fill timestamp and account valuation timestamp. Profit-taking messages additionally show the reduction's realized price P&L minus exit fees and proportionally allocated entry fees, plus allocated historical funding. Native funding is replayed by actual timestamp, so late publication can revise subsequent calculations; an already delivered message remains a timestamped observation.

The first scan arms against the current committed ledger high-water mark, avoiding historical trade spam. The outbox uniquely identifies account/order IDs and survives restarts. A confirmed Telegram acceptance is stored permanently. Explicit temporary rejection receives a delayed retry. Ambiguous network delivery or a crash during dispatch is marked `uncertain` and held for review rather than blindly repeated; exact-once delivery across network failures cannot be guaranteed. The watcher healthcheck flags uncertain/failed deliveries for enabled accounts. Retired-account rows remain in the outbox for review and are excluded from dispatch and current delivery health. Do not delete or reset its outbox.

The October 22 deployment baseline was `~/deployments/kalshi-perps-trade-alerts-20261022`; consult the current release receipt for the active image/source identity.

```sh
docker logs --tail 5 kalshi-perps-trade-alerts
docker exec kalshi-perps-trade-alerts cat /var/lib/trade-alerts/status.json
```

The only mounted source account volume is `kalshi-perps_perps-state` (read-only). The retired `kalshi-perps-shadow_trend-state` volume remains on the host; it is not mounted into the watcher. Dedicated notification volume: `kalshi-perps-trade-alerts_state`. Credentials are reused privately in a mode 0600 Telegram environment file, never stored in research artifacts or logs. No venue signing credentials are supplied. Polling is approximately every 15 seconds; Telegram delivery adds network time. No real trading or withdrawals are enabled by this service.


Account selection is validated against the registered account names; unknown or duplicate names stop startup. The registry retains an explicit `shadow` entry for a deliberate future opt-in, but the default is only `default`. Do not enable it without a reviewed deployment/mount change. Account retirement does not reset cursors, modify ledger events, deliver queued shadow notifications, or replay old default fills. Existing default pending notifications retain their normal delivery behavior. No test notification is needed for deployment validation.

Ledger reconstruction now orders compact timestamp/funding-priority/rowid keys in SQLite and reads one event payload at a time. Funding retains its economic timestamp even when published after a closing fill. Historical metrics still require a full traversal; output episode/action details and the fixed-seed strategy history remain proportional to retained history. No strategy seed, account state, order identity, or risk parameter is pruned for memory savings.
