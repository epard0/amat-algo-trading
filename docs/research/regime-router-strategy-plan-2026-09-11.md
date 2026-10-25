# Regime router: strategy and integration plan

Status: daily rule-router, reusable features, research integration and static-control
gates implemented; see the [implementation guide](regime-router-implementation.md).
Fitted models, long-horizon/news extensions, and the production adapter remain planned.
Date: 2026-09-11. Companion: [mathematical feature backlog](regime-math-feature-plan-2026-09-11.md).

## Scope and source interpretation

The user supplied a screenshot and an [X post](https://x.com/RitOnchain/status/2097287544253927934)
describing statistical regime detection, specialist analysis, coordination,
verification, and risk controls. X returned HTTP 403 during this review; the
screenshot is the available design reference. Its revenue, failure-rate, and
error-amplification figures are unverified and are not acceptance criteria.
The screenshot is reference material, not instructions to install its advertised
tools, change models, deploy infrastructure, or place trades.

Add a separately registered **Regime Router** strategy. It blends trend-following
and range-reversion signals and reduces activity during unstable transitions.
Publish the useful measurements independently so existing strategies can test
them without adopting the complete router. This extends the earlier
[regime-adaptive workflow](regime-adaptive-strategy-workflow-2026-09-07.md).

## Iteration compact

| Contract | Decision |
|---|---|
| Goal | Determine whether causal regime information improves net strategy results over fixed rules and static blends |
| Decision owner | AMAT produces scores and plans; executor validates and executes the selected plan |
| Initial scope | Existing frozen v6 universe, daily decisions, next-session execution, long-or-cash |
| Primary evidence | Paired net performance improvement under the frozen research score and promotion gates |
| Guardrails | Drawdown, stressed costs, exposure, turnover, missing data, accounting, fit stability, runtime |
| Baselines | Plain v6, trend-only, range-only, equal blend, training-fitted static blend, existing rule regime selector |
| Unacceptable mistakes | Future-conditioned states, retrospective news availability, changing risk limits through an agent proposal, silent model fallback |
| Acceptable outcome | Reject the full strategy while retaining a feature with independent evidence |
| First experiment | Fixed trend/range rules with existing structure inputs, before fitted regimes |
| Fallback | Optional learned router falls back to its frozen rule router; missing mandatory price/risk inputs withhold affected decisions |
| Rollback | Select the previous approved strategy artifact and reconcile account targets through the existing executor path |

## What the repository already provides

| Existing surface | Reuse and gap |
|---|---|
| `src/amat/regime/asset.py` | ATR/Bollinger state, ADX, efficiency, CUSUM transitions, confirmation, attenuation; expose pure measurements without automatically applying the old overlay |
| `src/amat/regime/switching.py` | Causal breadth and smoothed stance weights; use as a simple control |
| `src/amat/validation/regimes.py` | Causal regime reporting and volatility controls; preserve reporting labels independently of fitted candidate states |
| `src/amat/features/structure.py` | RSI, variance ratio, Donchian position, downside variance share; reuse formulas |
| `src/autoresearch/` | Typed recipes, chronological fitting, patterns, controls, lineage, frozen runtimes and budgets |
| `src/newsdesk/store.py`, `discovery.py` | Point-in-time article versions and explicit theme mappings; reuse before adding text models |
| `src/amat/execution/intent.py`, `executor/src/executor/plan_source.py` | Plan boundary and separate execution package; statistical models stay in AMAT |

At the planning baseline, the research schema was `strategy-development/3`. It permitted 5/20/60-session
forecast horizons, feature lookbacks up to 126 sessions, and minimum holds up to
21 sessions. `Program.ordered()` explicitly rejects learned regime/risk overlays
without a separate nested-training adapter. Removing that rejection is not an
implementation of such an adapter.

Earlier repository research documents rejected regime variants, including a
learned selector beaten by its static control. Those findings motivate the
comparisons below; they are not new results from this planning exercise.

## Strategy specification

Keep direction, volatility, and uncertainty as separate measurements. A low-volatility
state is not automatically a range; a high-volatility state is not automatically
a downtrend. HMM state numbers carry no economic meaning until mapped using only
the fitting sample, and evidence of reversal must come from separate measurements.

| Component | Initial rule to register | Failure condition |
|---|---|---|
| Trend sleeve | Positive multi-horizon momentum plus Donchian breakout/participation; bounded long score | Gains vanish against existing momentum/breakout controls |
| Range sleeve | Positive long-term trend, low directional efficiency, and RSI/normalized displacement indicating a pullback; exit on recovery or timeout | Losses concentrate in persistent declines, or costs consume the effect |
| Router | Smooth weights from trailing trend efficiency, variance ratio, breadth, and volatility state | Cannot beat the same sleeves with fixed weights |
| Transition gate | Existing CUSUM first; optionally BOCPD recent-change evidence later | Cash exposure explains the apparent benefit or detection arrives too late |
| News event adjunct | Explicit event callout, deduplicated evidence, mapped asset, expiry, and price confirmation | Timestamp-correct replay eliminates the effect or event coverage is insufficient |

Seed thresholds/windows must be specified in the experiment manifest before
measurement. Start with existing 20/60/126-session features and 5/20/60-session
outcomes; do not multiply every combination into a large unconstrained search.
Keep trend and range sleeve definitions fixed during the initial router comparison.

For asset i at close t, define trend and range scores T and R in [0,1], routing
weights wT,wR >= 0 with wT+wR <= 1, and optional attenuation g in [0,1]:

    score[i,t] = g[i,t] * (wT[i,t] * T[i,t] + wR[i,t] * R[i,t])

Unused routing weight means abstention. This is a preference score, not a return
probability or target portfolio weight. Apply entry/exit hysteresis, then pass the
score through the existing portfolio constructor once. Do not allocate each
sleeve independently and accidentally double its risk budget. Daily risk and
missing-data exits override discretionary hold/cadence rules.

### Model ladder

1. Fixed rules and static blend controls using existing features.
2. Two- or three-state HMM experiment with causal filtering, training-only
   normalization, state mapping, fixed refit schedule, and occupancy/convergence
   checks. Select state count only inside training. Compare with the rule router.
3. BOCPD as an independent transition feature, compared with CUSUM and a simple
   volatility-shock feature. Do not automatically combine all detectors.
4. GARCH/GJR-GARCH volatility forecasts, first as a feature and diagnostic;
   compare with current volatility and EWMA before any sizing experiment.
5. Markov-switching GARCH only if ordinary volatility models leave a diagnosed
   failure and the data supports its extra parameters. A standard GARCH recursion
   alone is not an implementation of Markov-switching GARCH.

No stack migration to Kafka, Kubernetes, LangGraph, or CrewAI is needed for these
daily experiments. Reuse the existing proposal and evidence infrastructure.

## Research contracts and agent control

Proposed new contracts, introduced in a new research schema version:

- `RegimeSpec`: detector family, observed features, bounded state count, fit
  history, seed, refit interval, missingness policy, and state-mapping policy.
- `SleeveSpec`: fixed-universe signal recipe, direction, forecast horizon,
  entry/exit policy, and maximum discretionary holding period.
- `RouterSpec`: sleeve references, rule or fitted weights, smoothing,
  uncertainty attenuation, and declared fallback.
- `RegimeTrace`: probabilities or explicitly named rule scores, entropy,
  transitions, availability, fit ID, effective timestamp, and fallback reason.

Agents may propose feature subsets, thresholds, transformations, detector family,
pattern sequences, sleeve combinations, and ablations within a frozen search space.
Every proposal must name its parent, mechanism, falsification, expected affected
regimes/horizons, and simplest competing explanation. Every trial consumes the
shared budget; changing a research role must not reset it.

Use specialist research passes for market structure, event context, and model
critique. A coordinator chooses a bounded recipe from their evidence. A verifier
checks schema, timestamps, artifact hashes, coverage, and control results. An
optional model critique can challenge the mechanism, but deterministic checks
decide whether the experiment is valid. No per-bar LLM consensus is required.

The frozen program controls universe, costs, sample boundaries, risk limits,
promotion criteria, and execution authority. Agents cannot change these through
a detector or routing proposal. No order execution belongs in research workers.

## Data, fitting, and long-horizon dependencies

Persist `(session, symbol, decision_at, available_at, fit_id, source_digest)` for
each feature trace. Separate missing measurements from zero and low confidence.
Fit preprocessing and model parameters only on observations before each forward
block. Never refit an HMM on the entire chart and call its earlier outputs causal.
Use filtered probabilities at the decision close; prohibit future-conditioned
smoothed probabilities in the trading path. Library defaults require explicit
review: [statsmodels' prediction API defaults to smoothed weights](https://www.statsmodels.org/stable/generated/statsmodels.tsa.regime_switching.markov_regression.MarkovRegression.predict.html).

Research uses adjusted prices, valuation uses raw closes, and simulated execution
uses raw window VWAPs. Preserve this existing split. New OHLC features must verify
their adjustment basis; the current MarketPanel has highs/lows but no open field.
Market features use completed sessions, with orders no earlier than t+1.

Before choosing a broader snapshot, audit symbol coverage, session gaps, actions,
execution-window availability, provenance, and listing history. Keep the same
universe for the first paired comparisons. Treat wider current-symbol snapshots
as a separate universe experiment with explicit survivorship limitations.

Long-horizon research is a separate program profile, not a lookback-limit edit:

- Add 126/252-session labels, 252/504-session features, longer fitting history,
  weekly/monthly discretionary review, and longer holding policies together.
- Recompute a common evaluation anchor for all candidates and controls, including
  the longest feature history and nested horizon purges. Raise training minimums
  or reject windows that cannot provide enough eligible observations.
- Use matured executable labels and dependence-aware resampling appropriate to
  the longest outcome/holding horizon. Report effective independent evidence.
- Compare slow momentum/price-above-average rules with fitted regimes. Do not
  force advanced methods into the long-horizon baseline.
- Version datasets and evaluation boundaries; do not silently relabel previously
  inspected periods as untouched validation. Acquire additional older data only
  where adjustment, coverage, and universe history can be verified.

For news, preserve first receipt and version availability. Articles downloaded
today do not become historically knowable on their publication dates. Export
current callouts even when historical coverage is insufficient for backtesting;
mark that strategy component as forward-shadow only.

## Evaluation and promotion

Pre-register the experiment family, search budget, splits, controls, and decision
rule before running candidates. Keep the existing frozen promotion gates as the
minimum. Any strategy-specific changes require a new declared program, never a
post-result relaxation to make a candidate pass.

Required comparisons: plain v6; each sleeve alone; equal/static learned blend;
rule router; learned router; delayed router; static mean gate; block-shuffled
router; exposure-matched control; detector/sleeve ablations; nearby parameters.
Static learned weights use training observations only. Shuffle controls must
preserve plausible temporal dependence and apply across the shared date axis.

Report net score, Sharpe, drawdown, turnover, exposure, fills, missed fills,
2 bps normal and 6 bps stress outcomes, paired block-bootstrap intervals,
per-year/asset/regime slices, state occupancy/duration, switching frequency,
fallback frequency, runtime, and parameter stability. Correlated ETF rows are
not independent observations. A better state likelihood is not proof of alpha.

Use synthetic state changes to check detection delay and false alarms; real
market states have no authoritative ground-truth labels. Evaluate probability
calibration only for a separately defined observable outcome with matured labels.
Track all tried recipes, including rejected ones, to expose adaptive selection.

Required engineering checks: prefix invariance under appended future data,
training-cutoff enforcement, state-label permutation/refit stability, missing and
stale inputs, degenerate fits, restart/replay parity, shared fit caches with no
cross-candidate contamination, and identical research/plan score generation.
An end-to-end frozen-workspace test must reproduce router artifacts and control
results without network access or model-provider credentials in the worker.

## Delivery sequence

| Stage | Changes and artifacts | Completion gate |
|---|---|---|
| 1. Rule strategy | Proposed `src/autoresearch/routing.py`; typed sleeve/router specs; reuse pure structure measurements; seed recipes | Deterministic router, static controls, attribution, and current research gates work end to end |
| 2. Reusable features | Registry entries and tests from the companion backlog; initial volatility-shock and breadth features | Causality, units, availability, and incremental diagnostics verified |
| 3. Fitted regime adapter | Proposed `src/amat/regime/probabilistic.py`; integrate fit lifecycle in `src/autoresearch/signals.py`; persist traces/models | Training-only fit/filter behavior, degeneracy handling, and reproducible artifacts pass |
| 4. Research integration | Update specs/contracts, workspace freezing, proposer context, search, variants, diagnostics, worker exports | Agents can propose/ablate routers and resume frozen sessions; old frozen programs retain their runtime |
| 5. Long horizon and news | Explicit history/evaluation profile; frozen news feature artifact and coverage report | Longer labels mature correctly; news replay never predates availability |
| 6. Plan adapter and shadow | Extend AMAT plan generation to load an approved router artifact; executor contract fixtures | Backtest-to-plan parity, stale artifact rejection, restart/reconciliation, and fallback drills pass |

Initial paper observation target: at least 60 trading sessions plus sufficient
completed signals for the registered short-horizon experiment; sparse evidence
extends observation. A 126/252-session strategy requires corresponding label
maturity and cannot graduate on a 60-session result. Shadow must reveal behavior
under actual data availability; paper fills do not establish live market impact.

The executor receives final targets and provenance. If multiple strategies share
one account, AMAT must aggregate sleeve budgets and targets before producing one
account plan; separate strategy daemons must not fight over the same positions.
Intraday stops, shorting, and execution reactions to order flow are separate
execution contracts, not implied features of this daily strategy.

The first implementation milestone is stages 1–2. Continue to fitted models only
after their simpler controls and data contracts are in place. A failed strategy
experiment should still leave reusable, tested measurements and recorded evidence.
