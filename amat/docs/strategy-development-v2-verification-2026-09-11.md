# Feature and signal engine v2 verification — 2026-09-11

The implementation now supports composable features, continuous weighted and
fitted signals, independent trading policies, causal refits, feature diagnostics,
explanatory control backtests and a diverse staged research search. Operating
instructions and recipe examples are in [the engine guide](strategy-development-engine.md).

These are research-engine checks, not evidence of a profitable new strategy.
The runs below retained the original baseline. No executor deployment or
strategy promotion was performed.

## Final-source integration

Workspace: `/home/supasuge/Documents/amat-research/v2-final-verification-20260911`

Program SHA-256:
`cd9c156eb09686ec109b5cf5c9a8d9616fa8006510b81a6b9387fde9c10db027`

This freezes the final implementation, including rejection of unchanged model
recipes and valid empty review catalogs. Its source/data/dependency and saved
artifact checks are recorded in `artifact-verification.json`: 66 saved artifact
hashes across the baseline and candidate passed verification. Both new CLI
commands (`features` and `diagnostics`) also completed successfully.

The supplied `trend-breakout.example.json` was evaluated through `run-one`:

- Candidate: `02253a6a97e3033cc6b87498fd6276addcd09cfe77ab4c0b223c2929e1e8bcff`.
- Two signal features, one interaction, confirmation and minimum-hold policy.
- Ten recorded chronological refits and diagnostics at 5, 20 and 60 sessions.
- Ten control backtests: matched candidate/baseline, one/two extra decision
  delays, each signal term removed, interaction removed, joint windows at 80%
  and 120%, and the deterministic block-shuffled timing control.
- Residual exposure-match error: 0.0025245 (about 0.252 percentage points).
- Objective: 0.06547 versus baseline 0.80609. Discarded; the incremental
  exposure-matched information gate failed.

The exported comparison completed a scripted `portfolio_team` review. A repeated
cycle dispatched zero new reviews and reused the cached result. Scripted reviews
test orchestration only.

The explicit checkpoint starts returns on 2023-01-03 with a 2022-12-30 valuation
anchor. Two invocations returned identical cached results and left the incumbent
unchanged. The baseline remains the incumbent. This historical period was already
inspected elsewhere in the project and is excluded from proposal selection.

## Live proposer boundary

A bounded call on the final source attempted the configured four-provider chain:

| Provider | Observed outcome | Budget charge |
|---|---|---:|
| OpenAI | HTTP 429; usage unavailable | 20,258 reserved tokens |
| Gemini | Described a change without encoding a changed recipe; rejected | 3,652 reported tokens |
| OpenRouter | Proposed protected/unknown configuration paths; rejected | 3,746 reported tokens |
| NVIDIA | Malformed/truncated JSON; rejected | 5,636 reported tokens |

The loop paused with `paused_no_provider` and a nonzero CLI exit status. No model
proposal reached a worker. The built-in search and explicit recipe path remain
usable; the optional live model chain did **not** demonstrate a valid proposal
on this run. The total ledger charge of 33,292 includes an unconsumed-or-unknown
reservation; it is not an assertion of billable usage or dollar cost.

An earlier live probe in `v2-model-verification-20260911` exposed the unchanged
recipe case. That probe returned an empty, unparsable review catalog when no
comparison existed. Both issues were fixed before the final-source workspace
was created, and regression tests now cover them. Frozen earlier workspaces
retain their old behavior by design.

## Staged search and fitted forecasts

Workspace: `/home/supasuge/Documents/amat-research/v2-signal-development-20260911`

Program SHA-256:
`b25df6cc40e87e271a3fc906f0495549d85e593ba6cb51a2963a0bfde1c20c02`

This freezes the feature, fitting, diagnostics, control and search implementation
before the final external-proposal/stall and empty-catalog guards. It uses the
Alpaca SIP snapshot, fixed v6 universe/risk settings, 2 bps normal and 6 bps stress
costs, and the new common measured window beginning in 2018 through 2022-12-30.
Its v2 scores are not directly comparable with the earlier v1 report.

The first batch completed ten screening hypotheses, a parameter refinement and
a combination drawn from discarded exploratory candidates. The frontier retained
up to four distinct, sufficiently different forecasts. All twelve trials were
discarded; there were no worker crashes. Trend agreement's objective of 0.83716
exceeded the baseline's 0.80609 but failed the exposure-matched information gate.
A deadband refinement scored 0.85506, below the required 0.05 improvement margin.
The fitted two-feature screening recipe completed ten chronological refits.

A subsequent invocation resumed the same ledger and completed two policy trials.
The final ledger has **14 experiments: 14 discards, zero keeps, zero crashes**.
The final stage sequence, outcome counts, saved fit cutoffs and artifact
hash verification are recorded in `artifact-verification.json`. All candidate
control runs retain their exact recipe/intervention and full accounting output.
All 647 saved artifact hashes across 15 candidate/baseline measurements verified.
The scripted review cycle completed all 14 comparisons (11 initially, three
after resume); replaying the completed catalog dispatched zero new reviews.

A separate initial integration smoke test also evaluated the three-feature
`fitted-trend-structure.example.json`: ten refits, three feature diagnostic sets,
ten controls, and a discard. Its workspace is
`/home/supasuge/Documents/amat-research/v2-integration-smoke-20260911`, program
`ae308f40f3b5b5f74797952d8d413bfa38881f2ce1cb91237ed1be8348cbcecd`.
It predates the final joint-window/interaction ablation refinements.

## Checks and limitations

The focused final research suite passed 88 tests. Coverage includes feature and
prediction prefix invariance after future data corruption, hand-computed
executable labels, missing-window handling, purged inner/outer fit cutoffs,
training-only normalization, hysteresis, signed forecasts, diagnostic ties,
paired uncertainty, exploratory combinations, frontier correlation filtering,
restart/stall budgets, no-op provider failover and empty review catalogs.

The final full AMAT suite passed **1,640 tests with one existing skip** in 242.22
seconds. The skip is the existing `__init__.py` dependency-layer exclusion.
AMAT type checking passed for 133 source files; Ruff lint/format and diff whitespace
checks passed. Executor's unchanged integration boundary passed its full 160-test
suite, type checking, lint and formatting.

The paired block bootstrap is a trial-budget-adjusted research guard, not a
calibrated adaptive-search significance test. Exposure matching is retrospective,
and the block-shuffled control is explicitly noncausal. No candidate was retained,
and these checks do not establish future profitability. Research workspaces remain
outside the executor-mounted checkout.
