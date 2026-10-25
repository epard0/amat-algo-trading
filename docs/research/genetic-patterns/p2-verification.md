# P2 verification — 2026-09-27

The P2 implementation was split between three subagents: candidate/signal
integration, coherent synthetic candles, and matched portfolio replay. Independent
cross-review identified and corrected missing corporate-action exception
provenance, evaluation-calendar auditing, matched-control recipe metadata,
NaN-aware parity, terminal-decision boundary handling and an omitted incremental
eligibility gate. No promotion or execution authority was granted.

## Focused checks

All 45 new P2 cases passed:

- Eight adapter/contract cases, including native v13 missing-evidence parity,
  hash/version refusal, score bounds and causal prefix invariance.
- Twenty-one scenario/bootstrap cases covering all 17 synthetic scenario kinds,
  joint OHLC geometry, preserved observations and unsupported/missing opens.
- Fifteen replay cases covering matched study identity, corrupted artifacts,
  authoritative accounting, CLI failure status, full-calendar gaps and mid-run
  source/exception changes.
- One frontier regression retaining distinct candle genomes.

Existing targeted regressions also passed: 94 signal/router/objective cases
(one slow case deselected there), and 58 market-robustness/Monte Carlo cases.
Strict mypy passed 18 source modules. Ruff passed every affected source and
genetic test file; 26 files passed formatting checks before the three additional
preflight tests, which separately passed Ruff after being added.

The broader run exposed a stale presentation-test mock: `start` now crosses the
`run_frozen_start` child-process boundary, while the test mocked only `run_loop`
and deliberately did not create a real frozen workspace. The test now bridges
that boundary in process, retains its original assertions, and leaves actual
child-process execution to the dedicated controller tests. No runtime guard or
production behavior was changed. All 18 presentation/controller/default-chain
tests passed together after the fixture correction; its lint and formatting
checks also passed.

The complete AMAT invocation finished in 660.11 seconds with **2,210 passed,
one skipped and the one presentation-fixture failure described above**. The
skip is the existing dependency-layer check for an undeclared `__init__.py`.
The corrected case passed in the 18-test targeted rerun. Three preflight guards
were added after full-suite collection and passed separately. Thus every
collected non-skipped case passed either in the full run or its corrected
targeted rerun; the initial full invocation itself is not represented as clean.
No executor code changed, and the separate executor suite was not run.

## Real-data comparison

The frozen v13 return-target incumbent was replayed on its complete 11-ETF
universe: SPY, QQQ, IWM, EFA, EEM, IEF, GLD, SLV, DBC, USO and UUP. Discovery used
2016-01-04 through 2020-12-31. Portfolio training measurement starts after the
existing warmup/initial-training anchor. The later comparison runs from the
2020-12-31 valuation anchor through 2022-12-30, covering 2021–2022 returns.

Each method used population 32, three generations after the initial population,
seed 11, maximum three rules, maximum lag two and 2 bps one-way screening cost.
Genetic search recorded 128 slots, 73 distinct genomes, 55 cached slots and zero
failed slots. Random search recorded 128 slots, 123 distinct genomes, five cached
slots and two failed slots. These are equal slot budgets, not equal unique-genome
counts or a controlled compute-efficiency experiment.

| Recipe | Training incremental score | Later annualized return | Later Sharpe | Later max drawdown | Later fills |
|---|---:|---:|---:|---:|---:|
| Incumbent | 0.0000 | 15.85% | 1.1800 | -7.93% | 120 |
| Incumbent + genetic champion | -0.3232 | 6.63% | 0.5174 | -9.34% | 773 |
| Incumbent + random champion | -0.0971 | 9.70% | 0.7599 | -9.54% | 657 |
| Incumbent + bullish candle | -0.5216 | 13.88% | 1.0691 | -8.17% | 815 |
| Pattern removed | 0.0000 | 15.85% | 1.1800 | -7.93% | 120 |

The training score is exposure-matched incremental Sharpe less rule complexity;
it is not a percentage return. All three additions failed
`no_incremental_exposure_matched_information`. Removal reproduced every
equal-length incumbent backtest field, with NaN-equivalent missing diagnostics.
The increased fill counts are a concrete reason to investigate turnover when
designing the next registered experiment. They do not by themselves establish
the causal explanation for all performance differences.

The selected genetic formula was:

```text
open[t] > open[t-1] AND low[t-2] < open[t]
```

The selected random formula was:

```text
high[t] > open[t-2] AND close[t-2] > low[t-2]
```

Both use completed, consistently adjusted candles. These are failed development
additions, not recommended rules. The full report also retains 6-bps cost stress,
one-session delay stress, yearly metrics and per-asset match/unavailability
diagnostics. No candidate was reselected after viewing later-period outcomes.

The incumbent itself was selected using 2018–2022 history. Consequently this
chronology isolates the new pattern search, but cannot make 2021–2022 pristine
out-of-sample evidence for the entire strategy. Nested discovery, search-aware
nulls, parameter-neighbor robustness, paired uncertainty and shadow evaluation
remain separate requirements.

## Evidence and reproduction

See [portfolio-replay.md](portfolio-replay.md) for commands and
[p2-smoke-runs.json](../../../amat/docs/genetic-patterns/p2-smoke-runs.json) for exact metrics and identities. Outputs
are stored under `outputs/genetic-patterns/2026-09-27-p2/`:

- `genetic/` and `random/`: complete discovery protocols, checkpoints and ledgers.
- `replay/protocol.json`: recipes, dependencies, calendar, panel identities,
  source hashes and corporate-action provenance, frozen before outcomes.
- `replay/report.md` and `report.json`: all five completed comparisons and gates.
- `replay/runs/`: authoritative per-run accounting and intervention recipes.
- `source-runtime.tar.gz`: the exact runtime source, dependency lock and selected
  configuration; its contents were verified against every replay source hash.
- `evidence-manifest.json`: SHA-256 identities for every generated evidence file.
- `full-test-run.log` and `checks.json`: full-run output and exact check results,
  including the initial fixture failure and its successful targeted correction.

Changing runtime source, snapshot partitions or the exception ledger during a
replay prevents publication of a completed report. Copies of discovery evidence
and the action-exception ledger are retained inside the replay bundle.
