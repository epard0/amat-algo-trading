# P2: candle patterns in authoritative AMAT replay

P2 connects frozen candle patterns to the existing research backtester. Discovery
still uses the P1 event-return screen; portfolio evidence comes from AMAT's actual
cash, corporate-action, allocation, fill, cost and risk accounting. A higher P1
screen score does not establish incremental portfolio value.

## Contract and score semantics

New candidates may carry an optional `genetic_pattern` field containing a
`genetic-candle-adapter/1` record, the inert pattern definition, and its canonical
operational SHA-256. Changing the formula, lags, timeframe, adjustment basis or
binding invalidates that digest. The extension requires an explicit
`strategy-development/8` program. Absent extensions are omitted from candidate
serialization, preserving historical candidate digests.

The first adapter supports daily, corporate-action-adjusted candle conjunctions
with a long-support `feature` binding. On an existing positive score, a match
adds `maxEffect`, and a confirmed nonmatch subtracts it. The result is clipped to
[0, 1], with `maxEffect` capped by patternSchema at 0.25. An unavailable candle
leaves the original score unchanged. An inactive zero score stays zero. The
formula is evaluated at completed-bar close and the runner retains next-session
execution. This is an explicit, fixed centering transform; it is not a learned
probability or a change to order-sizing authority.

The incumbent's cross-sectional ranking schedule, horizon-agreement guards,
portfolio caps and execution machinery remain authoritative. Removing the
extension must reproduce the incumbent's complete backtest exactly. Router and
entry-model combinations remain unsupported; P2 does not loosen their existing
composition contracts. Live planning and executor integration are later work.

## Matched experiment

The replay workflow consumes complete genetic and random discovery studies on
the same dates, symbols, data, screen and candidate-slot budget. It binds their
protocols, checkpoints, selected genomes and full discovery ledgers. It compares:

1. The frozen incumbent.
2. The incumbent plus the genetic-search champion.
3. The incumbent plus the random-search champion.
4. The incumbent plus a prespecified `close[t] > open[t]` candle control.
5. The incumbent after removing the genetic extension.

The replay protocol freezes candidate recipes, source and runtime identities,
data identities and chronological boundaries before portfolio outcomes are
computed. Search and replay failures remain visible. Equal slots do not imply
equal numbers of distinct valid genomes or equal wall-clock compute; those
differences must accompany any comparison of search methods.

The development objective is the existing exposure-matched incremental Sharpe
difference, less 0.01 per added complexity unit. Exposure matching scales the
more exposed strategy down on the training window. It is a retrospective
development diagnostic, not an executable benchmark. Existing quality gates
remain separate from the numerical score. A positive score alone does not pass
the gates or authorize promotion.

Chronological evaluation uses later history without choosing another champion
or calibrating the exposure match on that history. Reports include base costs,
6-bps one-way cost stress and one-extra-session signal delay. Later historical
results are development evidence. In particular, the selected v13 return-target
incumbent was itself selected using 2018–2022 development history. A pattern
search ending in 2020 does not make subsequent dates pristine out-of-sample
evidence for the entire strategy.

## Synthetic validation support

Bootstrap panels carry open/close, high/close and low/close ratios on the same
sampled row, preserving joint candle geometry and missing observations. Synthetic
scenarios preserve the observed OHLC prefix and explicitly model future opens,
expanding high/low where necessary to enclose them. These are hypothetical market
inputs, not reconstructed observations. Panels without observed candle support
do not gain fabricated support. Synthetic metadata and panel hashes bind the
new candle inputs; the legacy path without opens retains its previous identity.

## Remaining work

P2 is an integration and measurement milestone. Typed symbolic GP is P3. Nested
whole-search walk-forward evaluation, search-aware nulls and broader ablation
and transfer studies are P4. Frozen live inference, paper shadow reporting and
operational monitoring are P5. No P2 artifact grants execution authority.

## Reproduce a bounded comparison

Run from `amat/`. The selected incumbent contains 11 ETFs; symbol order is part
of the frozen discovery dataset. A previous four-asset discovery study is not a
matched input for this incumbent and the replay command rejects it.

```bash
uv run --locked autoresearch patterns discover \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --symbols SPY QQQ IWM EFA EEM IEF GLD SLV DBC USO UUP \
  --train-start 2016-01-04 --train-end 2020-12-31 \
  --population 32 --generations 3 --seed 11 \
  --output outputs/genetic-patterns/p2-example/genetic

uv run --locked autoresearch patterns discover \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --symbols SPY QQQ IWM EFA EEM IEF GLD SLV DBC USO UUP \
  --train-start 2016-01-04 --train-end 2020-12-31 \
  --population 32 --generations 3 --seed 11 --method random \
  --output outputs/genetic-patterns/p2-example/random

uv run --locked autoresearch patterns replay \
  --incumbent configs/strategy/amat_v13_return_target.toml \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --genetic-study outputs/genetic-patterns/p2-example/genetic \
  --random-study outputs/genetic-patterns/p2-example/random \
  --start 2016-01-04 --train-end 2020-12-31 \
  --evaluation-end 2022-12-30 \
  --output outputs/genetic-patterns/p2-example/replay
```

Each discovery run records 128 candidate slots, including invalid and cached
slots. Keep the source checkout fixed across both runs and the replay. Use fresh
output directories for new protocols; replay refuses to overwrite evidence.
The replay output includes the frozen protocol and recipes, copied discovery
ledgers, corporate-action provenance, progress/failure records, JSON/Markdown
reports, and full per-run accounting artifacts. The JSON report identifies
promotion requirements that this bounded P2 experiment has not assessed.
