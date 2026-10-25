# P13 research: continuous portfolio switching

The authoritative scope now includes [full-history validation, coherent market scenarios, associated data exports and paper-demo readiness](p13-full-history-validation.md). Every run must cover its complete registered calendar. This document specifies the continuous-state mechanics within that expanded plan; The historical foundation and 76-path sensitivity stage are implemented; see [current results](p13-sensitivity-verification.md). Synthetic scenarios and executor integration remain pending.

**Status: researched and specified; implementation and new performance runs have not started.**

The recommended next step is an optional immutable recipe schedule inside AMAT's
existing portfolio runner. Each policy gets one continuous account, one sequence
of market sessions and one equity curve. On a scheduled decision, the active
recipe changes; cash, positions, pending instructions, original entry bases and
re-entry embargoes continue. Initial scope is the existing P11 selections and
P12 market/cost contracts, without another parameter search.

## Why this answers a different question

P11/P12 answer how separately warmed recipes perform in each outer period. They
do not answer what an investor starting with the incumbent portfolio would have
experienced when adopting those recipes in sequence.

P12 already demonstrates that the distinction matters: the 2024 baseline at
10/20 bps entered the measurement period with different account states and later
made different decisions. Nine outer decision days differed in order presence.
The two runs returned 8.58%/9.18% during that year even though higher-cost final
wealth was lower. Exact fee and accounting checks supported the path explanation.
See the [retained cost diagnostic](../../outputs/genetic-patterns/2026-09-29-p12/cost-path-diagnostic.json).

A yearly reset would discard the mechanism we now need to measure. Compounding
P11 annual returns would compound results from different hypothetical accounts.
The proposed study instead measures the complete scheduled decision procedure.

## External research and its application here

These sources support the design principles; the concrete AMAT choices below
are engineering recommendations from the code/evidence audit, not requirements
imposed by an external framework.

| Primary source | Finding relevant to this phase | AMAT application |
|---|---|---|
| [QuantConnect: walk-forward optimization](https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization) | Parameter selection uses a trailing data window and an explicit training schedule; optimization frequency introduces a tradeoff. | Keep P11's annual selection schedule fixed and explicitly model activation delay. |
| [QuantConnect: warm-up periods](https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/warm-up-periods) | Indicator warm-up prepares algorithm state separately from tradable operation. | Warm incoming features from available history; do not import a future-selected recipe's hypothetical holdings. AMAT's pre-2024 baseline trading is a declared prehistory, distinct from indicator warm-up. |
| [NautilusTrader: execution flow](https://nautilustrader.io/docs/latest/concepts/backtesting/execution-flow/) | Existing-order processing and strategy callbacks have an explicit event sequence. | Preserve AMAT's own stricter close-to-next-window ordering at switch boundaries. Do not copy Nautilus's same-timestamp fill semantics. |
| [Cvxportfolio: simulator](https://www.cvxportfolio.com/en/stable/simulator.html) | Backtesting represents an actual holdings vector, costs and liquidity constraints. | Retain the current account and size trades from it; do not overwrite positions with target weights. |
| [Boyd et al.: Multi-Period Trading via Convex Optimization](https://web.stanford.edu/~boyd/papers/cvx_portfolio.html) | Multi-period decisions account for risk and transaction/holding costs; planned trades differ from executed trades. | Treat recipe changes as decisions with execution consequences. No convex-optimization dependency or new alpha model is needed for P13. |
| [Bailey et al.: The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | Repeated strategy selection complicates the interpretation of apparently held-out performance. | Retain the already-accessed-history label; do not promote the best transition/control variant after seeing this study. |

## Recommended initial contract

### A. One starting account per cost scenario

Use the existing baseline from the original panel start and the same original
initial cash. At the December 29, 2023 close, all policy paths at a given cost
must have identical cash, holdings, pending instructions and tracked entry bases.
Initialize the ledger at the first source session and retain explicit cash-only warm-up rows until decisions are valid. Measure the entire source span as the primary result, with the period after that anchor as a separate attribution view. September 25, 2026 is the frozen reproduction endpoint; reconcile newer available receipts and freeze the latest complete verified endpoint before the expanded runs.

At every subsequent switch, inherit the preceding real simulated state. Do not
restart from cash, inject capital, normalize equity inside the runner, import
P11 candidate holdings, or liquidate just to make a boundary convenient.

The four cost scenarios have different prehistory; compare policy and baseline
within the same cost. A later all-cash launch sensitivity can answer a different
question, but it must be separately named and registered.

### B. Selection cutoff, decision time and fill time are distinct

Primary recommendation: the new recipe first controls the **close of the first
observed trading session after the validation cutoff**. Its first instructions
can fill one session later under the existing lag-one execution contract.

| P11 validation ends | First new-recipe decision | Earliest resulting execution |
|---|---|---|
| 2023-12-29 | 2024-01-02 close | 2024-01-03 window |
| 2024-12-31 | 2025-01-02 close | 2025-01-03 window |
| 2025-12-31 | 2026-01-02 close | 2026-01-05 window |

These are trading-calendar dates, not calendar-day arithmetic. The January 2
window may still settle orders formed by the preceding recipe. Selection at the
validation close with execution at the next opening window is conceivable, but
requires an explicit selection-completion/data-arrival assumption. It is not the
recommended default.

P11 artifacts were actually created in September 2026. These earlier times are
**modeled historical eligibility**, not actual artifact publication timestamps.
Record both provenance and the simulated availability rule; this is retrospective
replay and must never be labeled a campaign that existed in 2024.

### C. Preserve position and order state

| State | Required behavior |
|---|---|
| Cash and quantities | Carry exact Decimal values; changes require a fill, action or declared cash flow. No boundary cash flows are proposed. |
| Mark and action basis | Continue raw-price valuation and corporate-action cash/share accounting; adjusted features and exit bases remain on their declared basis. |
| Pending instructions and misses | Preserve original signal/execution sessions and quantities; settle normally and include still-pending quantities in new sizing. |
| Entry bases | Track from actual establishing fills even while exits are disabled; retain through resizes and policy changes, clear only on flat. |
| Re-entry embargo | Keep absolute expiry on the global session axis, including through a no-exit recipe. A new shorter embargo cannot shorten an already incurred one. |
| Execution-model state | Keep the existing instance, including its deterministic missed-fill stress counter. |
| Active context | Record recipe, fold, selection receipt and decision-context hashes. |

Recommended exit-policy choice: incoming stop/profit thresholds apply immediately
at its first eligible decision, using each held position's preserved original
entry basis. Existing embargo expiry is honored; a new breach may extend it using
`max(existing_expiry, proposed_expiry)`. Changing rules does not clear a cooldown.
An immediate breach caused by a tighter new threshold creates an ordinary delayed
exit instruction, never an instantaneous boundary fill.

Do not introduce a new permanent "liquidate until flat" latch in this phase. The
existing order/embargo lifecycle remains authoritative. A missed instruction stays
recorded as missed; future decisions follow the declared active rules and carried
embargo. Retaining old exit thresholds until every position closes is a different,
more complex policy and belongs in a separately registered extension.

### D. Preserve the complete decision recipe

Changing only an MTF score matrix is insufficient. P11 recipes also change trend
horizons/deadband/clip, volatility estimation, target volatility, risk scalar and
rebalance thresholds. Each active context needs its own causal allocation inputs,
feature/eligibility mask, overlays and exit rules.

Incoming market indicators may use all causally available historical observations
under their fixed definition. Fold eligibility remains the P11 training-only mask
and must not be recomputed on the outer period. Preserve completed 15m, session-open
1h, daily, weekly and monthly alignment. Missing inputs contribute zero without
redistributing their weight. These still support daily decisions; P13 does not add
intraday fills or bracket orders.

Initially require identical ordered universe, execution lag/window, price basis,
participation cap, cash model, annualization convention and corporate-action feed
across contexts. Costs stay fixed within each scenario. The variable fields are
limited to the already implemented P11 recipe contract. Incompatible changes fail
registration. A new target is not a promise of instant realized exposure: deadbands,
pending orders, cash and liquidity continue to affect execution.

## Minimal architecture

The recommended extension seam is immediately before `_schedule_decision` in
[runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:962).
Keep one settlement/accounting loop. Choose the active validated strategy,
precomputed allocation context and overlay inputs for that decision. The no-schedule
path must preserve existing behavior exactly.

Two concrete traps need explicit handling:

1. [Entry tracking currently depends on exits being enabled](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:952).
   Scheduled mode must track bases throughout baseline/no-exit history.
2. [Embargo enforcement currently lives inside exit-rule application](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:489).
   Scheduled mode must still apply the carried embargo when new exit rules are
   absent, and prevent a new shorter rule from reducing existing expiry.

Proposed files, not yet implemented:

| Component | Responsibility |
|---|---|
| `src/amat/backtest/schedule.py` | Immutable decision contexts, schedule validation and transition records, independent of autoresearch imports. |
| Existing `runner.py` / `exits.py` | Select active context in the existing loop; preserve/record state and explicit exit migration semantics. |
| `src/autoresearch/genetic/continuous_study.py` | Resolve frozen P11 selection hashes, build contexts, register scenarios, invoke one run per path and publish receipts. |
| Existing genetic CLI | Add a distinct continuous-portfolio research command after the library contract passes. |
| New schedule/continuous-study tests | Accounting, timing, state continuity, static parity and immutable resume. |

Core `amat` code must not import `autoresearch`. The research adapter constructs
core-compatible contexts from recipes. Reuse `_candidate`, feature alignment and
existing allocator/execution/accounting functions, without creating another
simulator or installing QuantConnect/Nautilus/Cvxportfolio.

Single uninterrupted runs are sufficient initially. Resume can skip completed
scenarios and deterministically rerun one interrupted scenario from its declared
start. Mid-session checkpoint restoration would require serializing the full state
above and proving restart parity; it is not a prerequisite for this small study.

Changing the runner will deliberately change source identity. Create a separate
P13 runtime/evidence bundle and retain P11/P12 archives. Do not weaken their strict
resume checks to call an old study under changed numerical code. P13 compatibility
is established by explicit replay parity, not by rewriting old source hashes.

## Initial study and later bounded sensitivities

**Primary: 16 continuous paths.** Run baseline, balanced, return and defensive
policy schedules at each of 2/5/10/20 bps. Policies retain their P11 validation
choices, even when a choice subsequently performs poorly. There are two annual
recipe-to-recipe changes per policy plus the initial baseline-to-2024 activation.
The three policies share a 2026 recipe but have different incoming account states;
therefore they are separate paths and cannot be deduplicated by current recipe.

After primary correctness checks, optionally register:

- **24 latency paths:** three policy schedules × four costs × two additional
  activation delays (+1 or +5 observed sessions beyond the primary decision).
- **36 component paths:** three policies × four costs × no-MTF/no-exits/neither
  controls, applied consistently across each entire scheduled path.

That is **76 paths in the full proposed diagnostic matrix**, reusing the four
baseline paths. It is not a 76-trial optimization or a claim that all paths are
independent. Results must report every registered variant. Do not use P12's 2025
37.94% removal result to replace only that year's recipe in an otherwise selected
schedule; that would select using the already inspected outcome.

More frequent reselection, turnover penalties, gradual target ramps, hysteresis,
fresh GP evolution and learned models are later optimization questions. Fit them
inside a newly declared training/validation process after the continuous evaluator
is correct. The number of controls should not grow merely to chase this sample.

## Metrics and attribution

The main result is one continuous equity curve for each policy/cost, measured over
the same anchor/end and including costs, dividends, missed fills and all transitions.
Report total return, consistently annualized return, volatility, drawdown, turnover,
fees, fill/miss counts, exposure and target-versus-realized risk. Report annual
subwindows as slices of that one curve, with correct anchor equity; do not reset
capital or discard the first session's return.

For each switch retain old/new context hashes, eligibility cutoff, activation/fill
sessions, pre/post account state, entry bases, embargo expiries, inherited orders,
new instructions and fills. Provide fixed 1/5/20-session transition windows for
turnover, cash, exposure, fees and drawdown.

Call those **transition-window costs**, not automatically incremental switching
costs: ordinary rebalancing can occur in the same window. For causal incremental
attribution, a later paired control can keep the prior recipe from the same full
pre-switch state. This can be implemented by replaying the identical prefix and
branching the schedule, without requiring a checkpoint API. Do not subtract
unrelated independently warmed P11 paths and call the difference switching cost.

P11/P12 charts can remain descriptive references, but their separately warmed
returns are not parity targets for true switching paths. The continuous baseline
and single-context controls are the authoritative equivalence tests.

## Acceptance gates and implementation sequence

1. **Freeze contract and inputs.** Validate dates, policy receipt hashes, recipe
   hashes, fold eligibility, data/source/dependency identities, fixed configuration
   fields and the complete scenario matrix before outcomes.
2. **Implement and test scheduled core.** Unscheduled parity; single-context parity;
   repeated identical context is a no-op; future-data or future-schedule mutations
   cannot alter earlier decisions. Static-equivalence controls enable the same
   recipe from the original first decision, rather than changing their prehistory.
3. **Prove transitions.** Preserve pending orders across lag-one and longer-lag
   boundaries; handle split/dividend at a boundary; track a held position through
   exits-off to exits-on; retain embargo through exits-off; never shorten an old
   embargo; test partial/missed fills, insufficient cash and persistent execution
   counters. Check every daily accounting identity and consecutive session.
4. **Run the 16 primary paths.** Check identical pre-activation state within cost,
   deterministic whole-run replay, immutable completed-scenario resume, correct
   per-session provenance, and unchanged prior evidence.
5. **Report all paths; add registered sensitivities only after correctness.** Run
   applicable full core/research regression because P13 touches the shared runner;
   unlike P12, genetic-only tests are insufficient. Type/lint/format and source
   identity checks are also required.

Engineering acceptance does not depend on beating baseline. A valid negative
result is useful. Actual switching performance is unknown until implementation
and evaluation are complete.

## Data, compute and evidence limits

The retained inputs suffice to reproduce the frozen comparison through September
25, 2026. The expanded study must first reconcile the latest available daily,
action, execution-window and native intraday receipts into a new immutable data
vintage, downloading missing inputs if necessary. Keep the old vintage as an
explicit reproduction control. Hourly features retain the four-native-15m
session-open contract; unavailable early intraday views remain visible.

The 16 primary and 76 combined diagnostic paths below describe the historical
continuous-switching comparison only. The expanded plan adds a proposed 544-run
smoke matrix and 17,408-run extended frozen-mechanism scenario matrix, with 1,088
shared market worlds. These are proposed counts, not completed simulations.
Benchmark representative full-history worlds and storage before the extended
run; P12's 96-second timing for 108 replays is not a forecast for this workload.

There are only two annual switches per policy after initial adoption, and the
history has been accessed repeatedly. This cannot establish a stable optimal
switching interval or selection-adjusted significance. The planned market-path Monte Carlo
requires coherent cross-asset OHLCV, volume and corporate-action treatment,
recomputed features, and full strategy replay on each path. Resampling these
realized daily returns is a separate conditional analysis. A research-validation
purge/gap and a trading re-entry embargo also serve different purposes and must
not share an ambiguous field.

P7 remains a separate suspended shadow campaign. P13 neither migrates its runtime
nor clears its drift alert.

## Research artifacts

- [Full-history and reporting requirements](p13-full-history-validation.md)
- [Joint scenario research](p13-scenario-research.md)
- [Read-only Docker and paper integration audit](p13-paper-integration-audit.md)

- [Draft protocol with verified P11 recipe identities](p13-proposed-protocol.json)
- [Runner audit and source references](p13-runner-audit.md)
- [Selection/timing audit](p13-protocol-audit.md)
- [Research provenance and integrity checks](p13-research-evidence.json)

The protocol is a proposal, not an executed/registered historical experiment.
