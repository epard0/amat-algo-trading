# Strategy API and execution contracts

This is the cross-system strategy reference. There are three distinct interfaces: AMAT's research/simulation boundaries, the versioned AMAT plan consumed by the Alpaca executor, and the Kalshi perpetual decision API. The local HTTP task API is an orchestration layer described [separately](http-api.md).

## Contract map

| Interface | Producer / implementation | Consumer | Carries |
| --- | --- | --- | --- |
| Research boundaries | `amat/src/amat/boundaries.py` and implementations | Backtest/research runners | Typed historical decisions, simulated instructions and accounting |
| `ExecutionPlan`, schema `1.0.0` | `amat.execution.intent` | `executor.plan_source` and native safety/submission | Dated inert account-sized intents and provenance |
| Explicit intraday policy adapter | `executor/intraday_adapter.py` and research schemas | Capability-aware native execution path | Policy-specific capabilities and checkpoints |
| `Strategy` protocol / `StrategyService` | `kalshi_perps.profiles`, `api`, `strategy` | Paper/runtime composition | Source-bound target weights from completed hourly candles |
| `crypto-perps-decision/1` | Subprocess worker | Parent strategy adapter | Request/response identity, readiness and signed weights |

The protocols are not interchangeable. A research result, arbitrary JSON object or browser task cannot bypass a native adapter by labeling itself a plan.

## AMAT research interface

[boundaries.py](../amat/src/amat/boundaries.py) defines structural protocols; domain records live in `amat/domain`. Portfolio construction and strategy configuration produce target weights. `build_instructions` in `amat.execution` converts targets and known positions/marks into simulated orders for a future session. `SimulatedExecutionModel` fills only against the configured historical execution window and records missed fills. It has no broker client or account transport.

Causality is explicit: a decision after session `t` cannot fill before the permitted window in `t+1` or the configured later session. Missing/zero-volume execution windows remain missed fills. Costs are applied in their declared units. Pending instructions affect deltas so overlapping execution lags cannot repeatedly sell the same holdings. See [data/evidence](data-and-evidence.md) before changing signal, adjustment or accounting semantics.

## AMAT execution-plan API

The producer is [intent.py](../amat/src/amat/execution/intent.py); `PLAN_SCHEMA_VERSION` is `1.0.0`. `build_execution_plan` returns a frozen `ExecutionPlan`; `as_dict()` emits JSON-ready values. The CLI `amat plan --help` describes its explicit snapshot, account and output arguments.

| Field group | Fields and semantics |
| --- | --- |
| Version | `schema_version`, `engine`; consumers validate supported contracts |
| Strategy identity | `strategy_name`, `strategy_version`, exact raw TOML `config_sha256` |
| Data/code identity | `snapshot_id`, `appended_sessions`, `code_revision` |
| Timing | `generated_at`, `as_of_session`, `earliest_execution_session`, `window_start_local`, `window_end_local`, `exchange_timezone`, `expires_at` |
| Sizing inputs | `account_equity`, `current_positions`, `target_weights`, derived `gross_target_exposure` |
| Proposed work | `intents`, `withheld`, `unrecognized_holdings` |
| Explanation | `caveats`, optional `monitoring` |

Money and quantities serialize as decimal strings. Weights are numeric fractions. An intent contains `symbol`, `side`, positive `quantity`, `reference_price`, `notional`, `current_weight`, `target_weight` and `reason`. The reference price is a sizing observation; it is not a promised execution price. The daily strategy is long-or-cash: a sell reduces exposure and must not create a short.

Withheld instructions retain a reason rather than disappearing. Unrecognized holdings are reported and not traded by that plan. Expiry checks require timezone-aware instants. The executor resolves the earliest permitted date against its actual exchange calendar.

The consumer [plan_source.py](../executor/src/executor/plan_source.py) supports major version 1 and refuses unknown major versions. It explicitly rejects `research-policy/1` or required-capability plans on the legacy path. The [batch contract](executor/execution-batch-v1.md) defines staged sell-confirm-budget-buy behavior. Native safety rechecks account binding, authorization, timing, equity tolerance, caps, ownership and uncertain outcomes at submission. An inert plan is not permission to skip these checks.

## Perpetual Python API

Install both `kalshi-perps` and the sibling AMAT environment. The facade validates source identity during construction; it does not fetch provider data or create a broker. Invoke from `kalshi-perps/`, using its environment:

```python
from kalshi_perps.api import StrategyService

service = StrategyService.from_config("configs/paper.toml", strategy="sol-profit")
metadata = service.describe()
symbols = service.symbols
profiles = service.available_profiles()
# Supply real, completed, contiguous observations in the contract below.
# decision = service.decide(candles, as_of_ms)
```

`StrategyService.from_config(path, *, strategy=None)` loads config and optionally selects a named preset. `describe()` returns name, symbols, identity, profile payload, decision contract and interval metadata. `available_profiles()` lists TOML stems beside the configuration; it does not install, promote or authorize them.

For direct composition, `create_strategy(Config.load(path))` returns the structural `Strategy` protocol: `identity: str`, `symbols: tuple[str, ...]`, `decide(candles: dict, as_of_ms: int) -> dict`. Implementations run the actual pinned research functions in a subprocess using the configured AMAT interpreter. They do not duplicate the strategy math in the execution package.

## Candle input schema

`candles` maps exactly the configured tickers to ordered observed hourly rows:

```python
{
    "KXSOLPERP": [
        {
            "timestamp_ms": 1767225600000,
            "open": "12.34", "high": "12.40",
            "low": "12.30", "close": "12.36", "tradable": 1,
        },
        # Further contiguous observed hourly rows through as_of_ms - 3_600_000.
    ]
}
```

The row above only illustrates structure; one row is not a warmed-up signal history. `as_of_ms` and each opening timestamp are UTC hourly boundaries in integer milliseconds. Every symbol has the same contiguous calendar, ending exactly one hour before the decision boundary. No future or incomplete candle is admitted. Prices must be finite positive values with valid OHLC relationships; `tradable` is 0 or 1.

Prices are USD per Kalshi contract. Provider normalization happens once before this API. Do not multiply contract size again. Proxy candles are signal observations; native Kalshi books/marks/funding remain execution/accounting inputs.

A missing interval raises `HistoryUnavailable`. Symbol/calendar, timestamp, price and source identity violations fail explicitly. Adequate observed coverage with insufficient strategy lookback returns `ready=False` and empty weights. No forward fill or fabricated warm-up candle repairs missing history. The worker's unavailable boundary row exists only to read the research engine's causally published prior-bar signal; it is not observed data or an executable fill.

## Decision output schema

| Key | Meaning |
| --- | --- |
| `version` | `crypto-perps-decision/1` |
| `request_id` | Digest binding the exact subprocess request |
| `as_of_ms` | Completed decision boundary echoed from the request |
| `ready` | Boolean; whether strategy lookback supports evaluation |
| `weights` | Signed target notional/equity by exact ticker; empty during warm-up |
| `source_sha256` | Legacy source hash or profile source-set digest |
| `policy_hash` | Parent adapter's immutable policy identity |
| `input_hash` | Request digest retained for audit/replay |

The parent checks version, request ID, timestamp, source hash, boolean readiness, symbols and weight bounds. A subprocess failure raises `RuntimeError`; no new orders are admitted. Source mismatch or invalid response raises an explicit validation error. The adapter uses a bounded subprocess deadline.

A ready flat decision is valid. Weights already include engine allocations/leverage and its 0.98 buffer. Runtime risk admission can reduce targets; a separately configured venue maximum overlay is an explicit execution policy, not permission to multiply the engine's leverage again. Account compounding, integer holdings, native funding and actual book depth can diverge from independently compounded research sleeves.

## Profiles and extension points

[profiles.py](../kalshi-perps/src/kalshi_perps/profiles.py) is authoritative for permitted engines and parameters. Current families are `symmetric`, `directional`, `profit`, `adx_exits`, and `trend_short`. Profiles require a name, unique ticker legs, allocations summing to one, positive allocation/leverage (profile leverage at most 2), allowlisted source filenames and exact hashes. Engine-specific schemas reject unknown parameters. Frozen ADX exits and shadow restrictions are explicit code constraints. A historical shadow profile remains source/evidence, not an active deployment.

To add a profile using an existing family:

1. Freeze hypothesis, training selection and evidence identity.
2. Add validated TOML under `kalshi-perps/configs/strategies/`, pinning the actual source set and selection evidence.
3. Verify `describe`, source checks, causality, warm-up and parity against the actual research functions.
4. Use a new explicit account/state identity for a changed policy; preserve existing ledger meaning.

For a new family, add its strict schema, allowlisted source mapping, subprocess adapter and meaningful causal/parity tests. Broker clients belong outside this API. A new data provider implements observed `candles(symbol, start_ms, stop_ms)` plus provenance receipts; `SignalHistory` owns immutable cache/coverage and normalization. Use an explicit instrument mapping and block new risk on stale/missing intervals.

Change wire versions deliberately, update producers and consumers together, and retain rejection tests. Never rewrite source hashes just to silence a mismatch on an existing account. Consult [the native strategy guide](kalshi-perps/STRATEGY_API.md) and [contribution standards](CONTRIBUTING.md) before extending these contracts.
