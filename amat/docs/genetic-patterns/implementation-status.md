# Genetic pattern implementation: discovery and portfolio integration

Implemented through P12 matched component and cost research on 2026-09-29
with implementation subagents and independent cross-review.
P0/P1 provide a runnable **training-only discovery harness**. P2 now adds a
versioned candle-to-signal adapter, coherent synthetic OHLC, and a matched
authoritative portfolio replay workflow. See [portfolio-replay.md](portfolio-replay.md)
for the candle integration contract, evidence limits and reproducible commands.
P3 adds typed numerical symbolic evaluation, bounded tree variation and `/9`
portfolio integration; see [symbolic-programming.md](symbolic-programming.md).
P4 adds nested symbolic discovery/selection, full-pipeline alignment nulls and
paired uncertainty; see [nested-validation.md](nested-validation.md).
P5 adds frozen diagnostic artifacts, parity-checked inference and durable shadow
observations; see [shadow-inference.md](shadow-inference.md).
P6 adds registered completed-session campaigns and 15m/1h/1d/1w/1mo spotting;
see [multi-timeframe-shadow.md](multi-timeframe-shadow.md).
P7 adds explicit adjustment reconciliation and replacement calibration; see
[adjustment-reconciliation.md](adjustment-reconciliation.md).
P8 adds prevalence explanations, source-gap audits, and an extended intraday
coverage experiment; see [p8-verification.md](p8-verification.md).
P9 adds separate native aggregate receipts and parity audits; see
[native-intraday-evidence.md](native-intraday-evidence.md).
P10 adds native 15m/session-open hourly research inputs, five-timeframe portfolio
blending, resumable global/local parameter search, and post-selection controls; see
[parameter-search.md](parameter-search.md) and [p10-verification.md](p10-verification.md).
P11 adds repeated rolling windows, method-family/Pareto shortlists and three
predeclared validation profiles; see [walkforward-parameters.md](walkforward-parameters.md)
and [p11-verification.md](p11-verification.md).
P12 adds matched feature/exit/timeframe removal and paired execution-cost stress
on frozen P11 selections; see [component-comparison.md](component-comparison.md)
and [p12-verification.md](p12-verification.md).

P7 completed its data-reconciliation milestone. Its shadow campaign remains
suspended after a verified IEF prevalence alert, with neutral contributions.
Its archived artifact also requires its original runtime or an explicit
parity-verified migration; see [current P7 status](p7-current-status.md).

## P13 full-history synthetic replay — October 2, 2026

The isolated P13 worktree has completed 544 full-calendar synthetic policy/cost
replays across 34 worlds, including daily, 15m, hourly, weekly and monthly features.
See [results, verification and event-timing limits](p13-synthetic-replay-verification.md)
and [implementation/next-stage contract](p13-synthetic-replay.md). This is frozen
mechanism research; adaptive refitting and Docker deployment are not completed.

## Available now

- Frozen Pydantic models matching the proposed camelCase JSON contract, with
  strict finite numeric values and no unknown fields. Both candle and symbolic
  example JSONs validate and execute. Symbolic search resolves all 27 features
  through a frozen, source-hashed registry and uses resumable typed tree variation.
- Candle rule validation: operand/lag bounds, duplicate/reversed comparisons,
  self-comparisons, contradictions across rules and OHLC ordering. Canonical
  hashes exclude human names and normalize rule order/orientation.
- Causal vectorized candle evaluation with explicit 0/1/unavailable results.
  Missing or invalid OHLC propagates even when another condition is false.
- Optional adjusted opens in `MarketPanel`, derived row-by-row from raw open
  using the same adjustment factor as high/low/close. Existing default loading
  remains compatible; candle consumers fail closed when opens are absent.
- Seeded genetic search, equal-slot random search, elitism, tournament selection,
  crossover and mutation; bounded generation retries, failure accounting,
  canonical genotype cache, full RNG state and resumable JSON checkpoints.
- Verified daily-snapshot and hourly-crypto adapters. Crypto retains the UTC
  calendar and masks missing/off-grid/irregular/zero-volume/invalid bars. Daily
  calendars are checked against the independent exchange schedule.
- `autoresearch patterns validate` and `autoresearch patterns discover` commands,
  plus equivalent standalone `python -m autoresearch.genetic.cli` commands.
- Frozen protocol, actual input-content hashes, dependency/source/runtime hashes,
  process lock, atomic checkpoint publication, JSON and Markdown reports.
- An optional hash-bound `Candidate.genetic_pattern` extension, requiring
  `strategy-development/8`. Missing extension fields are omitted from serialized
  candidates, preserving legacy digests. The adapter adds at most `maxEffect`
  score points to active scores; missing evidence is neutral and zero stays zero.
- Native incumbent controls, genetic/random/simple-candle comparisons, exact
  removal parity, incremental portfolio scoring and explicit failure records.
  `autoresearch patterns replay` freezes the experiment before portfolio outcomes.
- Coherent adjusted opens in joint bootstrap panels and explicitly modeled opens
  in synthetic scenario panels, with versioned metadata and input hashes.

Subagent review found incomplete resume data identity and a first-population
interruption recovery gap. Both were fixed, covered by regression tests, and
independently rechecked. Changed actual arrays, scoring parameters, code or library
versions now refuse resume. A protocol-only interrupted initialization can restart
at slot zero; later resume restores the last complete checkpoint.

## Commands

Run from the `amat/` directory:

```bash
uv run --locked autoresearch patterns validate docs/genetic-patterns/candle.example.json

uv run --locked autoresearch patterns discover \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --symbols SPY QQQ IWM GLD \
  --train-start 2016-01-04 --train-end 2020-12-31 \
  --population 32 --generations 3 --seed 11 \
  --output outputs/genetic-patterns/my-daily-pilot
```

To compare random search, use the identical dates, symbols, costs, population and
generation budget, add `--method random`, and choose a different output directory.
To pause intentionally, use `--max-slots 40`; repeat the same command with
`--resume` to continue. `--max-slots` means additional slots in this invocation.
Three generations means three after the initial population: 32 × (3 + 1) = 128.
Every cached, rejected and elite slot counts toward that same budget. Runtime
differences and distinct valid genome counts should also be compared before
claiming search superiority.

The crypto adapter uses the downloaded manifest directory:

```bash
uv run --locked autoresearch patterns discover \
  --crypto-data data/research/genetic-patterns-2026-09-27 \
  --symbols BTCUSDT ETHUSDT \
  --train-start 2018-01-01 --train-end 2018-12-31 \
  --population 32 --generations 3 --seed 11 --one-way-bps 10 \
  --output outputs/genetic-patterns/my-crypto-pilot
```

Here 10 bps is an explicit illustrative cost scenario, not a claim about an
account's current fee tier. The daily default is 2 bps one way, matching the
existing research baseline cost convention.

## What the current fitness means

An observed candle at `t` can enter at `t+1` and exit at `t+2`. Daily observations
use adjusted opening-window VWAP proxies; crypto uses next-hour opens. Each event
pays its own round-trip cost. Training labels never extend beyond `--train-end`.
Every candidate shares one complete-data calendar requiring usable bars from
`t−maxLag` through `t+2` across every selected asset.

The score is mean equal-weight event contribution, in basis points per eligible
date, less 0.01 bps per comparison rule. Nonmatches contribute zero allocation;
unavailable dates are excluded explicitly from this **screen**, never silently
treated as flat portfolio days. Reports disclose data exclusions and the score
is not annualized. Support counts distinct matched dates, not correlated asset
events. Constant and insufficient-support patterns are rejected.

This cheap search diagnostic does **not** replace AMAT's incremental portfolio
objective. It has no cash, dividends, position-netting, capacity or order-fill
accounting. Its champion file is a discovered pattern, not a validated strategy.
`execution_authority` remains `none` throughout. P2 measures portfolio effects
separately; it does not replace this screen with portfolio-fitness evolution.
`SignalSpec` and its router restrictions, promotion gates, planning and executor
behavior remain unchanged.

## Deliberate limits and next integration work

1. P2 replay measures frozen candle champions against a plain incumbent. It does
   not choose winners on later history or claim that passing basic replay gates
   completes the broader promotion/uncertainty process. Fitted signal incumbents
   are excluded from this replay command until their fit states can be frozen.
2. P6 adds a dedicated strict shadow ingestion path preserving adjusted OHLCV.
   Legacy live/news helpers still omit this context and remain unsuitable for
   pattern inference. Synthetic scenario opens are hypothetical, not observations.
3. P3 is implemented for adjusted daily research. Its screening objective is an
   event proxy; frozen portfolio replay independently measures usefulness. See
   [p3-verification.md](p3-verification.md) for the registered comparison.
4. P4 implements historical nested symbolic validation with explicit prior-access
   limits. Untouched final history, asset-group discovery and actual portfolio
   switching transitions remain unassessed. P5 shadow inference and operational
   monitoring infrastructure are now available for verified daily snapshots;
   P6 now adds manual completed-bar ingestion and registered campaigns, with
   15m/1h/1d/1w/1mo diagnostic views. Prospective evidence still requires actual
   post-registration sessions; see [multi-timeframe-shadow.md](multi-timeframe-shadow.md).

The current grammar correctly treats OHLC ordering as nonstrict: `high > low`
is not a universal tautology because a flat bar may have `high == low`. Impossible
relations are rejected. Merely redundant transitive clauses are not removed yet;
behavioral-vector deduplication is also deferred. Symbolic depth is measured as
edges from a depth-zero root (maximum four), with at most 31 nodes.

See [implementation-plan.md](implementation-plan.md) for the full roadmap and
[verification.md](verification.md) for P0/P1 checks. See
[p2-verification.md](p2-verification.md) for P2 review findings, tests and the
real-data portfolio comparison. In this bounded comparison, all three candle
additions failed the incremental-information gate; the incumbent remains the
reference, and exact pattern-removal parity passed.


P13 research is documented; the historical implementation and results are recorded below.
See the [continuous-state design](p13-research-plan.md),
[full-history/scenario/report contract](p13-full-history-validation.md), and
[Docker paper-demo integration audit](p13-paper-integration-audit.md).
Every planned run covers its complete registered calendar with explicit warm-up
and missing-data coverage. The proposed extended scenario matrix contains
17,408 frozen-mechanism runs; that synthetic campaign is still pending. Historical results below do not authorize promotion.


P13 foundation implementation is complete in the isolated
`/home/supasuge/Documents/amat-p13-workspace/amat` checkout on
`codex/p13-continuous-history`. It adds full-calendar scheduled portfolio replay,
frozen data coverage and 16 measured historical primary paths. See
[implementation](p13-foundation.md) and [results/verification](p13-foundation-verification.md).
Continue P13 work in that isolated checkout; the Docker-mounted host runtime
remains unchanged. The 76-run historical sensitivity matrix is now complete; coherent synthetic worlds,
adaptive refitting and executor parity remain pending.


P13 historical sensitivity controls completed October 1, 2026: all 76 paths retain
the entire verified 2,698-session calendar; the baseline leads returns at every
registered cost. See [implementation](p13-sensitivity.md) and
[results and verification](p13-sensitivity-verification.md). The associated report,
annual/regime data, full account traces and transition diagnostics are retained
in the isolated `outputs/genetic-patterns/2026-09-30-p13-sensitivity` bundle.
No synthetic campaign, parameter promotion or Docker runtime change occurred.


P13 coherent market generation completed October 1, 2026: 17 full-calendar worlds,
all five timeframes, canonical five-minute execution data, and observed-prefix
account parity. See [generator](p13-synthetic-generator.md) and
[verification](p13-synthetic-generator-verification.md). Synthetic MTF feature
adaptation and the four-policy/four-cost replay matrix are next; 544/17,408-run
campaigns and adaptive refitting remain pending. Docker runtime is unchanged.
