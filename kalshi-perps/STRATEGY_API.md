# Strategy API

See the workspace [strategy API reference](../docs/strategy-api.md) for the complete current interface map, AMAT execution-plan contract, and cross-system extension requirements.

The public API evaluates an explicit policy over completed observed candles.
It performs no provider fetching and creates no broker, order, account or credential
client. The application composes those responsibilities in `runtime.py`.

## Entry point

```python
from kalshi_perps.api import StrategyService

strategy = StrategyService.from_config("configs/paper.toml")
metadata = strategy.describe()
choices = strategy.available_profiles()
required_symbols = strategy.symbols
result = strategy.decide(candles, as_of_ms)
```

Pass `strategy="doge-pnl"` to `from_config` to select a named preset. Configuration
and service objects for other profiles remain unchanged. All selected profiles
bind the source/config/selection identity; an existing account cannot silently
change policies.

For composition without the facade:

```python
from kalshi_perps.api import create_strategy
from kalshi_perps.config import Config
from kalshi_perps.profiles import Strategy

engine: Strategy = create_strategy(Config.load("configs/paper.toml"))
result = engine.decide(candles, as_of_ms)
```

## Input contract

`candles` is a mapping from the exact configured Kalshi tickers to ordered records:

```python
{
    "KXDOGEPERP": [
        {
            "timestamp_ms": 1767225600000,
            "open": "12.34",
            "high": "12.40",
            "low": "12.30",
            "close": "12.36",
            "tradable": 1,
        },
        # ... complete contiguous observed hourly history ...
    ],
    "KXSOLPERP": [...],
}
```

Times are UTC hourly opening boundaries in milliseconds. All symbols have the same
contiguous calendar, ending one hour before `as_of_ms`, which is an hourly boundary.
Prices are USD per Kalshi contract. The external provider normalizes underlying
prices before this boundary; execution never normalizes them a second time.

Missing intervals raise `HistoryUnavailable`. Invalid prices, symbol mismatches
and source hash mismatches fail explicitly. Inadequate lookback returns
`ready=False` and no weights. No incomplete/future candle enters a decision.

## Output contract

The process contract remains `crypto-perps-decision/1`:

- `request_id`, `as_of_ms`: bind the response to its request and completed boundary.
- `ready`: whether all configured legs can form a decision.
- `weights`: signed portfolio notional/equity targets keyed by ticker; empty while warming up.
- `source_sha256`: source identity (single source for the legacy adapter, source-set hash for profiles).
- `policy_hash`: frozen strategy identity.
- `input_hash`: request checksum for replay and audit.

Weights already include allocations, leverage policy and the engine's .98 buffer.
The execution risk gate can reduce them; it must not multiply leverage again.
A flat ready decision is a valid result and must not be forced into a trade.

## Profile schema

A profile is TOML with `name`, `[sources]`, optional `[evidence]`, and one or more
`[[legs]]`. Allocations must sum to one; each symbol appears once; leverage is
positive and at most 2. Source filenames must match the allowlisted engine family.
Every source has an exact SHA-256. Evidence identifies the selection artifact,
its SHA-256 and the selected training row. These values are frozen snapshots,
not automatic links that follow a later research result.

Supported families:

| Engine | Research function | Parameters |
|---|---|---|
| symmetric | `crypto_perpetual_study.strategy_weights` | Frozen 4h/20-day/10-day/vol20; leverage and allocation |
| directional | `crypto_directional.directional_weights` | direction, full/volN sizing, leverage, optional ATR target; source's 72h cap |
| profit | `crypto_doge_sol.profit_weights` | optional ATR target and holding cap divisible by 4h; allocation and leverage |
| adx_exits | Source-pinned ADX/partial-exit worker composition | Frozen ADX exit settings; allocation and leverage |
| trend_short | Source-pinned shadow trend worker composition | Validated template, ADX threshold and EMA bars; retained experimental profile |

The service calls the actual research functions in the configured AMAT Python
environment. The worker includes an unavailable row at the decision boundary only
to read the existing engine's causally published prior-bar signal. That row is not
an observed candle, a fill or a manufactured warm-up price.

## Extend it

For another profile using these engine families, add a TOML file in
`configs/strategies/` with validated leg settings, exact source hashes and selection
evidence. Select it with `--strategy NAME`; no application command is added.
For another engine family, add a strict parameter schema in `profiles.py`, a
source-pinned worker adapter, and causality/parity tests against the real function.
Do not add broker calls to a strategy implementation.

A new market-data provider implements `candles(symbol, start_ms, stop_ms)` returning
observed hourly rows plus provenance receipts. `SignalHistory` handles cache,
coverage checks and normalization. Add an explicit instrument mapping; do not infer
k-token normalization or silently mix venues. Provider errors and stale coverage
must block new risk. Keep Kalshi execution/funding separate from proxy signals.

The package's shared paper account is an execution adaptation of the independent
collateral sleeves used in some research. Configured initial allocations are
portfolio target multipliers; they do not reproduce independent sleeve equity
compounding. Compare actual prospective fills and account paths before claiming
research/execution parity or promoting any profile.
