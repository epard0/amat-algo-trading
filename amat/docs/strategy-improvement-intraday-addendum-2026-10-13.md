# Intraday trading and minute refitting — implementation addendum

**Scope expansion recorded October 14, 2026 (America/Detroit).**

**Scope expansion confirmed by the user:** “Expand to intraday trading and minute refitting as well.” Holding/overnight policy, data scope and risk settings remain unresolved. No implementation, data download, model fit, deployment or broker submission is performed by this planning update.

This addendum extends the [main implementation plan](strategy-improvement-implementation-plan-2026-10-13.md). Daily improvements remain in scope; intraday trading and adaptive fitting are now first-class workstreams rather than deferred possibilities. Activation of a replacement remains a separate decision. The [existing minute-refit contract](genetic-patterns/minute-refit-experiment-contract-2026-10-08.md) supplies useful requirements, but its proposed cadence and data choices are not automatically user-approved parameters.

## 1. Prediction and decision contract

Goal: determine whether minute information and causal periodic refitting improve net, risk-constrained ETF portfolio decisions relative to daily v13 and simpler fixed minute signals.

Decision owner: the user selects mandate, downside tolerance and eventual activation. AMAT computes signals, allocation and policy decisions. Executor observes the actual account, enforces its independent submission limits and owns paper orders.

Model output: per-asset score/target adjustment, admissible symbols and abstention reason, model ID, feature schema, full information cutoff and prediction timestamp. Portfolio decisions additionally identify policy/account state, target weights and cash-feasible authorized quantities.

Success: incremental net portfolio value under an agreed risk objective, plus causal/reproducible operation. More predictions, fits or trades alone do not qualify. Guardrails include tail exposure, costs, turnover, stale decisions, duplicate orders, refit latency, memory/CPU and model stability. Capital/risk state must survive model changes.

Working scope: existing 11 ETFs, long-only, unlevered, regular exchange sessions. Short selling, extended-hours trading and real-money execution are not inferred from the scope expansion.

## 2. Reuse and gaps identified in the code

- `autoresearch/intraday/data.py` contains raw one-minute partitions for four assets. It builds a 390-minute tensor and marks an asset-day valid using the entire day's completeness. That offline quality mask must not decide tradability before the missing future minute occurs. Add event-time readiness separately.
- `autoresearch/intraday/engine.py` contains completed-bar features and next-open research execution, fixed-notional sleeves and flat-at-session-end behavior. Reuse validated feature formulas/test ideas; its assumptions are not the new mandate or common capital-constrained portfolio ledger.
- `autoresearch/intraday/specs.py` limits recipes to four symbols and contains optional short directions. Create a new explicit contract for the chosen universe/mandate rather than silently changing old recipe meaning.
- `genetic/shadow_intraday.py` records adjusted minutes for feature diagnostics. Its prices/volume must not be treated as raw executable observations.
- `genetic/native_intraday.py` labels native 15-minute/hourly aggregates as coverage research, not inference inputs. They do not replace missing one-minute data.
- Existing genetic modules provide candidate/program/artifact utilities; they do not establish a functioning causal 250-minute online refit loop.
- `executor/daemon.py` schedules daily submit/report phases. Intraday decision delivery needs a separate event-driven path; shrinking the existing daily poll interval is insufficient.

Preserve existing daily and four-asset research results. Proposed new boundaries: minute contracts/ingestion, event replay, shared features, model artifacts, refit coordinator and intraday plan adapter. Final filenames should follow the existing packages after the implementation dependency audit; do not create a parallel agent framework or model-service stack unnecessarily.

## 3. Minute-data plan

Known inventory is mixed:

| Source | What is established | Limitation |
|---|---|---|
| `data/minute/alpaca-sip-2022-2026-09-14` | Raw one-minute manifest for SPY/QQQ/IWM/GLD, declared 2022–2026 | Four assets; archive bounds are not a completeness guarantee |
| Inspected P7 receipt `2026-09-28.receipt.minutes.json` | All 11 ETFs, adjusted data, August 17–September 28, 201,094 records including non-regular timestamps | Short interval; adjusted price/volume are unsuitable as raw execution inputs |
| Earlier P13 contract inventory | References a larger June–September adjusted receipt and native aggregates | Reverify actual files and hashes before any coverage claim |

Recommended research destination: verified raw minute history for all 11 assets over 2022 through the latest completed available session, plus sufficient earlier daily warm-up. Fetch only missing raw partitions after audit and reuse existing verified partitions. Provider access and actual coverage may limit dates; no paid subscription or cloud compute is assumed. Download duration/storage/API limits need a measured preflight before a large backfill starts.

A four-asset pilot is useful for debugging but is a different strategy universe; it cannot establish improvement over the 11-asset v13 portfolio. A recent full-universe pilot tests mechanisms, not durable annual performance.

Data contract: key `(symbol, bar_start, revision)`, UTC timestamps plus exchange calendar, event end, available/arrival timestamp or declared historical availability assumption, raw OHLCV, price/volume basis, corporate actions and partition hashes. Historical finalized bars do not reproduce their original arrival/revision history; label that assumption and stress delays/revisions separately. Features may use a causal adjusted series with explicit action treatment; ledger prices and share quantities use raw observations. Do not infer raw prices from current fully adjusted receipts.

Use the actual session calendar for holidays, early closes and daylight-saving changes. Do not require 390 bars on an early-close day. Treat zero volume, absent bars and stale symbols distinctly. Features/eligibility at time t may inspect only admitted observations through t. A future missing minute must not invalidate earlier decisions. Revisions retain identity and may invalidate a model/input receipt without creating duplicate decisions.

## 4. Five clocks and proposed defaults

These are proposed technical settings for review, not final registered parameters:

| Clock | Proposed initial behavior |
|---|---|
| Ingestion | Admit each completed one-minute interval after its declared availability/latency |
| Inference | Recompute targets every five qualified minutes; unchanged model between fits |
| Risk | Evaluate on each valid mark/account event; account events can block exposure independently of inference |
| Order management | Process fill/cancel/reject events and reconcile outstanding commitments |
| Training | Trigger after 250 newly qualified global minute timestamps, persisting across sessions/restarts |

Count one shared portfolio minute, not eleven asset rows. Define qualification from information available at the watermark; blocked/stale features produce explicit abstention. Duplicate/revised observations do not increment the trigger counter. Refit cadence is distinct from training-window length, label horizon and trading cadence.

Proposed mechanism-pilot settings: 20 completed sessions of rolling fitting history and 15-minute forward labels, with a purged chronological pre-activation validation segment. These are engineering starting proposals only. Final training length, label horizon, minimum mature sample size, GP grammar/population/complexity, CPU budget and acceptance margins must be frozen in the protocol after the holding mandate and risk objective are resolved. For an overnight/swing label, the 15-minute target may be inappropriate and must be replaced deliberately.

One-minute observations do not imply that a completed close can fill at that same close. Execution must occur strictly after signal availability plus processing/submission latency. If the modeled next-bar open precedes that time, defer or use a supported later execution assumption. Stress quote/spread absence, adverse selection, halts and gaps; one-minute OHLC cannot establish exact intrabar ordering when stop and profit levels both occur.

## 5. Unified replay and portfolio state

Build an event-time minute replay adapter over shared accounting and AMAT policy functions. Do not relabel minute rows as daily sessions: feature units, volatility annualization, exchange calendars and fills would become wrong. Treat intraday-to-daily returns consistently; compute portfolio daily/overnight risk explicitly rather than blindly annualizing 252×390 minutes when the mandate spans closures.

Retain a single capital/cash ledger for any actually combined strategy. Maintain realized holdings, pending orders, cash commitments, risk state and lot identities through model activations. Training cannot reset drawdown, flatten positions or overwrite pending orders. Separate daily and intraday research tracks may have separate diagnostic ledgers, but their equity curves cannot simply be averaged and presented as a feasible combined account.

The holding mandate controls end-of-day behavior: flat-at-close needs a causal liquidation cutoff and real missed-liquidation outcomes; overnight holding needs gap risk, corporate actions and cross-session state. Neither policy is silently inherited from the old prototype.

## 6. Fitting and activation lifecycle

1. Freeze the admitted feature prefix and only labels whose full outcomes are observable.
2. Record training trigger ID, watermark, data/source/config hashes, seed and parent artifact.
3. Fit asynchronously while inference continues on the incumbent.
4. Allow one fit per policy/data version; coalesce additional triggers into a bounded pending request.
5. Evaluate on declared pre-activation validation data using the same economic/cost objective and fixed complexity rules. Never use post-activation returns to choose a model that is retroactively applied.
6. If accepted and timely, activate model plus preprocessing atomically at a subsequent decision boundary.
7. Record fit start/end, label maturity cutoff, validation results, activation time and first decision using the model.
8. If failed, late or rejected, retain the incumbent and record the failure. Never retry with a better seed while concealing the original outcome.

Warm-start is disabled for the first mechanism comparison unless the registered procedure explicitly includes it. Typed program/model artifacts include all transforms and feature schema; loading must validate hashes and permitted operations. No arbitrary code evaluation or untrusted executable deserialization is needed for GP programs.

Rollback swaps to the last verified artifact; it does not retrain, reset the account or resend previous orders. Data staleness, expired models and corrupt artifacts block increases under a declared fallback while valid risk-reducing intents remain subject to account/order checks. An emergency fallback must not silently switch to a different investment strategy.

## 7. Intraday comparison design

Retain the original daily v13 replay as a separate unchanged reference. In the minute adapter, compare four matched controls:

| Arm | Signal/training policy | What the comparison isolates |
|---|---|---|
| M0 | Prior-completed-daily v13 signals on the declared common intraday execution/risk mandate | Slow-signal control |
| M1 | Conventional minute features, frozen parameters | Value of simple minute information |
| M2 | GP fitted once on the training prefix, then frozen | GP contribution without periodic adaptation |
| M3 | Same GP method with periodic 250-minute refit | Adaptation increment versus M2 |

Hold universe, capital, shared decision clock, fill assumptions, costs, risk policy and overnight mandate constant within M0–M3. If flat-at-close or intraday risk differs from original v13, label M0 as an adapted signal control rather than unchanged v13. M3 versus M2 measures adaptation; M3 versus v13 combines multiple effects.

Start with four arms at 2/6/10-bps costs: **12 registered primary minute portfolio paths**, in addition to the 27 daily paths. These paths may contain many fits; fitting work is a separately declared compute budget. At a 250-qualified-minute cadence, a normal 390-minute day averages about 1.56 triggers after sufficient initialization, across session boundaries—not eleven times that number.

First run deterministic synthetic event fixtures and a bounded mechanism pilot with a declared maximum eligible timestamp/refit count. The exact pilot dates and cap depend on the chosen data route; register them before fitting. If it passes engineering gates, run chronological full-process evaluation that repeats training, validation and activation at each historical trigger. Do not use one fitted program across all historical outer folds and call it adaptive out-of-sample evidence.

No inference-cadence grid, new signal-family search or unlimited GP optimization is implied. Each expansion gets a registered budget and unchanged comparison conditions. Persist resumed counters and trial IDs; the result includes unsuccessful fits and inactive intervals.

## 8. Intraday execution interface

Add an opt-in intraday paper adapter alongside the daily scheduler. Each intent carries decision/model/policy/account-state identity, data watermark, valid-after/expiry, cumulative authorized quantity and idempotency key. Replacing a model must not duplicate a decision or reset session order/notional limits.

Handle cancel/replace races, partial fills, outstanding cash, trade halts, disconnect/reconnect, session close and late packets. Do not rely on local timeout to infer an order failed. Reconcile broker-confirmed outcomes before replacement. Preserve the existing standard-library-only executor and subprocess boundary unless a separate dependency change is explicitly justified.

A recorded stream and stub broker suffice for offline end-to-end verification. Paper activation remains a subsequent explicit decision; this planning request creates no service, live feed, automation or order submission.

## 9. Tests, metrics and rollout gates

Required fixtures cover duplicates, out-of-order and revised bars; missing future observations; stale symbols; holidays/early closes/DST; split/dividend changes; restart at counters 249 and 250; training failure/latency/overlap; label-boundary purging; atomic activation; rollback with pending orders; corrupt artifacts; partial/failed orders; repeated callbacks; liquidation failure and overnight gaps.

Verify prefix/future perturbation invariance of features, decisions, models eligible at each timestamp, fills and risk state. Training-completion timing must affect only later decisions. Check batch/stream feature parity on the same admitted prefix and full accounting. Compare all four arms on identical usable calendars without trimming unfavorable/missing periods.

Report net growth, daily and intraday drawdown/tails, overnight P&L, turnover, concentration, target shortfall, fill costs, model age, accepted/rejected fits, decision abstentions, stale minutes, fit/inference latency percentiles, CPU/memory and activation history. Assess uncertainty with session blocks and whole-process replays; minute rows are not independent observations.

Expanded phases: data audit/backfill contract → causal event replay → conventional-minute baseline → fixed GP → refit coordinator → offline paper adapter → matched research evaluation → review → optional shadow/paper activation. Daily config-only comparisons may proceed independently after plan confirmation. No promotion criterion is based on infrastructure passing alone.

## 10. Remaining confirmations

1. Holding/overnight mandate: intraday-only flat at close, intraday decisions with overnight holdings, or separate swing and intraday research tracks.
2. Data route: longer all-11-asset raw minute history (recommended for comparable strategy evidence), longer four-asset pilot, or recent all-11 mechanism pilot. The latter two restrict the claim and do not replace full-universe validation.
3. Original investment/risk question remains pending: growth versus downside priority, maximum acceptable drawdown, one-day stress budget and breach action. Reentry and cash/settlement policy follow those answers and the actual account contract.

Proposed technical defaults—regular sessions, long-only/unlevered, five-minute inference, 250-minute refit trigger, 20-session fitting and 15-minute pilot labels—are reviewable placeholders. They are not treated as confirmed merely because intraday scope was selected.
