# Strategy improvement implementation plan

**Status: intraday/minute-refitting scope confirmed; remaining mandate and risk decisions await user confirmation.**

The [intraday implementation addendum](strategy-improvement-intraday-addendum-2026-10-13.md) is part of this plan and supersedes the earlier deferred-intraday scope. No strategy implementation has begun. This document plans the abilities described in the October 13 improvement review. It does not register a research protocol, change strategy parameters, promote a candidate, modify an executor deployment, or authorize broker submissions.

The current interpretation of the request is: implement the research and execution capabilities needed to assess the proposed improvements, retain v13 as an unchanged reference, and require a separate decision before activating a selected replacement. The user has requested comprehensive planning and ambiguity confirmation before implementation.

## 1. Intended outcome

Deliver a reproducible comparison of eight focused challenger policies against retained v13, together with explicit risk policies, cash-feasible order formation, diagnostic reporting and a path to consistent paper execution. Establish which changes help net portfolio performance and which merely reduce exposure, increase trading or exploit already inspected history.

The strategy remains long-only, unlevered and based on the existing 11-ETF universe unless the user chooses a different scope. The planning assumption is a several-days-to-several-weeks investment horizon. Intraday trading and minute refitting are now confirmed in scope. Their data, holding and timing contracts are detailed in the addendum; order-book-level execution is not implied.

The source evidence is the [improvement review](../../amat/outputs/analysis/2026-10-13-strategy-improvement-review/report.md), [YTD validation](../../amat/outputs/analysis/2026-10-13-ytd-leading-strategy-validation/report.md) and [nested-search findings](../../amat/outputs/analysis/2026-10-08-nested-strategy-search/findings.md).

## 2. Findings that shape the implementation

| Capability | Existing support | Required work |
|---|---|---|
| Metals and individual target caps | `amat.portfolio.apply_constraints` already applies symbol, cluster and gross caps | Frozen challenger configs; reporting of actual holdings drift; any explicit breach-reduction policy |
| Volatility-aware allocation | `AllocationConfig.volatility_power` already supports 0–1 | Isolated config arm and matched-risk controls; preserve current aggregate sizing |
| Ranking cadence | `select_on_schedule` refreshes rankings periodically while checking absolute eligibility daily | Daily-refresh arm with the existing schedule anchor and warm-up preserved |
| Rebalance tolerance | Simulated and plan sizing support minimum weight changes | Separate 1% band arm; quantify turnover and target tracking |
| Shorter trend horizons | Existing trend configuration supports alternate horizon tuples | Two fixed challenger configs; same full-history/warm-up policy; holding-time diagnostics |
| Legacy daily-loss/drawdown controls | The frozen research engine does not enforce the legacy risk block | New explicitly named/versioned risk policy; no silent reinterpretation of old fields |
| Risk-contribution limit | Current allocator explicitly raises when enabled | New implementation and separately specified semantics; not a simple switch |
| Stateful risk in paper planning | Current `planning.build_plan` is stateless and consumes supplied actual equity/positions | Explicit actual-account state input, checkpointing, restart/reentry semantics and plan provenance |
| Cash-feasible buys | Simulation settles sells first and then funds alphabetical buys; buys can be rejected when cash is short | Fee-aware proportional sizing; reserve; pending commitments; confirmed-proceeds contract |
| Broker enforcement | Executor has config-digest authorization, notional/order limits, account reconciliation and submission interlocks | Keep these independent checks; add compatible policy-state/cash metadata without importing AMAT |
| Trial registration | Return-target study freezes sources/configs but derives from v6 and hard-codes old dates/snapshot | Reuse evidence utilities, not the old study's baseline or selection rules |
| Reports | Daily equity/attribution and several metrics exist | Consolidated target-versus-held risk, asset/cluster P&L, residual notional, effective observation and trial diagnostics |

Code anchors:

- `amat/src/amat/config/models.py`
- `amat/src/amat/portfolio/__init__.py`, `cross_section.py`
- `amat/src/amat/backtest/runner.py`, `exits.py`
- `amat/src/amat/execution/__init__.py`, `intent.py`
- `amat/src/amat/planning/__init__.py`
- `amat/src/autoresearch/objective.py` and existing research registration/ledger utilities
- `executor/src/executor/plan_source.py`, `submit.py`, `safety.py`, `portfolio_monitor.py`, `portfolio_checkpoint.py`

The checkout already contains substantial uncommitted work. Implementation must preserve it, record exact working-file hashes and avoid resets or substituting a remote clean checkout for the actual runtime.

## 3. Scope and delivery boundaries

**Confirmed expanded scope:** all eight research arms, risk/cash-policy infrastructure, diagnostics, offline simulator-to-paper-plan parity and paper-ready interfaces, plus intraday trading and minute-refitting capabilities as specified in the addendum. No promotion, deployment, automatic research monitor, paid model-provider usage or broker orders are included.

The initial release should support completed daily decisions with next-session execution. Intraday execution and minute-model refitting are planned workstreams. Their operating parameters remain subject to the addendum decisions. A new reversal model, tax optimization and live cash-instrument trading remain separately gated extensions. The plan below specifies their prerequisites so they are not accidentally implied by a daily feature release.

Implement capabilities without choosing a winner in advance. New policy blocks default to disabled and must preserve existing config serialization, digests, numerical decisions, fills and equity paths when disabled. Keep the promoted v13 file unchanged. Assign explicit identities to challenger configs and policy implementations; do not label experimental policies as an already promoted new strategy version.

## 4. Architecture and contracts

### 4.1 Decision pipeline

Use a common AMAT policy pipeline for simulation and inert plan formation:

1. Admit only completed, valid observations and explicit account state.
2. Compute existing signals and ranking on the original schedule.
3. Apply allocation, volatility targeting, symbol/cluster/gross caps.
4. Apply the enabled risk policy, which can only reduce exposure or block increases.
5. Recompute final target risk, exposure and constraint diagnostics.
6. Form cash-feasible intended quantities using known marks, fees, reserve and commitments.
7. Execute only through the existing simulated or paper boundary.
8. Reconcile realized outcomes and update state for the next eligible decision.

A risk reduction must not be suppressed by the ordinary rebalance band or minimum notional without an explicit recorded reason. A pending order does not become a filled holding. All accounting remains Decimal-based; numerical risk estimates may use validated finite arrays.

### 4.2 Configuration and policy versioning

Add typed, opt-in sections for active risk policy, cash execution policy and diagnostic controls. Validate contradictory enabled/disabled settings, invalid thresholds, non-finite values and unsupported engine combinations. Keep the legacy risk block inert on the original engine and state that fact in diagnostics rather than retroactively changing its meaning.

A configured but unimplemented protection must fail visibly, not silently disappear. Risk policies identify their observation clock, action, reentry policy, stress-set hash and implementation version. Cash policies specify reserve, fee buffer, reference-price buffer and treatment of pending commitments/proceeds.

### 4.3 State and responsibility

AMAT owns policy mathematics and decisions. Executor owns actual broker observations, durable actual-account state and order submission. Executor continues communicating through subprocess/JSON; it gains no AMAT Python dependency and no independent copy of strategy signals.

Introduce an explicit risk-state envelope with account/state identity, policy/config identity, observation/session timestamps, cash-flow-adjusted equity reference, high-water mark, trigger status, cooldown/reentry state, pending commitments and evidence hashes. Simulation constructs it from simulated account events. Paper mode constructs it from reconciled actual observations.

Do not initialize a paper account's drawdown from simulated historical wealth. At first activation, use a declared actual-account anchor or an explicitly verified actual history. Contributions, withdrawals and cash corrections must not create artificial gains/drawdowns. Policy/config transitions require a migration receipt and a declared reset/carry rule. Missing or stale required state blocks increases; risk-reducing actions still require valid marks, holdings and order authority.

### 4.4 Artifact schema

Plans and run receipts need the policy identity, state hash/input cutoff, target weights before/after risk, target/held concentration, stress loss, trigger reason, reserve/budget, original/scaled quantities and residual reasons. Decide the exact schema version after auditing consumers: essential enforcement changes require a new supported contract/capability gate, so an older executor cannot silently ignore them. Existing plans retain existing semantics.

## 5. Capability implementation details

### 5.1 Allocation and realized concentration

Use existing cap and volatility-power fields for the first three independent challengers. Add reporting for target weights and actual marked holdings, including drift above configured target caps.

An optional holdings-breach policy compares observed holdings to declared limits and schedules reductions at the next permissible execution. It records detection and execution delay. Neither target nor observed-holdings checks guarantee an intraday ceiling or prevent an opening gap.

If risk-contribution limits are included, define component variance as `w_i * (Cw)_i` and explicitly handle non-positive/offsetting contributions and zero portfolio variance. Prefer a separately versioned allocator implementation rather than inserting a naive cap into frozen v1. Use deterministic scaling that never increases any exposure and recomputes all relevant caps/risk. This capability remains a separate policy arm until its exact semantics and threshold are confirmed.

### 5.2 Stress and drawdown policies

Declare joint shock scenarios before evaluation, covering metals, equities, rates, commodities, correlation convergence and gaps. Report scenario loss as a designed stress measure, not a probability or forecast. A stress cap scales proposed risk downward; it must not optimize scenarios around the January 30 outcome.

Drawdown and daily-loss thresholds are user decisions. Specify whether a trigger blocks increases, scales positions or exits; define reentry/cooldown and state persistence. Start with a completed-session policy for the recommended daily scope. A trigger evaluated after close schedules next-session action; retain the triggering loss and subsequent execution gap in every replay.

Faster volatility estimates and 12%/15% aggregate targets are optional registered risk-policy comparisons, not silent changes to the eight-arm campaign. Include a constant-exposure control calibrated on earlier data to distinguish useful timing from simply investing less.

### 5.3 Cash sizing and order sequencing

Use a pure AMAT quantity-budget function with known reference prices, fee/reserve buffers and pending cash commitments. Scale positive quantities proportionally when the known budget is insufficient; apply deterministic precision/minimum-size handling and retain residual notional. Do not spend estimated proceeds as if they were confirmed or assume every account has identical settlement/buying-power rules.

For paper-ready staged rotations, execute authorized sales, observe confirmations/partials and refresh actual cash, then request only the permitted buy-budget calculation through AMAT. Do not regenerate the full plan and accidentally duplicate sales. Original intent identity, cumulative filled quantity, remaining authorized quantity and idempotency persist across retries. Partial or failed sales cannot justify an unfunded buy; expiration ends the remaining batch.

The daily simulator may retain a documented aggregate-window fill assumption, but cannot claim tick-level sell-confirm-buy timing from one VWAP bar. Exact staged intrawindow timing requires finer observations. Do not use the completed window's price/volume to size an earlier decision. If this limits simulator/paper parity, report the limitation and run a separate minute execution replay before deployment eligibility.

### 5.4 Horizon and cadence

Create independent configs for daily ranking and the 1% band, preserving other parameters. Shorter-horizon arms preserve the common 252-session warm-up, full-history schedule anchor and portfolio state. Confirm that horizon-agreement logic remains meaningful and deterministic.

Add lot/episode-aware holding-duration diagnostics with corporate-action handling, left-censored opening positions and right-censored still-open positions labeled explicitly. Fills are not independent round trips. Future-return horizon diagnostics account for overlapping observations and are diagnostic outputs, not a new optimization target selected after looking at results.

### 5.5 Execution realism, cash yield and other extensions

Retain 2/6/10-bps cost comparisons and execution-lag/participation stress. Add configurable conditional cost-model interfaces and observed implementation-shortfall fields. Calibrate only from an earlier or prospective observation window; never count spread twice. Later execution windows require their own verified bars.

Support cash-rate inputs only when a valid historical/actual rate source and eligible account policy are declared; preserve zero as the original baseline. A tradable cash ETF is a portfolio instrument with orders and constraints, not an interest-rate field. Account-specific taxes, reversal signals and minute GP refitting require separate agreed data, research and implementation scopes.

## 6. Research design and compute limits

The first fixed comparison consists of the following eight challengers and shared unchanged v13 baseline:

| Arm | Sole declared intervention |
|---|---|
| A1 | Metals target cap 45% → 30% |
| A2 | Individual target cap 45% → 25% |
| A3 | Allocation volatility power 0 → 1 |
| B1 | Ranking interval 5 sessions → 1 |
| B2 | Rebalance weight band 2 percentage points → 1 |
| C1 | Trend horizons 63/126/252 → 21/63/126 |
| C2 | Trend horizons 63/126/252 → 10/21/63 |
| D1 | Declared fee-aware proportional cash policy with 1% reserve |

D1 is one grouped execution-policy intervention. If independent effects of reserve versus proportional sizing are desired, register two additional ablations before inspecting D1 outcomes. These settings are proposed comparisons, not preselected winners or guaranteed safe levels.

Start with **27 full-history replays: 9 configurations × 3 cost levels**. Slice those continuous paths into declared retrospective periods without extra fitting. This avoids redundant replays while preserving initial cash periods, pre-2026 holdings and schedule state. Explicit risk-policy arms, combination trials, perturbations and generated-market runs need separately registered budgets. No broad optimizer, new GP search or paid API work is included by default.

Freeze source/config/data hashes, baseline promotion identity, recipes, split definitions, cost/execution assumptions, objective, trial budget, failure policy and acceptance rule before challenger outcomes. The old return-target script cannot be used unmodified because its baseline is v6 and its dates/data/target rules are hard-coded.

The verified dataset currently ends October 12; try a normal data refresh once at implementation time and record its outcome. Never silently substitute an incomplete session or relabel previously inspected dates as untouched. Retrospective 2016–2026 evidence remains exposed to prior research. Prospective observations begin only after the new policy/protocol is frozen.

For adaptive fitting, rerun fit/selection inside chronological outer folds with purged label boundaries and prefix-only calibration. Fixed recipe comparisons and trained adaptive comparisons must be labeled separately. Candidate failures stay in the trial denominator. Do not replace failed seeds with luckier ones.

Retain DSR/PBO as selection diagnostics only when the required candidate series, trial history and assumptions are available. Report effective-trial sensitivity or unavailable status rather than inventing a single precise score. Paired block resampling uses shared session indices and several block lengths. Designed stress-world counts are not probabilities of future market regimes.

## 7. Acceptance criteria and tests

### Engineering acceptance — required regardless of performance

- Disabled-policy parity: retained v13 targets, fills, missed events, costs and equity exactly match the captured baseline under unchanged inputs.
- Exact accounting; finite risk inputs; no unintended leverage, short holdings or negative cash.
- Prefix/future-mutation checks for decisions, risk state, orders, fills and label maturity.
- Deterministic ties, quantity rounding, partial fills and research resume behavior.
- Risk activation and next-window behavior preserve the full trigger-day/gap loss; no retrospective loss clipping.
- Holdings drift, correlation/variance edge cases, all-cash state and binding constraints are reported correctly.
- Cash-budget invariants include fees, reserve, pending orders, partial/failed sales, gaps, splits, minimum notional and repeat submissions.
- Paper planning receives actual state; no synthetic high-water mark; cash-flow adjustment, restart, stale/corrupt state and account/config changes are tested.
- Artifact consumers reject unsupported essential policies; no silent downgrade.
- AMAT remains unable to submit broker orders; executor retains digest authorization, expiry, reconciliation, clamping and idempotency checks.

Reuse and extend existing portfolio, execution/accounting, cross-sectional, plan and protocol tests. Add focused tests for new stateful/cash contracts and meaningful offline integration cases. Run targeted tests during implementation, then affected-module suites, Ruff and mypy gates for changed modules. Broaden to full local non-network suites at the integration milestone; do not run paid/live-provider tests.

### Research acceptance — thresholds need user confirmation

Separate successful implementation from evidence that a challenger improves the investment strategy. A completed feature does not automatically earn promotion.

Recommended objective: net growth relative to baseline and a declared benchmark, subject to an explicit downside budget. Confirm maximum acceptable drawdown, daily stress budget, tolerated return sacrifice and desired confidence/economic-effect standard before freezing selection. Report SPY/QQQ opportunity cost plus a simple fixed multi-asset and prefix-risk-matched control where appropriate.

Promotion evidence includes full-calendar net returns, tail expected shortfall, maximum drawdown and recovery, asset/cluster attribution, actual/target exposure, cash drag, turnover, residual order notional, holding times, cost/lag sensitivity, neighborhood stability and trial-aware uncertainty. A prospective operational pilot verifies execution/state behavior but a fixed number of days does not guarantee statistical power.

## 8. Implementation phases and deliverables

| Phase | Work | Concrete exit condition |
|---|---|---|
| 0 — confirmation | Resolve scope, horizon and risk objective; finalize decision register | User choices recorded; open thresholds explicit |
| 1 — baseline capture | Snapshot relevant working sources/configs/data; reproduce baseline; identify consumers | Immutable baseline receipt and compatibility map |
| 2 — research surface | Frozen protocol/CLI, seven config-only challenger arms, trial ledger and path-slicing metrics | Config-only comparisons ready with no default-strategy changes |
| 3 — active risk policy | Typed config, monotone risk decisions, state envelope, target/held diagnostics, simulator integration | Accounting/causality and disabled-policy parity pass |
| 4 — execution policy | Reserve/fee-aware sizing, residuals, staged-confirmation contracts, D1 | Cash/idempotency tests pass; aggregate-window limits documented |
| 5 — paper compatibility | Actual-account state, checkpointing, plan/subprocess contract, offline executor fixtures | Same observable state yields the same authorized policy decision; no broker submissions |
| 6 — registered evaluation | Run bounded campaign, sensitivity and agreed controls; retain failures | Evidence package and candid winner/no-winner report |
| 7 — review and optional activation | Present candidate, config hash, validation and rollback receipt | Separate explicit activation decision; prospective paper observation if authorized |

Deliver source/config changes and focused tests, reproducible commands, a frozen protocol, all receipts/trial outcomes, equity/drawdown and concentration charts, decision-to-fill diagnostics, a comparison report, operator documentation and a rollback procedure. Any future paper activation binds the exact selected config/policy/state compatibility to authorized digests. Existing positions require a transition plan rather than a simulated account reset. No deployment or order submission occurs during this planning request.

Dependencies: phase 1 precedes numerical changes; phase 3 state semantics precede phase 5; phase 4 cash semantics precede D1 evaluation. A–C config studies do not wait for minute-data infrastructure. Intraday risk/refit work is now included, with the additional phases, contracts and tests in the addendum.

## 9. Ambiguities requiring confirmation

1. **Scope — resolved.** The user selected expansion to intraday trading and minute refitting. These join the daily research/risk/cash workstreams; promotion/deployment remain later decisions. Additional data/overnight choices are recorded in the addendum.
2. **Actual holding horizon.** Recommended working interpretation: several days to a few weeks. Alternatives: intraday/1–2 days, or several weeks to months. This determines whether daily bars and the planned signal families answer the intended question.
3. **Investment objective and risk tolerance.** Confirm growth-first versus downside-first, maximum acceptable portfolio drawdown, stress-loss budget and permitted return sacrifice. These thresholds remain unset; a volatility target is not a guaranteed loss ceiling.

Technical defaults proposed for confirmation with the plan: unchanged 11 ETFs; long-only/unlevered; preserve v13; daily/next-window controls; 1% cash-reserve challenger; actual cash-flow-adjusted state with no inherited synthetic high-water mark; explicit reentry policy to be finalized alongside risk thresholds; 27 initial fixed replays; no broad search/paid APIs; no promotion or deployment. Reversal, cash-instrument trading and taxes remain separate scoped extensions. Minute refitting is now included under the addendum.

The plan is intentionally not a registered strategy protocol until these choices are resolved. The confirmed expanded scope includes paper-compatible interfaces and offline tests. It does not activate an executor deployment.
