# Running paper comparison

Started October 19, 2026, America/Detroit. Campaign `20261019T222849-405390`.
Nine independent paper accounts start with **$10,000 each**. Prior smoke/replay
accounts are preserved separately. No real funds are allocated.

[Current comparison report](/home/supasuge/Documents/amat-workspace/kalshi-perps/runtime/comparisons/20261019T222849-405390/comparison.md) ·
[CSV](/home/supasuge/Documents/amat-workspace/kalshi-perps/runtime/comparisons/20261019T222849-405390/comparison.csv) · [Full JSON](/home/supasuge/Documents/amat-workspace/kalshi-perps/runtime/comparisons/20261019T222849-405390/comparison.json)

## Commands

From `kalshi-perps/`:

```sh
uv run kalshi-perps status --all
uv run kalshi-perps stop --all
uv run kalshi-perps stop --all --flatten
```

`run --all` resumes the existing campaign after a stop. There are still only three
application commands: run, status, stop. The active campaign is supervised by the
local user service `kalshi-perps-paper-comparison.service`, with restart on failure.
It continues outside this chat while this computer/user service is running. No
continuous execution is possible while the computer is off or suspended.

## Accounts

- doge-sol-balanced
- doge-sol-lower-drawdown
- doge-sol-max-pnl
- doge-pnl
- sol-profit
- near-pnl
- uni-pnl
- btc-long-pnl
- btc-eth-reference

Each has its own SQLite ledger under `/home/supasuge/Documents/amat-workspace/kalshi-perps/runtime/comparisons/20261019T222849-405390`. All use the same recorded
market observation per cycle, with independent simulated liquidity/accounting
per strategy. These are alternative-strategy comparisons, not one combined
portfolio competing with itself for exchange liquidity.

## Maximum-leverage policy

As requested, all accounts apply maximum-leverage paper sizing whenever their
strategy has an active entry/holding signal. Flat signals remain flat. Original
volatility and fixed-leverage exposure sizing are overridden. Active asset
allocations are renormalized over the currently active legs to deploy available
capital. The original entry, take-profit, time-exit and channel-exit rules remain.

Limits refresh each cycle from Kalshi's published **side- and size-dependent
leverage estimates**, together with its **initial-margin multiplier**. The model
uses the next larger published size bucket and does not extrapolate beyond the
published curve. It reserves 2% for fees/spread and rounds to valid contract counts.
Orders fill only at executable recorded depth; partial fills can delay reaching
the target. Effective leverage and target/initial-margin estimates are reported
separately. This is an indicative paper margin model, without portfolio offsets,
not an authenticated guarantee of live-account maximum leverage.

The cycle waits 30 seconds between observations; steady-state updates were about
35 seconds apart in verification. Source-pinned strategy decisions are cached only
while completed inputs remain unchanged; margin sizing still refreshes every cycle.

**Balanced and lower-drawdown DOGE/SOL become equivalent under this override:**
their allocation/entry/exit rules match, and their original leverage difference is
removed. They remain separate accounts so that equivalence is visible rather than
presented as an independent strategy advantage.

## Comparison metrics

The report records net equity P&L (realized plus unrealized, after fees/funding),
maximum sampled drawdown, fees, funding, effective leverage, fills, completed
trades, take-profit closes and net-profitable closes. Fees use the configured
4 bps per filled side. Funding uses native published event marks/rates and the
position held at each event, including late-publication backfill.

Take-profit frequency counts a completed actual paper episode attributed to the
engine's take-profit trigger. It does not treat every positive close as a take
profit or count each partial fill as a separate episode. Fees and funding determine
whether the completed episode was net profitable. Unattributed exits are reported
separately; manual flattening is excluded from natural close frequency. A 30-day
frequency is shown only after at least one observation day.

Drawdown is based on recorded marks and ledger mutations. REST sampling cannot
establish every intrabar drawdown. The existing equity/position event history is
preserved for subsequent analysis. A network/data issue blocks new exposure and
is shown in the report; the runner retries without resetting account capital.

## Live execution remains disabled

All campaign configurations hard-code `mode = "paper"` and
`execution_enabled = false`. The campaign constructs only unsigned read-only
public-data transports. Live credential variable names are explicitly removed
from the service environment. The live/demo templates remain disabled. No venue
orders were sent.
