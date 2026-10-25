# P13 expanded scope: full history, market scenarios and paper-demo readiness

**Status: historical foundation, 76-path sensitivity matrix, coherent market generator and 544-path synthetic replay completed; event-timing expansion, extended/adaptive search and executor integration remain pending.** See [synthetic results and coverage limitations](p13-synthetic-replay-verification.md). This extension records the
user's requirement that every run cover the complete available data span and
retain associated observed/simulated data for clear performance reporting.
It supersedes any interpretation of the original P13 proposal as evaluating
only 2024–2026. The detailed continuous-state mechanics remain in
[p13-research-plan.md](p13-research-plan.md).

## 1. Full-span contract for every run

Every scenario must register its universe, calendar, source vintage, first
available session and last complete verified session. Run the account across
that entire calendar. Market regimes, annual periods, active-feature periods
and selection windows are report slices of the same run, not separate accounts
or a reason to truncate the run.

For the current 11-ETF study, the verified daily panel spans **2016-01-04 through
2026-09-25**. The retained long native-15m receipt spans **2019-01-02 through
2026-09-25**. Newer retained raw/adjusted daily and native receipts reach
September 28, but have not been assembled into the same complete research
snapshot with verified execution windows, actions and overlapping history.
Before implementation runs, inventory and reconcile the latest available inputs
and freeze a new full-span bundle through its latest completed verified session.
Do not treat September 25 as a permanent arbitrary cutoff. Do not silently
truncate the daily account to the shorter intersection of all timeframe histories.

If an older interval or later extension cannot be verified, record the exact
missing dependency and dataset end. A headline "full history" must identify the
actual verified range and outstanding coverage, rather than imply inaccessible
prices were tested. Separate universes/calendars, such as crypto, need separate
full-span registrations; they must not be merged by taking a misleading shared
calendar or borrowing another asset's history.

Initialize the account at the first source session. During indicator warm-up,
record genuine no-trade cash/ledger observations under the declared cash model.
Do not fabricate signals to fill warm-up gaps. The existing runner starts its
ledger at the last warm-up session, so scheduled/full-span mode must explicitly
add first-session initialization and advance the accountant through warm-up.
Preserve legacy unscheduled behavior. For the current zero-cash-yield config,
post-warm-up static parity should remain exact; a nonzero cash-yield mode needs
an explicit full-span reference, not a silently adjusted opening balance.

Record `data_start`, `ledger_start`, `indicator_ready_at`,
`first_eligible_decision`, `first_fill`, `last_verified_session`, and every missing
or ineligible view separately. Unavailable features and true zero-valued features
must be distinguishable in the report. A missing optional 15m/hourly view follows
the already declared neutral-contribution policy; a missing mandatory mark or
execution input follows the existing fail/missed-fill contract.

Always show these three metric scopes side by side:

1. Full available calendar, including explicitly labeled warm-up cash periods.
2. Tradable history after indicator warm-up, without deleting later flat periods.
3. Post-selection / active-component periods, as attribution views of the same
   ledger. This prevents years of baseline prehistory from disguising whether
   MTF or a new recipe actually helped.

## 2. Cover the mechanisms without pretending hindsight is deployment

The first 16 continuous P11-policy paths and all additional latency/component
paths run start-to-end. Before a selection is available, the registered baseline
controls the account. The three profile histories remain separate even when they
later share a recipe.

Add a method-coverage manifest covering the incumbent, retained GP expression,
all 27 registered feature terminals at the current 21/63-bar lookbacks, portfolio
settings, MTF modes and exit modes. Existing fitted signal, router, entry and
learned-exit methods must also be inventoried: identify their actual adapter,
fit-state boundary, supported runner, and current inclusion or exclusion reason.
An implementation present somewhere in the repository is not evidence that the
P13 scheduled evaluator supports it. Missing adapters become explicit work items;
no method may be reported as tested because another method ran successfully.

There are two different kinds of full-span experiment:

| Experiment | What it can establish |
|---|---|
| Historical decision procedure, with selection/fitting restricted to available history | How a specified adaptive mechanism behaves with one continuous account. Existing P11 choices start only when historically eligible. Earlier reselection requires additional training/validation folds. |
| Fixed-recipe replay over the entire dataset, including years before that recipe was selected | Retrospective behavior across regimes and failure modes. It is a hindsight diagnostic, not out-of-sample evidence or authorization to deploy. |

Do not apply a mask learned in a later P11 training window to early history and
call it causal validation. Full-history fixed-recipe diagnostics must record the
future-derived recipe/mask provenance. A causal alternative needs a separately
registered past-only eligibility rule and becomes a different experiment.

The full-span extension does not authorize substituting P12's favorable 2025
removal result into an otherwise P11-selected schedule. That would select on an
already inspected outcome. Full-history plots can expose such a variant, but the
selection/validation provenance must remain visible.

## 3. Coherent simulated market data

The repository already declares **17 daily scenario families**: bull, bear,
range, whipsaw, crash/recovery, correlation break, Gaussian noise, Student-t(5),
Student-t(3), Laplace, skewed shocks, volatility mixture, volatility clustering,
gap down, gap up, liquidity crisis and flat prices. Existing daily scenario and
joint-bootstrap generators are useful building blocks, not yet a complete native
multi-timeframe switching simulation.

Every simulated world retains the entire source calendar. Keep its pre-anchor
observations/calibration prefix and generate the remainder through the registered
end. The report distinguishes observed prefix from synthetic suffix. The anchor
must have adequate past-only calibration for all modeled quantities. A practical
initial MTF anchor is **2020-01-02**, after 2019 native-intraday coverage, with the
2016–2018 intraday inputs explicitly unavailable. A generative experiment earlier
than empirically supported calibration must be labeled assumption-driven rather
than fitted to unavailable history.

Generate one joint cross-asset world, then reuse that exact world for every
policy, component and cost comparison. Recompute patterns, volatility estimates,
regimes, timeframe aggregates, orders and account outcomes on that world's data.
Never combine generated daily candles with unchanged observed 15m/hourly features.

The synthetic execution model must match the **09:30–09:35 Eastern** VWAP/volume
contract. A 15m OHLCV bar alone does not determine that five-minute window.
Recommended construction: a joint latent 5m candle/volume path with an explicitly
modeled within-bar VWAP; aggregate three bars to 15m, twelve to a full hour, and
all session bars to daily, then aggregate daily to calendar week/month. An
alternative is a rigorously constrained opening-five-minute sub-bar plus 15m
paths. Native hour tails retain the existing omission policy, while daily bars
still include the full session. Half-days follow the exchange calendar.

A synthetic 5m substructure calibrated only from 15m and recorded opening-window
data includes assumptions; retain them and stress their ranges. The actual minute
receipts begin in 2026, so they cannot calibrate a supposedly past-only 2020
synthetic world. Download and verify older fine-resolution data where available,
or label the microstructure model as assumed. Do not present modeled VWAP as an
observed broker execution.

Required synthetic invariants include positive finite prices, low ≤ open/close ≤
high, nonnegative volume, aggregation consistency, execution VWAP within its
modeled price bounds, execution volume consistent with session volume, cross-asset
shock dependence, coherent gaps, and preserved closure/missing-bar masks. The
calendar rules for consolidation are material; see
[QuantConnect's consolidation documentation](https://www.quantconnect.com/docs/v2/writing-algorithms/consolidating-data/getting-started).

Choose one coherent corporate-action convention per world. A synthetic total-return
world must not also credit observed dividends/splits as if raw prices were used.
A raw-price synthetic world requires a consistent generated action ledger. Reuse
historical corporate actions only in a prefix whose price/accounting basis agrees.

Two Monte Carlo labels are mandatory:

- **Frozen-mechanism stress:** keep the real-data-selected schedule fixed and
  test its transfer behavior on generated worlds. This does not validate an
  adaptive optimizer retraining in those worlds.
- **Full adaptive-pipeline simulation:** regenerate training features, eligibility,
  discovery/selection and activation inside each world using only its past, then
  execute the resulting schedule continuously. This is the broader eventual test
  of the entire learning mechanism and requires its own nested compute budget.

Resampling realized strategy returns is useful as a separate conditional display,
but is not a substitute for either full price-path replay. The existing daily
bootstrap samples its supplied calibration panel; callers must truncate that
panel to the declared cutoff rather than accidentally include future returns.

## 4. Compute and coverage plan

Start with correctness and a bounded full-calendar matrix, then expand without
changing definitions after seeing outcomes:

| Stage | Proposed coverage |
|---|---|
| Historical primary | 4 policy paths × 4 costs = 16 complete-history runs |
| Historical sensitivity extension | 24 activation-delay paths and 36 component-removal paths; 76 total including primary |
| Synthetic integration smoke | 17 families × 2 seeds × 4 policies × 4 costs = 544 complete-calendar runs |
| Extended frozen-mechanism stress | 17 families × 64 seeds × 4 policies × 4 costs = 17,408 runs across 1,088 paired worlds |
| Full adaptive-pipeline Monte Carlo | Separately budget after profiling per-world nested search; cannot claim completion from frozen-mechanism results |

The synthetic counts are a proposed staged workload, not completed work or a
claim of exhaustive continuous-parameter coverage. Deterministic scenario families
may create duplicate worlds; detect and report effective unique worlds rather
than count duplicates as independent draws. More seeds do not correct a poor
market model. Scenario frequencies are experimental allocations, not forecasts
of real-world probabilities.

Predeclare seeds, common random numbers, calibration cutoffs, scenarios, costs,
method coverage and failure handling. Parallelize separate worlds/runs; resume
immutable completed runs. Count invalid/extreme/unsupported paths explicitly and
keep failure records. Do not redraw until a result looks acceptable. Profile one
full path's time/memory/storage before sizing batches. Store each generated world
once, referenced by all runs, with generator/input/dependency hashes and retained
compressed data; do not duplicate market arrays per recipe. No runtime estimate
from P12's daily-only throughput should be presented as measured MTF Monte Carlo
throughput.

## 5. Regime and pattern reporting

Create causal regime labels from information available at each decision: trend
strength/direction, realized-volatility bucket, cross-asset correlation and liquidity
state. Fit any thresholds/models only on the declared past; freeze their parameters.
Show future outcomes separately. Named crisis intervals and synthetic scenario
labels are retrospective/ground-truth annotations, not live signals.

For each regime/pattern/timeframe show observation counts, available-feature counts,
active exposure, turnover, costs, drawdown, hit/exit rates with denominators, and
matched baseline differences. Include no-pattern and component-removal controls
on the same full world. Do not compound disconnected regime days and describe the
result as a continuously tradable regime strategy. Report episode distributions
and contribution summaries; overlapping pattern occurrences are not independent
trades. Insufficient samples stay marked insufficient.

Maintain per-timeframe/per-asset coverage, including 2016–2018 missing native
intraday data, rolling warm-up, incomplete weekly/monthly bars and gaps. Keep
market-regime labels distinct from a fitted strategy router's chosen mode, so the
report does not define success using the model's own output labels.

## 6. Readable report and associated data contract

Deliver a self-contained HTML report with a concise Markdown companion and
machine-readable tables. The report must work from local retained data and link
every summary row back to its run and dataset identities. Prefer the existing
research report infrastructure where practical; no live broker calls are needed
for rendering.

Recommended report flow:

1. **Decision page:** what was tested, full date range, which mechanisms survive
   which checks, what remains untested, and the evidence level for paper readiness.
2. **Full-history curves:** equity, drawdown, exposure, cash, turnover and costs;
   mark warm-up, real/synthetic boundaries, recipe changes and missing coverage.
3. **Market conditions:** filters for regime, scenario, calendar period, asset,
   pattern family, timeframe, cost and seed, with sample counts always visible.
4. **Mechanism comparison:** matched baseline/component results, robust parameter
   neighborhoods, sensitivity plots, and why a candidate is retained or rejected.
5. **Paper bridge:** exact artifact/config compatibility, decision parity,
   operational observations, and historical-versus-paper execution differences.
6. **Data and limitations:** run inventory, omissions/failures, definitions,
   reproducible commands, hashes and downloads.

Retain at minimum these datasets (Parquet for large tables, CSV for compact
exports, JSON for manifests):

- `runs`: policy, complete date range, scenario/seed/cost, recipe/schedule hashes,
  data coverage, status, rejection/failure reason and evidence classification.
- `market_worlds`: source/synthetic OHLCV, execution VWAP/volume, calendar and action
  convention, calibration interval, generator settings and dataset hashes.
- `equity_sessions`: cash, holdings value, equity, P&L components, fees, exposure,
  warm-up/eligibility flag and active recipe for every source session.
- `holdings`, `decisions`, `instructions`, `fills`, `misses` and `transitions`, with
  original signal/execute dates and persistent state references.
- `features_patterns`: actual feature values, availability masks, occurrences,
  signed timeframe contributions and threshold definitions.
- `regimes_episodes`, `metrics`, and paired baseline/component comparisons with
  explicit denominator, time scope and confidence/uncertainty interpretation.
- `paper_parity`: input/artifact/state identities, expected versus planned
  instructions, broker observation timestamps and execution classification.

Do not invent performance numbers, suppress failures, annualize a handful of
paper observations, or present synthetic scenario percentiles as forecast
probabilities. Every chart must identify full-history, active-period, regime-slice,
synthetic-world or actual-paper scope.

## 7. Narrowing the mechanism for the current Docker demo

A read-only audit found `amat-executor` running and healthy, with
`EXECUTOR_DISABLED=true` and `DRY_RUN=true`. Its ordinary v13 planner does not
currently ingest the research GP/MTF/exit/schedule artifacts or persistent research
entry-basis/embargo state. Details and timestamps are in
[p13-paper-integration-audit.md](p13-paper-integration-audit.md).

Therefore the required bridge is functional, not simply a TOML switch. Research
should narrow toward one of: retain the incumbent and display pattern diagnostics;
use one frozen validated recipe; or use a frozen, explicitly scheduled reselection
mechanism. The report must permit "no candidate improves the incumbent" as an
outcome. Favor stable net performance and operational equivalence over the single
largest historical return. Freeze selection and risk criteria before comparison;
reuse verified project promotion criteria where applicable rather than inventing
a favorable threshold afterward.

Before any paper deployment, the plan requires:

1. One immutable mechanism artifact, with complete configuration, feature/eligibility
   contracts, source/data versions, schedule and exit-state semantics.
2. Shared authoritative decision logic behind both simulation and `amat plan`.
   Given the same completed bars and reconciled account/order/exit state, planned
   targets and instructions must match the research replay.
3. Durable paper-side entry bases and embargoes derived from actual fills/actions,
   plus pending-order reconciliation. Backtest average cost is not a substitute
   for the declared initial-entry basis. Missing historical provenance is explicit.
4. Read-only shadow plan comparisons on completed sessions, followed by a distinct
   controlled paper release that preserves current holdings and operational state.
   Broker health and config allowlisting do not constitute performance validation.
5. Paper-specific reports of expected versus observed fill prices/timing, rejects,
   partial fills, restart/idempotency behavior, coverage gaps and reconciliation.

Alpaca documents that its paper environment omits some real execution effects,
including market impact and latency-related slippage. Keep modeled cost/liquidity
stress alongside paper outcomes rather than treating optimistic paper fills as
proof of live performance. See [Alpaca paper trading documentation](https://docs.alpaca.markets/us/v1.4.2/docs/paper-trading).

The container uses a read-only host-source mount: read-only inside Docker does
not stop host edits from changing the mounted planner. Implement P13 in an isolated
research checkout/source snapshot and promote a frozen compatible release later;
do not let experimental shared-runner edits silently change the running demo.
This research made no changes to container settings, submission state, broker
orders or P7's suspension.

## 8. Revised delivery order

1. Freeze the full-span data catalog and reconcile any latest-session extension.
2. Implement full-calendar continuous state and static/no-op/causality tests.
3. Run and report historical policy/component/latency coverage in full.
4. Implement coherent MTF and opening-window synthetic data with invariant tests.
5. Run smoke then extended registered synthetic worlds; expand to nested adaptive
   selection only with its actual fit/replay contract and profiled budget.
6. Produce the readable report and associated data exports, with honest method and
   regime coverage and a short list of mechanisms supported by the evidence.
7. Implement the missing paper artifact/state contract, prove plan/replay parity,
   and package a separately reviewable paper-demo release.

The original proposed 16/76 paths remain the historical switching matrix, not the
total definition of testing completeness. All subsequent scenarios inherit the
same full-calendar and reporting requirements.
