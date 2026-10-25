# P3: typed symbolic genetic programming

Implemented 2026-09-28. The existing `patternSchema/0.1.0` symbolic branch now has
an executable numerical evaluator, a reproducible bounded tree search, and a
registry-bound portfolio adapter. No arbitrary Python or generated source code is
executed. The candle grammar and its `/8` integration remain supported; the new
symbolic integration explicitly requires `strategy-development/9`.

## Concrete pattern contract

[The symbolic example](../../../amat/docs/genetic-patterns/symbolic.example.json) is executable without a schema
change. A tree contains feature terminals, finite constants, unary `neg`, `abs`
or `tanh`, and binary `add`, `sub`, `mul`, `safe_div`, `min` or `max`. The mandatory
outer `tanh` bounds its result to [-1, 1]. Schema limits remain depth four edges
(root depth zero) and 31 nodes. A search may declare tighter limits; the CLI uses
three edges and 15 nodes. At least one feature terminal is required.

Each terminal explicitly declares `name`, `source`, `lookback`, `lag`, and
`transform`. All 27 schema features are implemented. A frozen registry,
`genetic-symbolic-features/1`, defines raw formulas, secondary lookback five,
smoothing one, normalization window 63, z-score clipping at three, and SPY as
the relative-strength benchmark. Rank normalization uses midpoint ties;
z-scores use sample standard deviation and reject zero-variance windows.
Terminals are dimensionless before tree arithmetic. Named sources are checked
against the candidate universe, including implicit SPY dependencies.

Candle-derived terminals use consistently adjusted OHLC: signed body divided
by simple trailing true-range ATR, range divided by that ATR, and trailing mean
upper/lower wick fractions. Zero ATR or zero candle range is unavailable.
Rolling features require complete causal dependency windows. RSI resets and
reseeds after a price gap. Normalization precedes the explicit terminal lag.
History validation includes raw dependencies, normalization and lag.

Arithmetic propagates missing values, including through multiplication by zero.
`safe_div` returns unavailable when the denominator magnitude is below 1e-8;
it does not insert a favorable fallback value. Finite intermediate outputs are
clipped at magnitude 1e6, with clipping diagnostics. Nonfinite arithmetic is
unavailable. The registry digest includes settings and actual formula source
hashes. A cached evaluator is scoped to an unchanged panel; studies hash their
input panels before and after execution and reject mutation.

## Search and fitness

The search protocol freezes allowed terminals, constants, operators, structural
limits, variation probabilities, seed and slot budget. Typed subtree crossover,
subtree mutation, operator mutation and constant mutation have bounded retries.
The ledger records ancestry, operator paths, retries, failures and cached slots.
Random search uses the same initial-tree generator and grammar. Every slot,
including an elite, cache hit or failed attempt, counts toward the budget.
JSON checkpoints preserve RNG state and are verified before deterministic resume.

The initial screen is deliberately a cheap event proxy. At decision close t,
labels enter at t+1 and exit at t+2 using the existing daily execution-price
proxy, paying a round-trip cost. No label crosses the training boundary. All
allowed terminals and all assets share one complete-data calendar. A candidate
with unavailable arithmetic on that calendar fails as a whole, preventing it
from selecting favorable observations by division failures. Constant or nearly
constant outputs and insufficient effect support also fail.

For bounded output x, the screen uses long-only event weight (x+1)/2 and measures
its contribution relative to neutral weight 0.5. Fitness is the equal-weight
mean incremental contribution in basis points, less 0.01 bps per tree node.
This is not portfolio fitness: it omits cash, dividends, position netting,
capacity and actual order-fill accounting. The separately frozen replay measures
whether the discovered feature improves the incumbent under those constraints.

## Portfolio integration

`genetic-symbolic-adapter/1` binds the pattern digest, registry version and
registry digest. It contributes `maxEffect * x` to an already positive incumbent
score, clipped to [0, 1]. Zero incumbent scores stay zero, and unavailable
features contribute zero. The default discovery binding has `maxEffect=0.25`.
Continuous diagnostic summaries report positive, negative, zero and unavailable
values. Complexity counts tree nodes, excluding the mandatory output transform.

Matched replay retains all five recipes: incumbent, genetic champion, equal-slot
random champion, simple bullish candle, and removed pattern. It verifies common
grammar, dataset, full feature panel (including volume/context), registry,
calendar, code and dependencies. Protocols freeze before portfolio outcomes.
Removal parity, causal prefix parity, incremental-information gates, 6-bps cost
stress and one-session delay stress remain in force. No promotion or execution
authority is granted.

## Reproduce the registered smoke experiment

Run from `amat/` with the locked environment. Use fresh output directories for a
new experiment; existing protocols refuse changed source, inputs or settings.

```bash
uv run --locked autoresearch patterns discover-symbolic \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --symbols SPY QQQ IWM EFA EEM IEF GLD SLV DBC USO UUP \
  --train-start 2016-01-04 --train-end 2020-12-31 \
  --population 32 --generations 3 --seed 11 \
  --output outputs/genetic-patterns/2026-09-28-p3/genetic
```

Repeat with `--method random` and output ending `/random`. The defaults freeze
four terminals (`efficiency`, `volume_ratio`, `volatility_shock`, `body_atr`),
lookback 21, zero lag, source self, and rolling z-score normalization. Each method
uses 128 slots. The recorded genetic run pauses after 40 slots (`--max-slots 40`)
and continues with the same command plus `--resume` and without `--max-slots`.

```bash
uv run --locked autoresearch patterns replay \
  --incumbent configs/strategy/amat_v13_return_target.toml \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --genetic-study outputs/genetic-patterns/2026-09-28-p3/genetic \
  --random-study outputs/genetic-patterns/2026-09-28-p3/random \
  --start 2016-01-04 --train-end 2020-12-31 --evaluation-end 2022-12-30 \
  --output outputs/genetic-patterns/2026-09-28-p3/replay
```

Initial symbolic support is adjusted daily research. Hourly crypto, live/shadow
inference, behavioral deduplication, portfolio-fitness evolution, nested
walk-forward discovery, search-aware nulls and promotion remain separate work.
See [P3 verification](p3-verification.md) for measured results and limitations.
