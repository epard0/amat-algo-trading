# Native whole-share AMAT paper execution and perps investigation

User authorized switching the remote paper executor, retaining the current best strategy, verifying parity and monitoring the next session. Deployment occurred October 22, 2026 at 22:18 Detroit, outside market hours with zero open orders and no submission interlock.

AMAT runs `amat-executor:native-whole-20261022` with `EXECUTION_MODE=staged-v1`, `WHOLE_SHARE_BUYS=true`, `DRY_RUN=false`, `EXECUTOR_DISABLED=false`. The broker remains hard-pinned paper. Config SHA256 remains `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8` (v14.3.0 all groups + volatility switching); account identity and `executor_executor-state` volume are unchanged. Both services report healthy. Perps was not restarted by this deployment.

The remote image layers only the batch policy, configuration flag and daemon wiring onto the existing cash-rounding paper image. It preserves prior remote source changes and does not deploy unrelated local live-mode work. Frozen deployment source manifests, activation evidence and account parity results are in `outputs/paper-monitor/native-whole-2026-10-22/`. Private environment backups remain only on the VPS. The compose image and environment persist the selected mode across restart/reboot.

## Scope of parity

The existing planner matched the native backtest scheduler using observed account holdings and the available completed-session panel at 2/6/10 bps. All three had identical original intent (one SPY purchase). The latest panel available to this rehearsal ended October 21, after 23 live appended sessions. This is not a completed October 23 plan check: that session must use October 22 data. Six additional synthetic cases (two markets, three costs, cash and invested account states) also passed. Eighty-seven executor tests and twenty-one monitor tests passed, including preserved authorization, policy mismatch rejection, restart idempotency, whole-share purchase rounding and fractional legacy exit handling.

The original authorized quantity remains fractional when the strategy expresses a fractional intent; submitted purchases are rounded down to integral shares after cash budgeting and again at the final cash check. The original authorization, cumulative fills, remaining intent, cash commitments, expiry and account/policy identity remain intact. The whole-share flag contributes to policy identity. Existing fractional policy identities are preserved, and an already frozen batch cannot be reinterpreted with another policy. Sales retain native exit handling, including closing fractional holdings inherited from legacy execution.

This establishes decision/original-intent parity and whole-share purchase rules, not identical realized fills. The historical model uses observed opening VWAP and a 1% opening-volume entry cap; the broker batch uses asynchronous sales, confirmed cash, reserves and bounded limit buys. Those fill models are different. No historical volume or price was fabricated and no claim of identical execution returns is made.

## Next session

The independent VPS minute timer remains enabled with lingering. Monitoring requirements take effect October 23 and continue on later actual exchange sessions. It checks staged mode, whole-share purchase enablement, integral submitted purchase quantities, batch policy identity, prior exchange-session data, default config/account identity, actual fills, residuals, duplicates and overfills. Perps reports now include its latest persisted ready/weight/exit decision; stale/unready decisions are flagged separately from fresh quote marks. The next-session outcomes remain pending. The older October 22 missing-plan alert remains unresolved and visible.

## Why perps has no fills

Read-only SQLite audit found 5,823 process decision evaluations, zero nonzero target levels in either instrument, zero submitted orders and zero ignored carried-entry signals. Repeated polls are not independent strategy trials. The latest decision is ready, DOGE is flat and SOL is hold at zero exposure. Native quotes and books are fresh, proxy basis is small, hourly signal history has no missing intervals, and the runner is not halted.

The strategy requires a completed four-hour close above the prior 120 four-hour highs (20 days). At the investigation's completed-bar cutoff:

| Instrument | Close, USD/contract | Prior 20-day high | Rise needed to reach reference | Breakout |
|---|---:|---:|---:|---|
| DOGE | 7.875 | 9.757 | 23.90% | No |
| SOL | 10.356 | 12.228 | 18.08% | No |

These are moving entry thresholds, not forecasts or a justification to loosen rules. ADX selects the exit regime after entry. Signals use the configured Kraken perpetual proxy, normalized to Kalshi contract units, while paper execution uses Kalshi books. No thresholds, collateral, leverage limits, start state or credentials were changed to force a trade. The dashboard includes static reviewed conditions alongside live decision reasons, existing costs, performance curves and persistent alert history.
