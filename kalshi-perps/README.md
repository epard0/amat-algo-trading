# Kalshi crypto perpetuals

For current cross-system installation, API contracts and contribution guidance, start with the [workspace documentation](../README.md#table-of-contents). Dated reports and deployment observations below require their original local evidence and do not describe a fresh clone.

**VPS update — October 23, 2026:** the principal paper engine and main-account trade alerts remain running. The optional DOGE/SOL shadow experiment is retired, with its stopped container, original state volume and verified off-host backup retained. The local nine-account comparison described below is separate. See [trade-alert operation](docs/TRADE_ALERTS.md) and the [implementation/deployment report](../operations/ui-remote-2026-10-23/REPORT.md).

Standalone paper/demo/live execution with a modular strategy API, frozen research
presets, and automatic external perpetual history. The application has only three
commands: **run, status, stop**.

## Running all leading strategies

A supervised nine-account paper comparison is now active. Use:

```sh
uv run kalshi-perps status --all
uv run kalshi-perps stop --all --flatten
```

`run --all` resumes it after a stop. Each account starts with $10,000, uses isolated
state, and shares the same market-data observations. This campaign applies the
requested **maximum published estimated leverage** overlay, including side/size
limits, initial-margin requirements and a 2% execution-cost reserve. It refreshes
sizing each cycle and retains strategy entry/exit rules. Original volatility and
fixed-leverage sizing are overridden; balanced/lower-drawdown DOGE/SOL therefore
become equivalent under this policy. Live remains disabled.

[Campaign controls and metric definitions](PAPER_COMPARISON.md) link to the
continuously updated Markdown, CSV and JSON comparison reports.

## Use it

From `kalshi-perps/`:

```sh
uv run kalshi-perps run
uv run kalshi-perps status
uv run kalshi-perps stop --flatten
```

The default **DOGE/SOL ADX(14)** account is now running as a user-authorized forward paper experiment. SOL uses the ladder when completed four-hour ADX(14) is at least20 and original exits otherwise. DOGE keeps its original exits. Maximum published side/size leverage estimates and initial-margin requirements refresh each observation, with a2% reserve and25/75 active-leg allocations. Partial take-profits reduce actual integer holdings and are never bought back. Actual sorted native book depth supplies IOC fills, bounded at30bps total one-way modeled cost including4bps fee. Published native funding is applied once at its event timestamp, with late-publication backfill.

The historical robustness gate failed; the user explicitly authorized this paper experiment onOctober20. This is not a historical qualification or live promotion. Live venue writes and withdrawals remain disabled. Startup waits for a fresh entry rather than entering carried historical signals. [Current paper report](runtime/adx-forward/status.md), [JSON](runtime/adx-forward/status.json) and [frozen startup manifest](runtime/adx-forward/run-manifest.json). The standalone user service `kalshi-perps-adx-paper.service` resumes the same persistent account; the existing nine-account comparison is separate.

Explicitly selecting an eligible existing paper preset still automatically downloads,
validates and caches completed Kraken perpetual candles, obtains actual Kalshi
books and marks, evaluates the existing research engine, and simulates fills.
No separate download, warm-up, import or strategy-registration command is needed.
`run --once` performs one observation; otherwise it polls until stopped. `run`
also resumes a stopped account. `stop` halts new trading; `--flatten` additionally
attempts bounded reduce-only closes. Inspect residual positions when depth is
insufficient. Ctrl+C stops the local runner while retaining positions.

To select another preset, use the same option with each command:

```sh
uv run kalshi-perps run --strategy doge-pnl
uv run kalshi-perps status --strategy doge-pnl
uv run kalshi-perps stop --strategy doge-pnl --flatten
```

Each preset has a separate account identity and database. Selecting the default
preset explicitly uses the same default account. `--config FILE` remains available
for custom account settings or demo/live environments.

## Configured strategies

These reproduce frozen training selections from the other chat, **Assess crypto
strategy engine fit**. Presets pin engine source hashes and the original selection
artifact SHA-256. They are paper research configurations, not live promotions.

| Preset | Assets/allocation | Exposure policy | Sizing and exits |
|---|---|---|---|
| **doge-sol-adx14** — default paper | DOGE 25%, SOL 75% | Maximum published side/size leverage overlay; 2% reserve | Original DOGE exits; SOL ADX14 >=20 ladder, original otherwise; user-authorized paper |
| doge-sol-balanced | DOGE 25%, SOL 75% | 1.5x each | Full sizing; DOGE 0.5 ATR / SOL 2 ATR; 72-hour holding caps |
| doge-sol-lower-drawdown | DOGE 25%, SOL 75% | 1x each | Same exits as balanced |
| doge-sol-max-pnl | DOGE 25%, SOL 75% | DOGE 2x, SOL 1.5x | DOGE 0.5 ATR / 72 hours; SOL channel exit |
| doge-pnl | DOGE | 2x | Full sizing; 1 ATR / 72 hours |
| sol-profit | SOL | 1x | Full sizing; 1 ATR / 72 hours |
| near-pnl | NEAR | 2x modeled | 40% volatility sizing; channel exit |
| uni-pnl | UNI | 1x | Full sizing; channel exit |
| btc-long-pnl | BTC long-only | 2x | Full sizing; channel exit |
| btc-eth-reference | BTC 50%, ETH 50% | 2x each | Original symmetric long/short, 20% volatility sizing |

All use four-hour signals with 20-day entries / 10-day channel exits. The engine's
2% exposure buffer is preserved. Profile leverage is an exposure policy, not a
promise that the venue will admit that leverage. Risk admission can cap targets.
The ADX default is a separately authorized paper experiment despite its failed historical robustness criteria. It is excluded from new maximum-leverage comparison campaigns. Existing campaign accounts and their configurations are preserved. Losing short-window variants were not relabeled as leaders.

Profile details are in `configs/strategies/*.toml`. Independent research collateral
sleeves are mapped to allocation-weighted targets in this package's shared
collateral ledger. That is an explicit execution adaptation; prospective fills,
compounding, drawdowns and P&L need not match the research sleeve simulations.

## Historical gaps resolved

The default signal source is **Kraken USD perpetual trade candles**, a continuous
external proxy series. Binance's futures REST endpoint returned HTTP 451 in this
environment. Kraken's public endpoint was accessible and returned completed,
contiguous history for the forward checks. No location restriction was bypassed.

The provider pages observed data, drops incomplete candles, checks UTC hourly
boundaries/OHLC/volume/duplicates, and requires coverage through the most recent
completed hour. It never invents missing prices or stitches Kraken candles into
Kalshi's gapped history. A missing or stale external interval blocks new exposure.
Completed cache entries cannot silently be revised. The initial history anchor
persists, so restarts and incremental updates do not discard the strategy path.

Default history is 90 days, automatically retained and extended after bootstrap.
The cache stores original underlying-price observations, response checksums,
retrieval times, provider symbols and completed coverage. Prices are scaled **once**
by the relevant Kalshi contract size before reaching the strategy. For example,
100 DOGE per contract multiplies DOGE/USD by 100; 0.1 SOL multiplies SOL/USD by 0.1.

**Kraken supplies signals; Kalshi supplies execution books, marks and funding.**
External candles are not historical Kalshi fills. The runtime also blocks new
orders when the latest signal close and the current Kalshi mark diverge beyond
the configured basis limit. Different provider prices can produce different
signals from the historical Binance proxy studies. The initial strategy state is
reconstructed from the configured bootstrap history; it does not recover a
pre-existing live position or prove exact full-history research parity.

`status` shows provider, symbol, row count, coverage, checksum and strategy name.
Provider download/validation is automatic inside `run`. API keys are unnecessary
for the external history and public Kalshi smoke checks.

## Configure it

The default `configs/paper.toml` separates account, strategy, data, paper economics,
venue and risk settings:

```toml
[strategy]
profile = "strategies/doge-sol-balanced.toml"
amat_root = "../../amat"
python = "../../amat/.venv/bin/python"

[data]
provider = "kraken-perpetuals"
cache = "../runtime/signal-history.sqlite"
history_days = 90
max_basis_bps = "500"
```

Profile files describe independent strategy legs. For example:

```toml
[[legs]]
ticker = "KXDOGEPERP"
engine = "profit"
allocation = "0.25"
leverage = "1.5"
tp_atr = 0.5
max_hold_hours = 72
```

Keep the profile's pinned `[sources]` and `[evidence]` sections. Changing a strategy
creates a different policy identity; use a new account/database rather than
reinterpreting an existing ledger. `kalshi` remains an explicit native-only signal
provider. An explicitly labeled local `strategy.history_file` is also supported
when using the native provider; it requires the documented signal-history schema.

## Python strategy API

Strategy evaluation is independent of providers, brokers, credentials and CLI:

```python
from kalshi_perps.api import StrategyService

service = StrategyService.from_config("configs/paper.toml", strategy="sol-profit")
print(service.describe())
print(service.symbols)
decision = service.decide(completed_hourly_candles, as_of_ms)
# decision: ready, weights, policy_hash, input_hash and response identity
```

`StrategyProfile` validates TOML profiles. The `Strategy` protocol exposes
`symbols`, `identity`, and `decide(candles, as_of_ms)`. `create_strategy(config)`
constructs the appropriate adapter. `StrategyService` is a convenient facade.
The existing AMAT research functions run out of process in their own interpreter;
they are not copied into the trading package. A source mismatch blocks decisions.

Modules have distinct responsibilities:

- `profiles.py`: leg schema, constraints and provenance.
- `api.py`: public strategy service and factory.
- `strategy.py` / `strategy_worker.py`: validated process contract and actual engine calls.
- `history.py`: external perpetual provider, strict quality checks and immutable cache.
- `marketdata.py`: Kalshi books, native candles, marks and funding.
- `runtime.py`: composition, risk admission and execution coordination.
- `paper.py`, `venue.py`, `storage.py`: accounting, venue transport and durable state.

See [STRATEGY_API.md](STRATEGY_API.md) for contracts and extension guidance.

## Paper, demo and live boundaries

Paper has no venue order-write transport. It supports persistent portfolio P&L,
fees, funding, settlement, partial IOC/FOK fills, consumed depth, reduce-only
orders, margin checks and restart deduplication. Snapshot, fill and cadence
changes commit atomically. Replay funding is explicit. Forward paper reads native
funding event marks and backfills late rates against historical holdings rather
than today's position. Publication lag can affect interim equity/sizing.

API prices are USD **per contract**. Contract size is not applied again to an
already scaled price. BTC size is 0.0001 BTC; ETH is 0.001 ETH. Registry discovery
contains 20 crypto rows, while each strategy enables only its configured symbols.
Unverified kSHIB/kPEPE normalization and inactive instruments remain blocked.
Paper margin/liquidation is a documented fixed-rate portfolio approximation, not
Kalshi's portfolio risk engine. Liquidation can leave a visible negative-equity
deficit. REST polling does not reconstruct every intrabar price or book event.

Demo/live templates remain `execution_enabled = false`. They require explicit
account/key binding and risk limits. Venue writes are journaled before submission,
never automatically retried, and unresolved outcomes block replacements. Venue
admission remains at most 1x gross with full-notional collateral reservations.
Live's initial BTC-only allowlist is unchanged. No demo or live venue orders were
submitted during these changes. A successfully warmed signal engine is not live
strategy qualification.

## Replay and development

The original synthetic BTC/ETH example remains available using the reference
configuration, separately from the default forward account:

```sh
uv run kalshi-perps run --config configs/reference-paper.toml --recording examples/paper.json
```

A recording declares `schema = kalshi-perps-recording/1`, `source`,
`price_unit = usd_per_contract`, `funding_coverage`, `candles` and `events`.
Candle opening timestamps are epoch milliseconds. Rows contain `open`, `high`,
`low`, `close` and `tradable`. Events contain unique IDs, timestamps and per-symbol
`bids`, `asks`, and `mark`, plus explicit funding and settlement entries.
A database cannot mix recording and forward sources.

Developer checks, from this package directory:

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

[Verification evidence](VERIFICATION.md) · [Implementation plan](IMPLEMENTATION_PLAN.md)

Official data reference: [Kraken perpetual market candles](https://docs.kraken.com/api/docs/futures-api/charts/candles/).


### Paper service operational alerts

The VPS paper service accepts `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` through
a private `deploy/telegram.env` file (mode 0600); never include this file in an
image or source control. Recreate the service to apply changes. Only these two
notification variables belong in the paper service; venue signing credentials
remain prohibited. Alerts use the `[KALSHI PERPS PAPER]` prefix and cover blocked
data/accounts, recovery, watchdog stalls and abnormal runner exits. Identical
alerts are attempted at most once an hour, with durable acceptance records.

`deploy/health.py` distinguishes a fresh runner heartbeat from trading-data
health. A responsive runner blocking invalid data stays unhealthy for trading
but is not repeatedly restarted. Missing heartbeats still trigger the ten-minute
watchdog. Status includes failure stage/reason/frames and per-asset native quote
and completed signal-history timestamps. Freshness remains 15 seconds for
execution; funding is fetched before quotes and the deadline is checked again
before each simulated fill request. A stale snapshot is recollected once;
invalid metadata, books or revised immutable history remain blocked.
