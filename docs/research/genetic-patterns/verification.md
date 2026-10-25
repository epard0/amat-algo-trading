# First milestone verification — 2026-09-27

## Focused checks

83 new tests pass across:

- `tests/unit/test_genetic_patterns.py`: 34 contract/evaluator checks.
- `tests/unit/test_genetic_search.py`: 10 deterministic search/checkpoint checks.
- `tests/unit/test_genetic_data.py`: 7 verified-loader/CLI checks.
- `tests/unit/test_genetic_study.py`: 18 causal label/study/recovery checks.
- `tests/unit/test_adjusted_open.py`: 14 adjusted-open compatibility checks.

The adjusted-open agent also ran the existing snapshot and context/regime suites:
72 tests passed including its 14 new checks. Strict mypy passes all 12 affected
source modules. Ruff passes new files, the command integration, panel, optimize,
and stress modules; all 17 checked source/test files pass formatting.

`snapshot.py` retains two pre-existing lint findings: RUF022 on its export list,
and PLR2004 on a SHA-256 length literal. Running the same Ruff check against
`git show HEAD:amat/src/amat/data/snapshot.py` reproduces both. They were not
introduced by adjusted-open plumbing. `git diff --check` passes.

## Real-data workflow smoke tests

Every run uses 32 population slots × (initial population + 3 generations), seed
11, and minimum support of 30 distinct dates. See [smoke-runs.json](../../../amat/docs/genetic-patterns/smoke-runs.json)
for hashes, exact scores and local report paths.

| Run | Training data | Slots | Distinct genotypes | Cached slots | Failed slots |
|---|---|---:|---:|---:|---:|
| Daily genetic | SPY/QQQ/IWM/GLD, 2016-01-04–2020-12-31 | 128 | 85 | 43 | 0 |
| Daily random | Same dates/assets/costs | 128 | 123 | 5 | 5 |
| Crypto genetic | BTCUSDT/ETHUSDT hourly, 2018 | 128 | 71 | 57 | 9 |

The daily genetic run was deliberately paused after 40 slots, then resumed to
128. Unit tests separately establish exact uninterrupted/resumed checkpoint
equality. Both schema example files validate through the integrated CLI.

The ETF screens use 2 bps one way; crypto uses an explicit illustrative 10 bps
one way. Daily data yield 1,255 complete screening dates; crypto yields 8,621
complete hourly screening positions. Crypto's 214 unavailable OHLC cells across
the two assets are preserved on the full grid before window-level filtering.

The best daily genetic and random scores are approximately 3.7374 and 3.6993
bps per eligible date after rule penalties. Both champions match 1,234 of 1,255
dates, showing that this simple screening objective can favor broad exposure.
The best crypto score is approximately -0.0333 bps. These are adaptively selected
training diagnostics, not strategy returns, out-of-sample evidence, or proof of
genetic-search superiority. P2 must measure incremental net portfolio value.

## Review and recovery

Three implementation subagents owned separate files. Two independent cross-
reviews identified missing actual-input identity and initial-checkpoint recovery.
Regression tests and independent rechecks confirm the fixes. Reports and frozen
protocols retain `execution_authority: none`.

## Broader regression suite

The full AMAT suite completed in 416.91 seconds: **2,159 passed, one skipped,
one failed**. The failure was
`test_real_loop_resumes_and_exports_matched_reviewable_evidence`: its frozen-source
guard raised `controller changed; resume using this workspace's frozen runtime`.
This run overlapped the final source edits to the new genetic modules, and the
existing research controller correctly refused a changed installed source tree.

An isolated rerun of that exact test after edits passed in 110.48 seconds. No
runtime guard was loosened and no unrelated test was changed to obtain the pass.
Thus every collected non-skipped test passed either in the full run or its
isolated rerun; the initial full invocation itself is not represented as clean.

The skipped dependency-direction check says `__init__.py is not in a declared
layer`; this is the suite's existing skip behavior. Executor tests were not run:
no executor, execution-plan contract, or order-submission behavior changed.
