# Default ADX DOGE/SOL paper trading

Started October20,2026,12:49p.m. America/Detroit. Initial simulated collateral10,000USD. User explicitly authorized this forward paper experiment despite failed historical robustness criteria. Live venue writes and withdrawals remain disabled.

[Current report](../../kalshi-perps/runtime/adx-forward/status.md) · [Full JSON](../../kalshi-perps/runtime/adx-forward/status.json) · [Startup source hashes and authorization](../../kalshi-perps/runtime/adx-forward/run-manifest.json)

The strategy uses four-hour ADX14>=20 to choose the fixed SOL partial-profit ladder at entry, original exits otherwise. DOGE retains its original exits. Maximum initial leverage uses current published side/size curves and margin multipliers with2% reserve. Active allocations25% DOGE/75% SOL are renormalized when one asset is active. Partial-sale residuals persist through IOC partial fills/restarts and are never bought back. Real native sorted depth is consumed within a30bps one-way cost bound including the modeled4bps fee; unfilled quantity waits for a fresh observation. No simulated liquidity is invented. Native published funding is applied once against holdings at the event timestamp and backfilled when published late. Signals use completed Kraken perpetual proxy candles. Carried startup signals are skipped.

Report metrics include net equity P&L, realized/unrealized P&L, fees, native funding, sampled peak-to-trough drawdown, positions, leverage, fill records, distinct filled take-profit actions, profitable/partial actions and completed actions. Partial IOC fills belonging to the same take-profit action count once.30day normalization begins after one observed day. REST samples do not establish intrabar troughs. No withdrawal policy is active.

From kalshi-perps/:

```sh
.venv/bin/kalshi-perps status
.venv/bin/kalshi-perps stop --flatten
```

The runner is supervised by the enabled user service `kalshi-perps-adx-paper.service`, polling every30seconds plus request latency. It survives process failures and is enabled for user-service startup; the computer must be running and connected. Status is refreshed after each successful observation. A blocked data check prevents new orders; the report records the state. Existing35% initial-equity loss guard remains, and published maintenance checks can liquidate/halt paper positions. Stop with flatten requests bounded reduce-only closes; inspect residuals if depth/cost limits prevent completion. A systemd stop preserves positions; use the explicit flatten command for closes.

```sh
systemctl --user status kalshi-perps-adx-paper.service
systemctl --user stop kalshi-perps-adx-paper.service
systemctl --user start kalshi-perps-adx-paper.service
```

This account is independent of the existing nine-account maximum-leverage paper campaign and the read-only quote collector.
