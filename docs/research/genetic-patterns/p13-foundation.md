# P13 implementation: full-history continuous portfolio foundation

Implemented in the isolated `codex/p13-continuous-history` worktree at
`/home/supasuge/Documents/amat-p13-workspace/amat`. The Docker-mounted host runtime
is not updated. This milestone covers the scheduled runner, a frozen input bundle,
and the 16 historical primary paths; sensitivity and synthetic matrices remain
separate pending milestones. No paper candidate or deployment is authorized by
these retrospective results.

## Runtime contract

`ScheduledDecisionContext` supplies a context identity, observed activation
session, earlier selection cutoff, strategy, optional causal signal override,
risk scalar array, asset overlay, and exit rules. `run_backtest` accepts
`decision_schedule` and `full_history`; its defaults retain the original static
runner behavior. An explicit empty schedule captures baseline state provenance.

A single ledger and execution-model instance carry cash, holdings, pending
instructions, actual adjusted entry bases, fill-stress counters and embargoes
through all switches. No account is restarted at an annual boundary. Existing
orders settle under their original signal and execution dates. Corporate actions
restate pending quantities before settlement, and new decisions follow settlement.
Entry bases are tracked before exits become active. An incurred embargo survives
exit-disabled contexts, and an incoming shorter embargo cannot shorten it.

Only existing P11 trend, volatility, rebalance and target-volatility parameters
may change. Universe, cash, execution, cost configuration, annualization, warm-up
and other unimplemented strategy changes are rejected. Context activation must
follow the selection cutoff; context arrays are copied and validated before use.

Full-history mode opens the account at the first source session. Initial opening
attribution is zero; subsequent cash yield is accounted for through warm-up.
Decisions remain gated by the original warm-up requirement. Every source session
is retained, with explicit warm-up flags in exported tables.

## Frozen historical inputs and coverage

The complete verified bundle spans 2016-01-04 through 2026-09-25: 2,698 sessions,
11 ETFs, 29,678 valid raw marks and execution-window cells, and 315 action rows.
Native 15-minute coverage begins 2019-01-02; session-open hourly bars are derived
from four complete native 15-minute bars. Earlier intraday coverage is explicitly
unavailable, and missing contribution weight is not redistributed.

Retained September 28 daily and native receipts agree with overlapping frozen
history, but no verified raw 09:30–09:35 Eastern VWAP/volume extension is retained.
That missing execution input blocks a complete extension. Adjusted minute OHLCV
and full-day VWAP are not substitutes. No new data download was performed.
The data bundle retains a coverage table, native interval masks, overlap audit,
original partitions and the action-exception ledger. Two approximately machine-
precision adjusted candle ordering issues remain explicitly flagged; marks and
execution windows are unaffected.

## Historical replay and evidence

The study freezes P11 validation selections and training-only feature eligibility.
Baseline decisions control earlier history. New contexts activate at the first
observed close after each cutoff and execute under the configured lag. The first
new-recipe execution is January 3, 2024; subsequent recipe changes inherit the
actual preceding portfolio. Shared 2026 recipe identity does not deduplicate
policy paths with different historical state.

Each run uses the full source calendar at 2, 5, 10 or 20 bps one way. The four
policies are baseline, balanced, return and defensive. Results retain three views:
full calendar, tradable history anchored at the last warm-up session, and the
post-selection period anchored at December 29, 2023. CAGR uses actual elapsed
calendar time. Regime attribution uses the prior close's label; disconnected
regime slices report descriptive means and counts, never a fictitious tradable
CAGR or continuous drawdown.

Immutable receipts bind source/dependency identities, selected recipes, cached
feature arrays, the panel, native data, action-exception policy, resolved actions,
and every exported run file. Resume validates existing records and makes no new
replay callbacks for completed runs. A runtime change invalidated the initial
integration attempt; it is retained separately as failure evidence, not a final
result. The final study directory contains the verified rerun.

Outputs include local HTML/Markdown reports, equity/drawdown PNG, comparison and
regime CSVs, per-run full-calendar CSV/Parquet, and exact Decimal JSONL containing
holdings, pending instructions, fills, missed fills, entry bases and embargoes.
Frozen feature arrays and their eligibility bindings are linked alongside the
market-data coverage files. These outputs evaluate the selected P11 mechanism;
they do not claim coverage of every fitted family or the planned synthetic worlds.

## Reproduction

Run from the isolated project root with `PYTHONPATH=src`; the shared dependency
virtualenv otherwise has an editable installation pointing to the host checkout.
The CLI is `scripts/run_p13_continuous.py` and requires explicit `--artifact`,
`--snapshot`, `--native-data`, `--p11-study` and `--output` arguments. The foundation
bundle's reproduction script records the concrete paths used for this milestone.
A different market vintage needs rebuilt feature bindings, not a silent append
to frozen P11 arrays. The study rejects that mismatch.

## Remaining implementation

1. Acquire and reconcile the missing raw execution-window extension before
   claiming the latest available market endpoint.
2. Execute the registered activation-delay and component controls, then add
   active-component/pattern contribution views and explicit method coverage.
3. Build coherent cross-asset 5m worlds and execution VWAP/volume, derive all
   timeframe inputs, and pass full-calendar smoke tests before the extended
   17,408-run frozen-mechanism matrix.
4. Register a separate compute budget for adaptive discovery/refitting inside
   each generated world; fixed-schedule stress is not equivalent evidence.
5. Build shared research/executor decision logic and state reconstruction, then
   demonstrate matched-input plan parity before a separately reviewable paper
   release. The current Docker service continues with its existing runtime.
