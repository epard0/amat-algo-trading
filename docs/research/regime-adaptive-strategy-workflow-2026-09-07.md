# Regime-adaptive strategy and agent workflow

**Status:** research and engineering plan, not a promotion or authorization to
trade a new strategy  
**Scope:** `amat` research/plan generation and `amat-executor` paper execution  
**As of:** 2026-09-07

## Decision in one page

AMAT should become a **wide-universe scanner but a selective allocator**, not a
wide-universe portfolio that must always trade. The existing evidence does not
support replacing v6 with a more complex model yet:

- The current research snapshot has 2,665 sessions and roughly 860 reconstructed
  round trips, but only a handful of genuinely forward observations.
- Ten registered mechanisms produced zero promotions.
- Expanding the frozen strategy from 12 to 81 symbols lowered validation Sharpe
  from 1.194 to 0.788 while increasing fills from 3,643 to 13,066.
- Hard selection did not rescue the wider universe, and selection inside the
  frozen universe was inside noise.
- The v6 regime overlay was correctly left out of capital control after it lost
  to its static control (1.04 versus 1.14 Sharpe on already-used history).

Those results are documented in [the signal research
roadmap](signal-research-roadmap.md) and [the v6 design/audit](v6-design-and-latest-audit.md).
They say that breadth is useful for finding opportunities, not that every eligible
listing should receive capital. They also say that increasing trade count is not
an objective by itself. The objective should be:

> Maximize the number of independent, executable opportunities whose conservative
> net expectancy is positive, subject to risk, capacity, turnover, and evidence
> gates.

The recommended next model is deliberately modest:

1. Build a point-in-time, survivorship-aware universe.
2. Exclude untradeable, illiquid, stale, or operationally unsupported assets.
3. Classify each asset's structure at each supported horizon: trend, range,
   relative-value convergence, event-driven, high-volatility shock, or no edge.
4. Let specialized sleeves make calibrated proposals only where their required
   structure is present.
5. Combine proposals using soft scores, factor/cluster limits, turnover penalties,
   and an explicit cash/abstain option.
6. Keep risk, plan validation, submission, and reconciliation deterministic.
7. Promote only through causal walk-forward evidence and a long paper shadow.

This separates three problems that should not be forced into one model: **daily
cross-asset allocation**, **intraday execution/alpha**, and **multi-day event
trades**. Each has different data, labels, costs, and executor requirements.

## What works now, and what does not

The executor can currently consume an AMAT v6 plan, compare it with a paper
account, submit idempotent market-on-open VWAP orders, poll orders, reconcile
positions, and write a report. On 2026-09-07 the running container was repaired
by recreating it with the workspace's actual `../amat:/opt/amat:ro` bind. Its
configuration, Alpaca paper account, calendar, snapshot, and plan-generation
checks then passed. The broker's retained AMAT order history contained 13 orders;
all 13 were filled, including the most recent IEF sell reflected in the retained
report.

That validates the current paper path, not future capabilities. Today:

- the plan/executor contract is long-or-cash;
- the daemon has one daily decision/submission/report cycle;
- market-on-open VWAP execution has no true arrival-price or venue VWAP benchmark;
- the paper venue does not simulate real adverse selection or market impact;
- the live equity curve is short and contains an operational observation gap;
- news history is far too short for an event model promotion;
- an AMAT-only read-only mount cannot see the parent workspace `.git`, so container
  plan provenance cannot attest a clean code revision.

Long/short, intraday, and event holding periods therefore require explicit new
contracts. They are not flags to enable in v6.

## Target architecture

```text
point-in-time assets + prices + corporate actions + news + borrow/liquidity
                              |
                    data-quality quarantine
                              |
               wide universe and capacity screen
                              |
       +----------------------+-----------------------+
       |                      |                       |
  market structure      cross-asset structure     event structure
 trend/range/shock      factors/pairs/clusters    news/filings/catalysts
       |                      |                       |
 trend/range sleeves     stat-arb sleeve          event sleeve
       +----------------------+-----------------------+
                              |
          calibrated forecasts + uncertainty + horizon
                              |
       cost-aware portfolio/risk optimizer with abstention
                              |
              immutable, signed AMAT execution plan
                              |
           executor validation -> broker -> reconciliation
                              |
       fills + markouts + exposures + realized performance
                              |
             research ledger and promotion evidence
```

The scanner can cover hundreds of assets while the portfolio holds only the
small subset with the strongest *net* evidence. Asset coverage and position
count are different controls.

## Universe and adaptive asset selection

### 1. Point-in-time universe factory

For every session, persist the listings that were knowable then, including
listing/delisting dates, symbol changes, splits, dividends, exchange, asset type,
sector/industry, price, rolling dollar volume, spread proxy, borrow status, and
data availability. Never construct an old universe from today's listings.

Begin with liquid US equities and existing ETFs because the current broker,
calendar, corporate-action handling, and execution path understand them. Add
futures, options, crypto, or international sessions only as separate data and
execution adapters. “Wide range of assets” must not silently mix incompatible
clocks and risk models.

Hard eligibility gates should include:

- point-in-time listing and broker tradability;
- minimum price and median daily dollar volume;
- maximum spread/illiquidity estimate;
- sufficient clean history for the requested horizon;
- fresh bars and corporate-action reconciliation;
- for shorts: shortable/easy-to-borrow status, estimated borrow fee, locate
  policy, margin, and recall risk;
- capacity: proposed participation below a conservative fraction of ADV;
- no unresolved data-quality quarantine.

These gates determine what *may* trade. They must not use future returns.

### 2. Structure eligibility

Compute a causal feature snapshot using data with `available_at <= decision_at`.
Give each asset/horizon a probability distribution over:

- `trend_up`, `trend_down`;
- `range_mean_reverting`;
- `relative_value_convergence`;
- `event_positive`, `event_negative`;
- `shock_or_transition`;
- `no_edge`.

Require persistence/hysteresis before changing state, and enlarge the `no_edge`
region when classifiers disagree. A regime label is a routing feature, not proof
of profitability.

### 3. Soft selection, not another top-K contest

For candidate `i`, estimate a conservative score such as

```text
net_score_i = calibrated_expected_return_i
              - estimated_spread_i
              - impact_i(size, ADV, volatility)
              - borrow_i
              - turnover_penalty_i
              - uncertainty_penalty_i
```

Map scores to small initial risk budgets, then solve jointly with gross/net,
volatility, beta, factor, sector, cluster, single-name, and liquidity constraints.
Shrink forecasts aggressively toward zero. Cash is a valid optimizer output.
Prefer many small independent tilts to a brittle hard cutoff unless a registered
experiment demonstrates that concentration adds net value.

Track both:

- **opportunity frequency:** eligible independent signals per session; and
- **trade frequency:** submitted and filled orders after rebalance bands/costs.

An increase in the second without the first is churn, not improvement.

## Technical and structure features worth testing

AMAT already has ATR/Bollinger state, ADX, EMA slope, efficiency ratio, CUSUM,
confirmation, liquidity controls, multi-horizon trend, and covariance-aware risk.
New features should be registered one family at a time and tested against shuffled,
constant, and simpler controls. Do not turn them into an indicator vote.

| Feature family | Measurement | Why it can help | Principal failure mode |
|---|---|---|---|
| Variance ratio | `VR(q) = Var(r_q) / (q Var(r_1))` | Distinguishes persistence from short-horizon reversal more directly than a moving-average label | Overlapping observations and changing volatility distort significance |
| Multi-scale Hurst estimate | slope of `log(range or fluctuation)` versus `log(window)` | Adds a scale-sensitive persistence descriptor inspired by the supplied adaptive-agent article | Very noisy in short windows; never use fixed `H > 0.5` as a trading rule |
| Return ACF / Ljung-Box | lagged autocorrelation plus joint residual test | Measures whether directional/reversal structure is detectably different from noise | Multiple lags create data-mining risk |
| Aroon / Donchian position | time since high/low and position within causal channel | Measures breakout age and location; complementary to trend magnitude | Frequent false breaks in ranges |
| Realized semivariance | positive and negative squared returns separately | Separates harmful downside volatility from upside movement | Needs adequate intraday sampling for stable estimates |
| Bipower jump variation | realized variance minus continuous-variation estimate | Identifies discontinuous shock regimes where normal trend/range models should abstain | Microstructure noise at high frequency |
| Range volatility | Parkinson or Garman-Klass estimators | Uses OHLC information and can estimate quiet/compressed states efficiently | Gap and market-hours assumptions can be wrong |
| Volatility of volatility | rolling dispersion/change of realized vol | Detects unstable transition periods and informs sizing | Reactive and collinear with volatility spikes |
| Gap and close-location value | open/previous-close gap; close within daily range | Separates overnight information from intraday auction behavior | Corporate actions and stale closes must be cleaned |
| Volume surprise | causal volume z-score versus same weekday/time bucket | Confirms participation behind a move and flags events | Volume regime shifts and closing-auction concentration |
| Amihud illiquidity | `mean(|r| / dollar_volume)` | Converts liquidity into an asset-specific cost/capacity feature | Daily proxy is crude and unstable for very quiet assets |
| Relative strength | rank residual return versus sector, market, and peers | Cross-sectional information can rank opportunities without assuming absolute drift | Sector composition and point-in-time membership leakage |
| Rolling factor residual | alpha/residual after market/sector/style betas | Prevents a “stock” signal from being an unrecognized factor bet; supplies stat-arb residuals | Betas move and factors may be incomplete |
| Correlation/cluster stability | rolling/shrunk correlation and cluster turnover | Finds redundant positions and structural breaks in diversification | Correlations rise in stress and estimates lag |
| Breadth/divergence | fraction above trend, advance/decline, index versus constituents | Helps tell broad trends from narrow index moves | Universe changes create survivorship bias |
| Distribution shape | rolling skew, robust kurtosis, tail exceedance rate | Identifies asymmetric/tail regimes missed by variance | Sample hungry and dominated by isolated bad ticks |
| RSI/Stochastic/CCI | bounded location/momentum oscillators | Potential timing feature only inside a prequalified range regime | “Overbought” can persist throughout strong trends |
| MACD/PPO | difference of exponential trends, preferably percentage-scaled | Alternate trend acceleration feature | Mostly repackages existing moving-average information |
| Options state | put/call skew, term structure, IV minus forecast RV | Forward-looking event/risk pricing when point-in-time options data exists | Expensive data, stale surfaces, and embedded risk premia |
| Order-flow imbalance | signed trades/depth/queue change by time bucket | Relevant for intraday entry and execution rather than daily direction | Requires full-depth, timestamp-clean data and a new simulator |

ADX and Hurst-like statistics can be useful as continuous features and abstention
inputs. Threshold folklore such as “ADX above 25 means trend” must be treated as
a hypothesis and fitted only inside training folds.

## Statistical-arbitrage measurements

The new `amat.stat_arb` research module implements a causal first-pass pair
diagnostic. Given aligned positive price histories, it computes:

1. log-price hedge regression

   ```text
   y_t = alpha + beta x_t + s_t
   ```

2. log-return correlation;
3. current spread z-score

   ```text
   z_t = (s_t - mean(s)) / std(s)
   ```

4. an augmented Dickey-Fuller-style regression

   ```text
   delta s_t = c + gamma s_(t-1)
               + sum(phi_k delta s_(t-k)) + epsilon_t
   ```

   and the t-statistic for `gamma`;
5. AR(1)-implied half-life

   ```text
   half_life = -log(2) / log(phi),  where 0 < phi < 1
   ```

6. mean crossings and crossing rate; and
7. explicit rejection reasons for correlation, stationarity proxy, half-life,
   crossings, and hedge-ratio direction.

The scanner ranks only from the trailing window supplied by the caller. Its
default ADF threshold is an approximate large-sample screening value, not a
MacKinnon p-value and not a promotion test. This follows the spirit of the
[ADF documentation](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.adfuller.html),
whose null is a unit root, while keeping AMAT's core dependency set small.

### Next stat-arb experiment

Do not scan every possible stock pair and trade the prettiest result. That
guarantees severe multiple-testing pressure. Instead:

1. Pre-register economically related candidate groups: same-industry peers,
   share classes, related ETFs, or assets with a clear common factor.
2. Residualize returns against market, sector, rates, commodity, and style factors.
3. Form/test on rolling training windows; freeze hedge ratios through the forward
   evaluation window.
4. Require sign/stability of beta, correlation, stationarity, crossing count, and
   a half-life shorter than the maximum holding horizon.
5. Add structural-break tests and suspend a pair when residual behavior changes.
6. Simulate both legs, asynchronous fills, spread, impact, borrow, margin, dividends,
   corporate actions, and forced buy-ins.
7. Optimize entry/exit bands in training only; compare to no-trade, random-pair,
   distance-pair, and factor-neutral controls.
8. Correct for the number of pairs/thresholds tried and require forward bootstrap
   confidence over the baseline.

The classic [Gatev, Goetzmann, and Rouwenhorst pairs study](https://www.nber.org/papers/w7032)
matched normalized price paths, used explicit formation/trading periods, and
noted that microstructure can explain part of measured profit. That is motivation
for controls and costs, not a claim that modern pair convergence remains free
alpha. Robot Wealth's recent writing makes the complementary practical point:
cointegration is a diagnostic, while an economic reason for convergence is the
trade thesis.

## ML model recommendation

### Prediction target

Do not predict raw next-day direction. Create horizon-specific labels based on
the return executable by the actual policy:

```text
label(i, t, h) = position_direction * forward_return(open_t+1 -> exit_t+h)
                 - spread - impact - borrow - turnover
```

For a daily router, predict a calibrated probability of clearing a conservative
cost-and-risk hurdle, plus expected magnitude and uncertainty. For relative value,
predict convergence before a timeout. For events, predict horizon-specific
abnormal return and adverse excursion after the article's `available_at`, not its
publication date alone.

### Model ladder

Use a ladder so complexity must demonstrate incremental value:

1. fixed rules and linear/logistic models;
2. regularized elastic-net baseline;
3. shallow histogram gradient boosting or a learning-to-rank tree model;
4. calibrated ensemble of genuinely different sleeves;
5. only after substantially more forward data, a contextual bandit for sleeve
   allocation;
6. deep sequence models or reinforcement learning only if simpler models fail for
   a diagnosed representational reason and the sample supports them.

Tree models are appropriate candidates because they handle nonlinear interactions
and missingness without imposing a huge sequence model. Their output still needs
time-ordered calibration. Use expanding or rolling-origin splits with a gap/embargo;
standard random K-fold is invalid for this use. The supplied Imperial project is
useful for its formal state/action/reward decomposition and long/short/idle action
space, but its reward is sparse realized profit and its simulator assumes actions
execute. AMAT should not inherit those simplifications.

If reinforcement learning is revisited, define a constrained reward such as

```text
r_t = net_pnl_t
      - lambda_turnover * traded_notional_t
      - lambda_risk * risk_violation_t
      - lambda_dd * drawdown_increment_t
```

and never let a learned policy bypass portfolio/risk or execution validators.

### Data contract and leakage controls

Every feature row needs:

- `symbol`, `decision_at`, `feature_as_of`, `available_at`;
- raw snapshot/partition digest and corporate-action version;
- universe membership known at `decision_at`;
- feature code/config revision;
- label start/end and cost-model version;
- missingness/quarantine flags.

News additionally needs source, URL/content hash, first-seen timestamp, revision
timestamp, entity mapping, novelty/duplicate cluster, and horizon. A language
model can extract event type, affected entity, direction, duration, confidence,
and supporting evidence, but a calibrated historical model—not the LLM's prose
confidence—must determine whether the signal receives risk.

## Multi-agent design

TradingAgents supplies a useful organizational pattern: specialist analysts,
opposing research views, a trader synthesis, multiple risk perspectives, and a
final manager. Its published experiment is short and expensive, however, so its
headline performance is not evidence that an LLM committee creates alpha. AMAT
should borrow the **structured workflow**, not delegate controls or order authority
to chat agents.

### Roles

| Role | Reads | Produces | May affect orders? |
|---|---|---|---|
| Data-quality agent | snapshot manifests, freshness, corporate actions, news timestamps | quarantine report and missing-data artifact | No; deterministic quarantine code enforces it |
| Universe/capacity agent | point-in-time listings, liquidity, broker asset metadata | eligible universe with reason codes | Proposal only |
| Market-structure agent | causal technical/volatility features | regime probabilities by asset/horizon | Proposal only |
| Technical analyst | trend/range/cross-sectional features | forecast, uncertainty, invalidation condition | Proposal only |
| Stat-arb analyst | factors, pair diagnostics, borrow/cost inputs | convergence candidates and both legs | Proposal only |
| News/fundamental analyst | timestamped articles, filings, fundamentals | event thesis, horizon, citations, novelty | Proposal only |
| Bull and bear reviewers | all analyst artifacts | strongest case and falsifier for each proposal | No |
| Model-risk validator | manifests, trials ledger, walk-forward results | leakage, stability, and promotion verdict | Can veto only |
| Portfolio/risk engine | validated numeric forecasts, covariance, constraints | deterministic signed target weights | Yes, through code |
| Plan builder | approved targets and execution contract | immutable AMAT plan with digest | Yes, through code |
| Executor | one valid plan, broker/account/calendar state | broker orders and durable journal | Sole submitter |
| Reconciliation agent | plan, submissions, broker orders/fills/positions | discrepancies, markouts, incidents | No; can halt future cycles via a deterministic alarm policy |

The word “agent” does not require an LLM. Data checks, risk, plan creation,
submission, and reconciliation should be deterministic services. LLMs are useful
for unstructured news, evidence synthesis, and adversarial review.

### Artifact contract

Agents communicate through validated immutable artifacts, not chat summaries.
Every proposal should contain:

```json
{
  "schema_version": "1",
  "run_id": "...",
  "decision_at": "...",
  "available_at_cutoff": "...",
  "symbol": "...",
  "horizon": "daily|intraday|event",
  "thesis": "trend|range|relative_value|event|no_edge",
  "direction": "long|short|flat",
  "expected_net_return": 0.0,
  "calibrated_probability": 0.0,
  "uncertainty": 0.0,
  "invalidation": ["..."],
  "source_digests": ["..."],
  "model_revision": "...",
  "warnings": ["..."]
}
```

Missing artifacts, schema errors, stale timestamps, conflicting digests, or an
unresolved veto result in abstention. Agents cannot modify production risk limits,
broker credentials, the executor database, or an already-signed plan.

### Full daily workflow

1. **Continuous ingestion:** append price/news/fundamental data; timestamp first
   availability; quarantine late, stale, revised, or inconsistent records.
2. **After close:** freeze a decision snapshot and its digest. Universe/capacity,
   structure, technical, stat-arb, and event analysts run independently.
3. **Challenge:** bull/bear reviewers try to falsify proposals and identify common
   factor exposure, stale news, crowded pairs, broken stationarity, or cost risk.
4. **Numeric synthesis:** a versioned model converts validated features/proposals
   into calibrated horizon-specific expected net returns and uncertainty.
5. **Portfolio/risk:** deterministic optimization applies gross/net, volatility,
   drawdown state, factor/sector/cluster, turnover, borrow, liquidity, and capacity
   limits. Unused risk remains cash.
6. **Plan:** AMAT writes one immutable plan with data/config/code/model/cost digests,
   `as_of`, submission window, signed weights, references, and all gate verdicts.
7. **Pre-open:** executor verifies plan freshness, account, calendar, broker assets,
   shortability where applicable, buying power, duplicate client IDs, and kill
   switches. Any ambiguity refuses submission.
8. **Execution:** executor alone submits orders, records every broker response,
   polls terminal status, and compares positions to targets.
9. **Close:** reconcile intended, submitted, accepted, filled, canceled, rejected,
   and unmatched orders. Store decision-close, arrival, interval VWAP, fill, spread,
   fees, and adverse markouts when the required data exists.
10. **Evaluation:** append equity/exposure observations and attribution by sleeve,
    regime, symbol, horizon, model version, and cost bucket. Operational gaps are
    not compressed into fake daily returns.
11. **Review cadence:** weekly incident/data review; monthly calibration/drift and
    capacity review; retraining only on a declared cadence or drift trigger. No
    automatic production promotion.

Intraday is a different workflow: streaming quotes/trades, market-clock buckets,
queue/latency-aware simulation, position limits enforced continuously, and a
separate emergency flatten policy. It should not be emulated by running the daily
daemon more frequently.

## Performance and execution metrics

The executor report now separates return statistics from execution quality.
The performance block retains compatibility fields and adds:

- observation count and actual interval length;
- total and annualized return;
- max/current drawdown and drawdown duration;
- ulcer index;
- annualized volatility and downside deviation;
- Sharpe, Sortino, and Calmar;
- historical 95% VaR/CVaR;
- positive/negative period counts and gain-to-pain ratio.

Samples below 20 sessions are prominently labeled as not meaningful.
Annualized, dispersion, and tail values are suppressed when an interval exceeds
four calendar days. This prevents the recent daemon outage from being treated as
one daily return.

The execution block reconciles plan intents to broker orders and records planned,
broker, filled, partial, open, canceled, rejected, unmatched, and overfilled counts; planned
reference notional; filled notional; reference-notional fill rate; reconciliation
status; and signed adverse decision-close-to-fill markout. That markout includes
overnight movement and is explicitly not opening-VWAP implementation shortfall.

Next additions, once quotes/bars are durably captured, should be:

- arrival-mid and interval-VWAP implementation shortfall in bps and dollars;
- effective and realized spread;
- fill latency, participation rate, cancel/replace count, rejection taxonomy;
- target-versus-achieved weight and residual exposure;
- slippage by symbol, order size/ADV, volatility, regime, and time bucket;
- sleeve/regime/factor P&L attribution;
- exposure-weighted return, turnover, capacity, and borrow cost;
- strategy-versus-frozen-baseline differential with block-bootstrap confidence.

Keep paper and backtest metrics visibly separate. Paper fills are an operational
test and an optimistic bound on real execution, not evidence of expected returns.

## Promotion gates

Each experiment changes one mechanism family and carries a manifest and trials
ledger. A candidate remains shadow-only unless all applicable gates pass:

| Gate | Minimum evidence |
|---|---|
| Runtime | clean container start, deterministic health check, no unresolved missed phase, idempotent replay |
| Data | point-in-time coverage, freshness/completeness report, zero unexplained leakage/quarantine bypass |
| Reconciliation | every intent matched or explained; no unknown broker orders; target/position difference bounded |
| Causality | feature availability audit and same-close alignment test pass |
| Validation | rolling/expanding walk-forward with gap/embargo; untouched final holdout used once |
| Baseline | improves the frozen strategy and simple control on registered primary metric |
| Uncertainty | stationary/block bootstrap interval for differential excludes zero or another predeclared equivalent |
| Multiple trials | deflated/probabilistic Sharpe or explicit family-wise/FDR correction records all trials |
| Costs | survives spread, impact, borrow, and at least 2/4/6/10 bps sensitivity as applicable |
| Stability | parameter neighborhood, subperiod, symbol leave-one-out, and regime results are not concentrated |
| Risk | drawdown, gross/net, factor/sector/cluster, tail, liquidity, and capacity limits pass |
| Paper shadow | at least 60 sessions for operations; preferably 252 for daily performance inference |

Sixty paper sessions can validate operation; it cannot establish stable expected
return. The required performance window should be derived from uncertainty and
turnover, not a desire to ship.

## Delivery sequence

### Phase 0 — executor and measurement hardening (this run)

- repair the running container mount and validate paper account/plan access;
- verify retained broker orders against submitted quantities/status;
- rewrite performance metrics with gap-aware observation semantics;
- add intent/order/fill reconciliation and durable execution summaries;
- make provenance detection fail closed rather than crash on a slow git status;
- rebuild, restart, and re-run container checks/tests.

### Phase 1 — durable execution evidence

- capture pre-submit quote, arrival mid, opening interval bars/VWAP, and final fill;
- persist broker order updates rather than relying on seven-day reports;
- alert on any unmatched/rejected/partial/stale order or target-position drift;
- mount or inject a trustworthy build revision into the image/plan manifest;
- accumulate uninterrupted paper observations.

### Phase 2 — universe and stat-arb research

- implement the point-in-time universe factory and liquidity/capacity report;
- run the new pair diagnostics only inside predeclared economic groups;
- build factor-residual, break, cost/borrow, and two-leg simulator support;
- compare wide scanning plus soft allocation against frozen 12, wide-all, hard
  top-K, and random-K controls.

### Phase 3 — daily adaptive router

- produce calibrated structure probabilities with an explicit no-edge state;
- add trend, range, and relative-value sleeve proposals;
- use small risk budgets and frozen optimizer constraints in shadow mode;
- require forward superiority before changing v6 capital control.

### Phase 4 — event/news sleeve

- continue point-in-time news accumulation and timestamp audits;
- define an event taxonomy, novelty/entity mapping, abnormal-return labels, and
  1/5/20-session horizons;
- use LLM agents for extraction and opposing evidence, then calibrate with numeric
  models; abstain until the event class has adequate forward support.

### Phase 5 — long/short paper contract

- introduce a versioned signed-position plan schema;
- add borrow/locate/fee/margin and short-sale validation;
- model dividends, recalls, forced covers, gross/net beta, and both-leg execution;
- run a separate paper account/sleeve until reconciliation and risk gates pass.

### Phase 6 — intraday research platform

- acquire timestamp-clean quotes/trades or depth data;
- build event-driven simulation with latency, queue/fill, spread, impact, halts,
  auctions, and session controls;
- deploy a separate streaming executor and continuous risk service only after
  simulator and replay parity.

### Phase 7 — optional learned allocation

- compare calibrated tree/linear routers to fixed deterministic routing;
- consider a contextual bandit only after enough forward context/action/reward
  data exists;
- defer deep RL/transformers unless a pre-registered experiment shows a simpler
  model cannot represent a materially valuable pattern.

## Source interpretation

- [Cumming's reinforcement-learning project](https://www.doc.ic.ac.uk/teaching/distinguished-projects/2015/j.cumming.pdf)
  motivates explicit state/action/reward design, but not an immediate RL promotion.
- [Robot Wealth](https://robotwealth.com/blog/) motivates causal alignment,
  rolling evaluation, simple baselines, economic mechanisms, and careful treatment
  of selection/turnover.
- [Awesome Quant ML Trading](https://github.com/grananqvist/Awesome-Quant-Machine-Learning-Trading)
  is a discovery catalog, not validation evidence.
- The supplied [regime-adaptive X article](https://x.com/RitOnchain/status/2094705814317752701)
  motivates regime/strategy/risk/execution/backtest/reconciliation roles. Here,
  “Grok bot” is replaced with provider-neutral AI agents and deterministic control
  services.
- The supplied [ML trading system image](https://x.com/RitOnchain/status/2096246534677627009/photo/1)
  motivates modular data/features/models/signals/risk/execution/evaluation. Its
  model list is not a recommendation to deploy every model.
- [TradingAgents code](https://github.com/TauricResearch/TradingAgents), its
  [project description](https://tradingagents-ai.github.io/), and the
  [paper](https://arxiv.org/abs/2412.20138) motivate specialist analysis,
  adversarial debate, structured shared state, and a final approval boundary.
  AMAT should validate those process benefits independently of the paper's short
  experimental window and headline returns.

The common lesson is architectural: trustworthy data, causal features, explicit
uncertainty, hard risk boundaries, realistic execution, and honest evaluation are
more valuable than choosing a fashionable model.
