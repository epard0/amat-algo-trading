# Frozen DOGE/SOL forward shadow

**Retired October 23, 2026 by user instruction.** The container is stopped with Docker restart policy `no`; its original `kalshi-perps-shadow_trend-state` volume and frozen evidence remain. A checksum-verified off-host snapshot was restored successfully. Main-account trade alerts continue without a shadow mount. The following describes the retained experiment and historical operating procedure, not a currently running service. See the [retirement report](../../operations/ui-remote-2026-10-23/REPORT.md).

The separate `kalshi-perps-shadow` VPS container ran the user-authorized paper experiment. The original `kalshi-perps` container, image, account database and settings remain unchanged. The default state volume is mounted read-only in the shadow to read its status report. No signing credentials or venue-write transport are used.

The shadow starts with $10,000, a 1x notional sizing target with a 2% reserve, and DOGE/SOL allocations of 25/75 normalized among active legs. Both long and short positions are supported, one net position per asset. Only shorts require completed four-hour ADX14 >=30, price below a falling EMA, and an EMA lower than three completed bars before. DOGE uses EMA25 (100 hours); SOL uses EMA50 (200 hours). Entry permission is latched at the original short breakout onset. No delayed entry into a rejected breakout; no opposite onset while held or same-bar reversal.

All original strategy exit settings remain: 20-day breakout, 10-day channel, 72-hour cap, DOGE 0.5 ATR full target, SOL entry-locked ADX14>=20 ladder (25% at 1 ATR, 25% at 2 ATR, 50% runner with 3-entry-ATR trail), otherwise original 2 ATR full target. Residual partial-profit positions are never increased again. Partial IOC targets and action identities persist across restarts for long sales and short covers. The initial-equity 35% guard is unchanged and blocks strategy orders including exits rather than flattening; funding and liquidation continue.

This is a new prospective DOGE25/SOL50 variant. The prior EMA50/EMA50 backtest was a training-ineligible diagnostic, and its results are not assigned to this account. The shorter DOGE EMA is an explicit prospective heuristic, not a claimed training-selected improvement. Parameters remain frozen. A changed profile receives a new identity and account; no silent retuning.

Observe cumulative net P&L, sampled peak-to-trough drawdown, native funding, fees, positions, and distinct take-profit actions with actual fills. Report TP action frequency after one observed day; also inspect profitability of those actions. Funding uses native event timestamps and historical holdings, including late publication. Fills use actual sorted native depth, integer contracts/ticks, current side-specific margin curves, 4bps taker fee and a 30bps total per-side execution bound.

The default comparison captures its equity, fees, funding and filled TP counter at the first fresh baseline and reports changes from there. Its drawdown starts at that observed equity rather than using the default's lifetime drawdown. The default retains existing holdings/capital and maximum leverage; shadow starts flat and skips carried signals. Thus the comparison measures whole configurations and does not isolate the trend gate's effect. Record each account's timestamps; marks are asynchronously sampled at about 30-second cadence. A stale default report makes the comparison unavailable rather than silently treating old values as fresh.

Live remote artifacts:

- Shadow: `/var/lib/kalshi-perps/adx-forward/status.json` and `status.md` inside `kalshi-perps-shadow`.
- Account: `/var/lib/kalshi-perps/paper-doge-sol-trend-shadow-v2.sqlite`.
- Common-start baseline: same database stem with `.comparison-baseline.json`.
- Volume: `kalshi-perps-shadow_trend-state`; no default account balance was copied.
- Deploy directory: `~/deployments/kalshi-perps-shadow-20261022`.
- Frozen image ID: `frozen-image-id.txt` in that directory; deployed Compose uses the immutable local image ID.
- Source release hashes: `/opt/kalshi-perps/shadow-release-manifest.json`.

Operator status:

```sh
docker exec kalshi-perps-shadow python deploy/health.py
docker exec kalshi-perps-shadow python -m kalshi_perps.cli status --config configs/paper.toml
docker exec kalshi-perps-shadow cat /var/lib/kalshi-perps/adx-forward/status.md
```

The service restarts automatically after failures/host reboot and uses the corrected progress watchdog: fresh blocked status is responsive; ten minutes without durable progress triggers recovery. It has a read-only root filesystem, dropped capabilities, no-new-privileges, bounded CPU/memory/processes/logs and its own network. Do not reset the account to erase losses. The brief initial deployment check produced zero fills; the corrected full-target TP labeling release uses a new account identity and comparison baseline.

Review after at least 90 calendar days and 20 natural completed episodes, extending observation if signals are sparse. Inspect cost/funding coverage, data blocks, rejected/partial fills, guard/liquidations, drawdown and profit-taking profitability. No automatic promotion or withdrawals are enabled.
