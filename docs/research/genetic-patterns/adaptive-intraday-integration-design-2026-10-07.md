# Adaptive genetic patterns and intraday execution

Prepared October 7, 2026 America/Detroit from the current workspace, existing
research evidence, and a read-only inspection of selected container settings.
This is an implementation proposal. The configuration examples below are new
interfaces to implement, not settings accepted by the current application.
No strategy runtime, deployment configuration, or execution switches were changed.

**Recommended behavior**

Make genetic programming a versioned signal component inside one strategy
decision engine. Trigger a bounded refit every 250 new qualified one-minute
observations, evaluate entries on each completed minute, and manage account/order
events independently. Retain the accepted model while a challenger is training;
replace it only after the configured validation policy passes. A refit need not
produce a replacement or a trade.

The existing daily allocator can provide a slower exposure and regime context.
An explicitly budgeted intraday component can propose entries and exits within
the total portfolio limits. This is a new strategy behavior that requires its
own replay; shortening a daily strategy's polling interval does not create it.

**What exists and what is missing**

| Area | Observed behavior | Required change |
|---|---|---|
| Deployment | Running `amat-executor:phase-b-20260920`, v13 config, `DRY_RUN=true`, `EXECUTOR_DISABLED=true` | Develop and validate independently of deployment; these switches currently prevent order submission |
| Strategy | Daily 63/126/252-session trends, five-session ranking refresh, 2 percentage point rebalance band | Explicit intraday signals, holding periods, entry/exit rules, and allocation budget |
| Pattern schema | `PatternDefinition.timeframe` admits 5m and slower, but no 1m | New version supporting minute bars with explicit units and availability |
| Pattern integration | `Candidate.genetic_pattern` is a research extension; daily adapter changes only positive scores | Shared typed signal component; retain overlay mode and introduce a separately tested independent-entry mode |
| Refresh | `refresh_shadow` preserves formula and calibration dates while reconciling data | A separate adaptive fitting controller; do not overload data refresh with model discovery |
| Planner | `v1-frozen`, last completed daily session, no GP runtime input | Shared causal inference and state transition code used by replay and an intraday planning interface |
| Executor | Daily submit/report phases; maximum ordinary sleep 240 seconds | Independent decision, account reconciliation, and watchdog schedules |
| Plan persistence | One plan file per date; retries reuse it | Immutable plans per decision; retries reuse the exact decision artifact |
| Order identity | Date/config/symbol/side/quantity | Identity including immutable decision ID and intent sequence |
| Risk limits | Session-named order/notional limits checked against a single plan | Durable cumulative limits and pending-order reservations across all session decisions |

Code locations:

- [Strategy configuration](/home/supasuge/Documents/amat-workspace/amat/src/amat/config/models.py:703)
  and [v13 settings](/home/supasuge/Documents/amat-workspace/amat/configs/strategy/amat_v13_return_target.toml:69).
- [Pattern schema](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/genetic/models.py:177),
  [adapter](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/genetic/adapter.py:109),
  [refresh](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/genetic/shadow_refresh.py:67),
  and [symbolic search](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/genetic/symbolic_search.py:396).
- [Planner](/home/supasuge/Documents/amat-workspace/amat/src/amat/planning/__init__.py:46),
  [daemon](/home/supasuge/Documents/amat-workspace/executor/src/executor/daemon.py:740),
  [plan parser](/home/supasuge/Documents/amat-workspace/executor/src/executor/plan_source.py:142),
  and [order identity](/home/supasuge/Documents/amat-workspace/executor/src/executor/submit.py:72).

**Configuration ownership**

Strategy configuration owns behavior that changes the backtest: bar timeframe,
entry cadence, features, labels, training windows, fitting budget, validation,
promotion, exits, cooldowns, allocation, and cost assumptions. Executor settings
own transport and operations: event subscriptions, polling fallback, reconnects,
account reconciliation, and watchdog frequency. Record both effective identities
in operational receipts; avoid contradictory copies of strategy timing knobs.

Keep the behavior policy immutable. Each fitted model is a separate content-hashed
artifact with its data cutoff, feature registry, code identity, window boundaries,
seed, search budget, validation result, parent, and activation eligibility. The
approved policy controls which artifacts may activate automatically; configuration
authorization alone must not authorize an arbitrary replacement model.

Proposed research starting point, with values to compare rather than presumed
optimal parameters:

```toml
[adaptive_patterns]
enabled = true
mode = "shadow"
timeframe = "1m"
binding = "intraday_signal"
refit_every_bars = 250
fit_window_bars = 20000
selection_window_bars = 3000
label_horizon_bars = 15
split_gap_bars = 15
warm_start = true
max_concurrent_refits = 1
refit_timeout_seconds = 120
activation = "next_decision_after_validation"
failure_policy = "retain_accepted_if_valid_else_disable_component"

[adaptive_patterns.search]
population_size = 64
generations = 2
max_depth = 3
max_nodes = 15

[intraday_decisions]
entry_every_bars = 1
signal_exit_every_bars = 1
entry_cooldown_bars = 5
entry_confirmation_bars = 2
regular_session_only = true
```

This excerpt is not a complete deployable policy. Required additional fields
must declare the intraday capital budget, maximum model age, data-age limits,
entry/exit thresholds, maximum holding period, session-end handling, portfolio
risk bounds, and predeclared acceptance criteria. Existing daily score thresholds
and session lookbacks must not be silently reinterpreted as minute parameters.
The split gap must cover the actual overlapping outcome intervals, including
execution delay; a 15-bar setting alone is not proof of leakage prevention.

Proposed executor transport defaults for an initial shadow experiment:

```text
MARKET_DATA_TRANSPORT=stream
ORDER_UPDATES_TRANSPORT=stream
MARKET_DATA_POLL_FALLBACK_SECONDS=5
RISK_WATCHDOG_SECONDS=5
ACCOUNT_RECONCILE_SECONDS=30
IDLE_WAKE_SECONDS=240
```

These are proposed new settings. Entry timing remains bar-driven. Running a
watchdog every five seconds must not launch training, recompute the daily history,
invoke an LLM, or reissue a decision every five seconds. A faster timer only has
value when it reads fresh data and handles an action the strategy actually defines.

**The 250-bar contract**

Count one new qualified timestamp per strategy cohort, not one row per symbol
and not one polling cycle. For a cross-asset model, declare which symbols are
required for that cohort's observation to qualify. Track incomplete timestamps
explicitly; never manufacture observations to meet the trigger. Optional symbols
can be unavailable under a declared per-symbol eligibility policy.

Count only completed regular-session minutes admitted by the ingestion policy.
Carry the counter across sessions, half-days, weekends, and restarts. Duplicate
or corrected bars do not advance it. Store the count, last admitted timestamp,
next threshold, and refit request state transactionally. With continuous valid
bars and a zero count at a 09:30 session open, the 250th minute ends at 13:40;
the actual request happens after ingestion. That is 4 hours 10 minutes of market
observations, not a fixed wall-clock timer and not 250 minutes per symbol.

After initial warm-up, enqueue a refit at each threshold. Persist the request
before dispatch and deduplicate by policy/cohort/threshold/data identity. Allow
one running job and at most one pending latest request. If jobs exceed the
interval, record coalesced or missed refits and alert on the resource problem;
do not claim that every requested fit completed. Continue inference with the
accepted model within its declared age and data-quality bounds.

At a trigger:

1. Freeze the as-observed input prefix and all identities.
2. Remove labels whose outcomes are not yet observable. For next-bar execution
   and a 15-bar holding label, compute availability from actual entry/exit times.
3. Reserve the latest fully matured selection window. Fit on the preceding,
   nonoverlapping 20,000 observations, with purge/gap and separate feature warm-up.
4. Seed the bounded search from prior accepted programs plus fresh variation.
   Reevaluate every candidate on the new window; do not reuse old fitness values.
5. Compare the challenger with the incumbent and a simple baseline on the same
   selection dates and state/cost assumptions. Check effective support, turnover,
   complexity, drawdown, and stability against predeclared thresholds.
6. Publish an immutable artifact and acceptance receipt. Atomically update the
   active pointer only when eligible; rejected or failed candidates leave the
   accepted artifact in place while it remains valid.
7. Activate on the first decision boundary after actual fitting, validation,
   publication, and any declared embargo have completed. Never backdate activation
   to the data cutoff. Preserve positions, entry bases, pending orders, and exit
   ownership when the model changes.

The selection window is repeatedly consulted and is therefore selection data.
It must not be called untouched out-of-sample evidence. Evaluate the entire
adaptive selection process in an outer chronological replay and then in
prospective shadow observations. The trigger interval and window lengths are
themselves strategy parameters whose selection must be accounted for.

The existing search resumes only an identical context. Rolling adaptation needs
a new context each time; warm start means importing program structures into that
new context, not bypassing checkpoint identity checks. With the existing generation
convention, 64 candidates and two additional generations consume 192 slots.

**One shared decision path**

```mermaid
flowchart LR
    A[Minute and quote events] --> B[Validated data and causal feature state]
    B --> C[Every 250 qualified minutes: background fitting]
    C --> D[Validation and immutable model registry]
    D --> E[Shared strategy inference]
    B --> E
    F[Reconciled positions and pending orders] --> E
    E --> G[Versioned decision plan]
    G --> H[Executor checks and paper orders]
    H --> I[Broker order and fill events]
    I --> F
```

Move reusable deterministic pattern contracts/evaluation into an AMAT-owned
module that both research and planning can import. Research owns discovery and
evaluation orchestration. Keep the existing executor-to-AMAT process/artifact
boundary. An optional persistent local planning worker can eliminate repeated
Python startup and historical loading while still exposing a versioned IPC
contract; the executor should not import or approximate strategy internals.

Use one cached causal feature store, updated by new bars. Resample higher
timeframes only at completed boundaries, with availability based on receipt time
and the exchange calendar. Cache feature arrays by data/feature identity and
fitness by data cutoff, program, labels, and costs. Limit memory, worker CPU,
wall time, and artifact retention separately from the execution process.

The current intraday research recipe already has `decision_minutes`, confirmation,
cooldown, stops, and holding periods. Reuse its useful concepts after aligning
execution semantics; it is a separate engine today. Its panel uses a 390-minute
day and full-day completeness gating. A streaming implementation must use the
exchange schedule and prefix-only eligibility: an afternoon gap cannot decide
whether a morning signal was tradable. Do not wrap each minute as a daily
`MarketPanel` session; that would corrupt annualization and daily lookbacks.

Data versions also matter. Alpaca documents initial minute bars followed by
possible `updatedBars` after late trades. Preserve what was available at each
decision, record later revisions, and never rewrite past decisions. A short
settling delay can be tested, but it does not guarantee that a bar is immutable.
Feed identity and entitlement must match the research assumptions.
[Alpaca stock streaming documentation](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data).

**Increasing useful trade frequency**

| Concern | Initial experiment | Meaning |
|---|---|---|
| Entry and signal exits | Every qualified completed 1m bar | Reassess a signal when new feature information exists |
| Protective risk checks | Fresh quote/order events plus 5-second watchdog | Position risk and emergency exits are independent of entry cooldown |
| Order lifecycle | Stream fill/rejection/cancel events; reconcile every 30 seconds | Track partial fills and outstanding exposure |
| Model refit | Every 250 new qualified 1m bars | Adapt the program using a longer rolling history |
| Structural research | Separate bounded offline/nightly experiments | Explore larger grammars without blocking order handling |
| Daily core allocation | Preserve its declared daily/session schedule initially | Provides slow portfolio context while the new component is evaluated |

Alpaca's `trade_updates` stream includes partial fills, full fills, cancellations,
and rejections. Persist event identity, ignore duplicates, and reconcile after
reconnects rather than assuming a stream gives exactly-once delivery.
[Alpaca order streaming documentation](https://docs.alpaca.markets/us/docs/websocket-streaming).

For a small daily-only comparison, test `selection_interval_sessions = 1` against
5 and a lower rebalance band against 0.02 in a separate configuration. These
changes can alter turnover; they do not provide intraday signal generation.
For the requested intraday behavior, explicitly allow the new component to propose
entries within a budget. The current GP adapter cannot turn a zero baseline score
into an entry. Retain that existing behavior for overlay mode and test independent
entry mode as a separate mechanism.

Evaluate cadence, entry threshold, confirmation, cooldown, holding horizon, and
rebalance band as distinct interventions. More evaluations do not imply more
qualifying trades or better net results. Report signal opportunities and rejection
reasons, decisions, submitted orders, fills, and completed trades separately.
Measure net return, net expectancy, turnover, drawdown, exposure, cost sensitivity,
and effective sample size. A trade-count objective by itself rewards churn.

**The intraday plan and executor contract**

Introduce an explicit new plan version rather than placing unvalidated intraday
fields inside the current daily schema. Keep daily v1 behavior covered by parity
tests; route intraday v2 through its own timing and state checks. Include:

- Decision ID, sequence, bar start/end, data cutoff and receipt watermark.
- Strategy policy, model, feature registry, code, and actual input-content hashes.
- Fit/selection cutoffs, validation receipt, model availability and activation time.
- Account/position/pending-order state version used to compute the decision.
- Valid-from and expiry timestamps, execution mode, and a reason per intent.
- Target exposures, requested deltas, and the ownership of existing exit rules.

Derive order IDs from the immutable decision identity and intent sequence. Retrying
the same plan must reuse its IDs; a new decision may have the same symbol, side,
and quantity and still needs a new identity. Deterministic IDs alone do not prevent
two successive plans from buying twice: calculate target deltas against confirmed
positions plus signed remaining orders, reserve buying power, and reconcile state
versions before submission. A cancellation request does not release its reservation
until the broker confirms it. Do not finance buys from unconfirmed sells.

Replace one-plan session checks with persisted cumulative order/notional usage
and conservative pending reservations. Keep a separate emergency risk-reduction
policy so entry budgets/cooldowns cannot accidentally suppress protective exits.
On ambiguous POST outcomes, query by the same client ID rather than blind retry.
Account mutations need serialization; data ingestion and training can run separately.

Persist entry time/basis, fill history, original model and exit rules, realized
P&L, cooldowns, and risk-halt state across restarts. A model replacement should not
silently reset holding duration, loosen an existing stop, or erase a loss limit.
Any supported transfer of position ownership must be a declared, replayed policy.

Subminute quote-driven stops require subminute evidence and execution modeling.
The existing minute engine's next-open, close-triggered exits cannot validate
five-second stop behavior. Broker-native protective orders are a separate adapter
capability to test for supported instruments and order types.

**Implementation sequence and acceptance evidence**

1. Add versioned minute-data, pattern, adaptive-policy, and model-artifact contracts.
   Implement exchange-calendar ingestion, persisted bar thresholds, label maturity,
   and a nonblocking refit controller in research/shadow mode. Include a real
   bounded search invocation; a timer stub alone does not implement refitting.
2. Factor shared deterministic inference and account transitions. Produce continuous
   adaptive replay with timed activation and inherited positions. Compare daily
   baseline, faster cadence alone, frozen GP, and adaptive GP under matched risk
   and costs. Measure fitting and decision latency on the actual hardware.
3. Add the intraday plan version, immutable per-decision storage, cumulative risk
   reservations, order-state reconciliation, and event-driven scheduling. Require
   replay/planner parity at identical observed inputs and supplied account states.
4. Run shadow decisions and collect prospective evidence, then prepare a concrete
   paper deployment with approved policy/artifact identities and rollback. Automatic
   model replacement within that approved policy does not require an operator to
   edit a configuration hash every 250 bars.

Acceptance tests should cover no refit at bar 249, one request at 250, the next
at 500, duplicate/revised bars, late arrivals, missing inputs, half-days, weekend
carry, and restart recovery. Also cover training timeouts, single-job concurrency,
failed validation, stale-model fallback, future-prefix invariance, immature labels,
purged boundaries, publication/activation latency, and retained exit ownership.

Executor tests must cover repeated decisions, identical-quantity distinct plans,
partial fills, pending cancels, unknown POST outcomes, state changes between planning
and submission, cumulative caps, emergency exits, reconnects, and replayed events.
Run both modules' required checks when the plan boundary changes. Broker calls are
unnecessary for the deterministic regression tests.

Use the current baseline as an actual competitor. The retained P13 foundation
report records full-calendar CAGR of 12.50% for baseline versus 10.43%, 11.44%,
and 10.67% for the three switching profiles at 2 bps one way; baseline led returns
at all four tested cost levels. These are existing retrospective findings, not
newly reproduced results or an isolated estimate of the GP contribution.
[P13 foundation evidence](/home/supasuge/Documents/amat-workspace/amat/docs/genetic-patterns/p13-foundation-verification.md:10).

The isolated P13 checkout at `/home/supasuge/Documents/amat-p13-workspace` contains
continuous-state replay work worth reusing after dependency and source-identity
review. Do not overwrite the heavily modified current workspace or treat those
isolated additions as already deployed.

Paper execution validates operation, while fill realism still needs independent
cost and liquidity stress. Alpaca explicitly excludes market impact, queue position,
and latency slippage from its paper simulation.
[Alpaca paper trading assumptions](https://docs.alpaca.markets/us/docs/paper-trading).

Professional-quality automation here means reproducible decisions, independent
risk control, cost-aware selection, reliable order handling, and evidence that the
adaptive process adds value. Neither frequent fitting nor a larger model establishes
professional trading skill or superior returns on its own.
