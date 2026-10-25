# P3 verification — 2026-09-28

P3 was implemented with three subagents owning the numerical evaluator/registry,
bounded tree search, and candidate/replay integration. The main task added the
training screen, CLI and evidence run. Independent cross-review covered numerical
missingness, registry/source identity, history and source dependencies, checkpoint
integrity, grammar validity and native portfolio accounting.

## Checks and review corrections

New tests cover all 27 complete-data feature formulas, scalar/vectorized
expression parity, prefix/future invariance, missing windows, division thresholds,
clipping, candle geometry, immutable terminal caches, structural caps and all
mutation families. Search tests include a 312-slot variation run, full JSON
pause/resume equivalence, equal-budget random control and corrupt/failure-cache
rejection. Integration tests cover registry/version refusal, required history,
authorized sources, continuous score bounds, missing-evidence parity, removal
accounting and symbolic study ingestion by portfolio replay. Study tests verify
a common calendar, event-weight fitness, whole-expression numerical rejection,
explicit grammar enforcement and refusal to resume changed volume data even if
OHLC is unchanged.

Review corrected a zero-variance numerical edge by checking the actual window
range as well as floating-point standard deviation. It also added direct screen
grammar validation so Python callers cannot bypass the search whitelist. Strict
mypy tuple validation now takes `object` through a small helper while preserving
runtime rejection of mutable lists.

The first focused regression run passed 302 cases and failed one legacy CLI
assertion that symbolic evaluation was unsupported. That assertion now expects
both supported schema examples to evaluate; all 14 data/CLI and symbolic-study
cases passed after correction. Production behavior was not changed to satisfy
the stale expectation. Ruff passes, formatting passes all 30 checked files, and
strict mypy passes all 17 checked source modules.

The full AMAT suite then passed **2,313 tests, with one existing skip**, in
281.29 seconds. The skip is the dependency-layer test for an undeclared
`__init__.py`. No executor code changed; the separate executor suite was not run.
`git diff --check` passed. Full and initial focused logs, the corrected targeted
run and exact check metadata are retained in the evidence bundle.

## Registered experiment

The experiment uses the existing verified Alpaca SIP snapshot and the complete
11-ETF v13 incumbent universe: SPY, QQQ, IWM, EFA, EEM, IEF, GLD, SLV, DBC, USO
and UUP. No new download was necessary. Discovery uses 2016-01-04–2020-12-31;
portfolio replay evaluates later returns over 2021–2022. The initial training
portfolio segment follows the incumbent's existing warmup/anchor conventions.

The frozen grammar permits four normalized terminals, lookback 21, rolling
z-scores, source self and lag zero; maximum depth three edges and 15 nodes.
Both search methods use seed 11, population 32, three generations after the
initial population, and 2-bps one-way event costs. Each records 128 slots.
Genetic search was intentionally paused at 40 slots and resumed to completion.
It recorded 72 distinct genomes, 56 cached slots and four failed slots. Random
search recorded 90 distinct genomes, 38 cached slots and six failed slots.
Both champions use the same 1,002 eligible training dates; the genetic tree has
nine nodes and the random tree has eight. Equal slot budgets do not imply equal
distinct genomes or controlled runtime.

The training event-proxy champion scores are 1.135285 bps for genetic search and
1.387839 bps for random search, after node penalties. Thus the genetic algorithm
did not beat the random control on its own screening objective in this run.
Those numbers are not annualized returns or evidence of portfolio usefulness.

| Recipe | Training incremental score | Later CAGR | Later Sharpe | Later max drawdown | Later fills |
|---|---:|---:|---:|---:|---:|
| Incumbent | 0.0000 | 15.85% | 1.1800 | -7.93% | 120 |
| Incumbent + genetic champion | -0.4775 | 10.63% | 0.8064 | -7.86% | 446 |
| Incumbent + random champion | -0.2407 | 10.43% | 0.7761 | -7.90% | 538 |
| Incumbent + bullish candle | -0.5216 | 13.88% | 1.0691 | -8.17% | 815 |
| Pattern removed | 0.0000 | 15.85% | 1.1800 | -7.93% | 120 |

The portfolio score is exposure-matched training Sharpe improvement less node
complexity, not percentage return. Both symbolic additions and the bullish-candle
control failed `no_incremental_exposure_matched_information`. Pattern removal
reproduced incumbent accounting exactly. All five recipes completed, including
6-bps cost stress and one-session delay stress. Positive screening fitness did
not translate into incremental portfolio value in this comparison.

The incumbent was selected using 2018–2022 history. Therefore the later period
isolates the chronology of the new pattern search but is not pristine
out-of-sample evidence for the whole strategy. Neither candidate was reselected
after inspecting later results. Nested whole-search walk-forward validation,
search-aware nulls, paired uncertainty, parameter robustness and shadow inference
remain future work; execution and promotion authority remain `none`.

## Reproduction and evidence

[Symbolic programming](symbolic-programming.md) documents the contract, numerical
semantics, fitness, limitations and reproduction commands. Evidence is retained
under `outputs/genetic-patterns/2026-09-28-p3/`, including complete discovery
checkpoints/ledgers and replay protocols, diagnostics and accounting artifacts.
`source-runtime.tar.gz` contains 170 source/dependency/configuration/test files;
all 154 replay runtime source hashes were verified against its contents.

Exact machine-readable results are in [p3-smoke-runs.json](p3-smoke-runs.json).

The bundle also includes `checks.json`, all command logs and an
`evidence-manifest.json` binding every generated evidence file by SHA-256.
