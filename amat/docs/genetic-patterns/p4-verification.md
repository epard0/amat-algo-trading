# P4 verification — 2026-09-28

P4 adds a frozen nested validation workflow for symbolic genetic programming.
Three subagents implemented fit-only discovery, inner/outer portfolio replay,
and statistical/protocol primitives. The main task implemented orchestration,
artifact verification, failure recovery, CLI and the registered real-data run.
Independent review checked split isolation, null mapping semantics, statistical
interpretation and portfolio initialization.

## Review and regression coverage

There are 43 new P4 tests:

- 16 protocol/statistical cases: immutable round trips, chronology, split-local
  joint mappings, paired dependence, fold boundaries, constant returns,
  nonfinite rejection, add-one/tie handling and Monte Carlo resolution.
- 12 fit-search cases: equal budgets, seed shortlists, complete failed populations,
  deterministic interrupted resume, fit identity, mapped feature scores with
  original labels, invalid donors and final fit rows with unmatured labels.
- Nine portfolio cases: selection freeze, authoritative prefix parity, incumbent
  fallback, exact removal, selected-pattern diagnostics, warnings, mapping
  permutations, all three split boundaries and common-row donor availability.
- Six orchestration cases: pre-outcome freeze, complete null repetition, checksum
  verification, changed-protocol refusal, failed-null recovery, source-panel
  copying, paired-date integrity and positive/negative support-gate paths.

The focused genetic suite passed 251 tests before the final aggregate gate case
was added. All six orchestration tests passed afterward. Strict mypy passes 17
source modules; Ruff and formatting pass the genetic source and tests.

Review led to concrete corrections: feature-donor eligibility is separate from
label maturity, so the final two fit observations can donate valid feature values
inside a null while their unavailable labels are still purged. Replay mappings
must be permutations confined to fit, inner and outer splits. Strength diagnostics
report when the schema cap makes the requested upward change a no-op. Outer
reports include accounting warnings. Portfolio initialization is explicitly
identified as a separate warmed historical replay per fold, with actual switching
transitions still unassessed.

The final full AMAT suite passed **2,360 tests with one existing skip** in
317.99 seconds. The skip is the dependency-layer check for an undeclared
`__init__.py`. All 34 checked genetic source/test files passed formatting, and
`git diff --check` passed. No executor code changed; its separate suite was not
run. The full log and exact check metadata are retained in the evidence bundle.

## Registered data and experiment

The immutable plan is [p4-smoke.json](../../configs/genetic/p4-smoke.json).
The existing verified Alpaca SIP snapshot supplies the incumbent's complete
11-ETF universe. No new download was required. Data start on 2016-01-04.

| Outer fold | Fit ends | Inner selection returns | Outer returns |
|---|---|---|---|
| outer-2021 | 2018-12-31 | 2019–2020 | 2021 |
| outer-2022 | 2019-12-31 | 2020–2021 | 2022 |

Each method uses seeds 11 and 29 and 32 candidate slots per seed, selecting two
training-ranked distinct contenders for inner portfolio replay. All four P3
normalized terminals, constants, operators, depth/node bounds and probabilities
are frozen. Three complete null repetitions and the observed pipeline total
1,024 slots. Paired bootstraps use 1,000 repetitions with 20-session blocks.

The three-replicate null budget has minimum attainable p=0.25 and cannot pass
alpha=0.05. This run verifies implementation and evidence handling; it is not a
powered significance experiment. A larger experiment requires a new frozen
protocol and output directory. No seed, grammar or shortlist was retuned after
viewing outcomes.

## Observed selection

All eight inner contender evaluations failed to improve the incumbent. Both
methods retained the incumbent in both outer folds. The best inner incremental
score among each method's two contenders was:

| Outer fold | Genetic best inner score | Random best inner score | Selected policy |
|---|---:|---:|---|
| 2021 | -0.0824 | -0.2070 | Incumbent for both methods |
| 2022 | -0.3090 | -0.3719 | Incumbent for both methods |

These scores are exposure-matched inner Sharpe improvements less node complexity,
not returns. The failed contenders retain complete accounting artifacts and
explicit rejection reasons. Incumbent fallback means the measured incremental
outer return is exactly zero; it does not establish that the rejected patterns
have zero standalone returns or that genetic programming can never be useful.

The observed and all three null pipelines completed: eight fold jobs and all
1,024 registered slots are accounted for, including 45 failed genotype slots. The two methods each have 503 paired
outer return observations (252 in 2021 and 251 in 2022), zero mean excess, and
95% paired intervals [0, 0] because they retained exactly the same incumbent.
These degenerate intervals describe the selected fallback policies, not uncertainty
about the universe of possible patterns.

The maximum null statistics were 0, 0 and -0.0297088 annualized arithmetic excess.
With ties counted, both observed methods have conditional family-adjusted p=0.75;
the Wilson interval for the underlying exceedance probability is approximately
[0.208, 0.939]. Both explicitly fail historical-support gates. In the third null
replicate, both methods selected a pattern for 2021, so real-data execution also
exercised non-incumbent selection and outer replay. Those synthetic shifted
patterns are not deployable candidates.

A complete `--resume` verified every completed fold's child evidence and reproduced
the final report JSON byte-for-byte. The runtime source hashes still matched all
158 frozen files after the experiment. Exact results and full trial counts are in
[p4-smoke-runs.json](p4-smoke-runs.json).

## Interpretation and retained evidence

The incumbent was already selected using 2018–2022 history. These folds therefore
remain historical development evidence. Null exchangeability is conditional on
the chosen feature-alignment transformation, not guaranteed by circular shifts.
The paired bootstrap does not correct broader researcher choices. Actual
incumbent-to-pattern position transitions, held-out asset-group discovery,
untouched final history and shadow inference remain unassessed.

The output bundle at `outputs/genetic-patterns/2026-09-28-p4/` retains the frozen
parent protocol and all null mappings; per-fold complete discovery checkpoints;
pre-outer selection manifests; primary/stress/delay accounting; diagnostics;
completed-fold artifact checksums; and progress/failure records. The archived
runtime contains 180 source/configuration/dependency/test files, and all 158
runtime source hashes were verified against the parent protocol. A copied
action-exception ledger matches the frozen identity.

See [nested-validation.md](nested-validation.md) for numerical definitions,
statistical gates, limitations and exact commands.

`checks.json` and the command logs record the checks above.
`evidence-manifest.json` binds every retained evidence file by SHA-256.
