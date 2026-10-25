# AMAT research foundation and October 23 operational review

Evidence cutoff: October 23, 2026, 12:30 p.m. America/Detroit for broker figures; historical simulations end October 12, 2026. No new strategy has been backtested or promoted in this run.

# AMAT functionality and directional-exposure audit — October 23, 2026

This is the opening functionality analysis for the new research run. It audits source and focused regression tests; it does not authorize short trading or change the paper executor. The current default is **v14.3.0, all groups with volatility-triggered cost-aware switching**, configuration SHA256 `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8`. Broker results, current position profit, and monthly attribution belong to the accompanying operational/performance review and are distinct from this capability audit.

## Principal finding

**The deployed AMAT strategy and its native backtest are long-or-cash. They do not open direct short positions.** This is a deliberate contract enforced in several layers, not a missing sign in P&L parsing. Selling in this engine means reducing or closing an existing long. A downtrend can lead to cash or a different positively trending ETF, but the current universe contains no inverse ETF and no signed short sleeve.

Separate research modules can simulate signed equity sleeves and crypto perpetuals. Their presence does not give the current AMAT profile short capability or establish native execution parity. Those experiments must be reviewed as their own research protocols, with matched controls, before their returns can be compared or adopted.

## 1. Current strategy, decisions and active controls

The [default profile](/home/supasuge/Documents/amat-workspace/amat/configs/strategy/amat_group_selection_volatility.toml:1) contains SPY, QQQ, IWM, EFA, EEM, IEF, GLD, SLV, DBC, USO and UUP. It uses 63/126/252-session trend horizons, a 0.2 deadband, daily selection, at least two positive trend horizons, four positions, no more than two per rotation group, a 20% annualized forecast-volatility target, 45% individual weight limit, and a 100% target gross limit. Separate universe cluster caps also apply. Capital sizing uses volatility power zero; volatility still enters trend normalization and portfolio risk targeting. It is inaccurate to describe this default as unconditional inverse-volatility allocation.

Rotation groups differ from risk-cap clusters: the default rotation groups are growth, real assets and defensive; `max_active_groups=3` admits all three. Their definitions and defaults are in [AdaptiveRotationConfig](/home/supasuge/Documents/amat-workspace/amat/src/amat/config/models.py:729). The enabled group ranking is relative to QQQ on completed weekly observations, while asset selection refreshes daily. QQQ is a reference series, not an obligation to hold QQQ or a guarantee of QQQ-like returns. See [rotation group ranking and selection](/home/supasuge/Documents/amat-workspace/amat/src/amat/portfolio/adaptive.py:115).

The cost-aware switch rule affects eligible incumbent replacements within a group. Its gate activates when either relevant asset has 20-session annualized volatility above 25%, or its 20-session volatility exceeds 1.5 times its 60-session volatility. It compares a fixed five-day projection of the trailing 20-session relative log return with a round-trip cost hurdle. This is a heuristic opportunity proxy, not a fitted return forecast. Invalid or newly excluded incumbents can leave immediately. See [gate](/home/supasuge/Documents/amat-workspace/amat/src/amat/portfolio/adaptive.py:27) and [replacement hurdle](/home/supasuge/Documents/amat-workspace/amat/src/amat/portfolio/adaptive.py:159).

The current default's structure/trend-quality, risk-budget and learned-regime filters are disabled. The adaptive `regime_overlay` and hysteresis settings default to false. Daily volatility targeting, positive-trend eligibility, caps and conditional switching are active; the presence of other options in the repository does not mean they are active in this profile.

**Configuration trap:** the `[risk]` block contains daily-loss, drawdown-halt, Kelly and regime settings, but these are **inert in the native v1-frozen runner used by this default**. Its own [RiskOverlayConfig documentation](/home/supasuge/Documents/amat-workspace/amat/src/amat/config/models.py:601) says so. The [planner description](/home/supasuge/Documents/amat-workspace/amat/src/amat/planning/__init__.py:9) explains why the earlier path-dependent overlay was rejected. Consequently `max_daily_loss=0.03` and `max_drawdown_halt=0.2` must not be presented as guaranteed active strategy loss limits. Broker submission limits are separate operational safeguards; they do not turn these inert research fields into portfolio stop rules.

## 2. Long/short support by layer

| Layer | Native AMAT behavior | Evidence |
|---|---|---|
| Strategy validation | `long_only=false` raises an error and requires a new strategy version. | [TrendConfig](/home/supasuge/Documents/amat-workspace/amat/src/amat/config/models.py:166) |
| Features | Negative trend scores are clamped to zero. | [long_scores](/home/supasuge/Documents/amat-workspace/amat/src/amat/features/__init__.py:279) |
| Portfolio construction | Weights clipped nonnegative; caps only reduce exposure; residual becomes cash. | [apply_constraints](/home/supasuge/Documents/amat-workspace/amat/src/amat/portfolio/__init__.py:156) |
| Plan contract | Negative target weights rejected as an unrepresentable short. | [execution intent validation](/home/supasuge/Documents/amat-workspace/amat/src/amat/execution/intent.py:288) |
| Position/account ledger | Negative position quantities and negative cash are invalid. | [Position](/home/supasuge/Documents/amat-workspace/amat/src/amat/domain/positions.py:26), [LedgerSnapshot](/home/supasuge/Documents/amat-workspace/amat/src/amat/domain/ledgers.py:40) |
| Session accounting | Sale beyond holdings raises; no synthetic short sale proceeds or margin overdraft. | [SessionAccountant](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/accounting.py:133) |
| Paper submission | Intent target must be in [0,1]; sells clamp to observed holdings. | [preflight](/home/supasuge/Documents/amat-workspace/executor/src/executor/submit.py:190), [quantity resolution](/home/supasuge/Documents/amat-workspace/executor/src/executor/submit.py:285) |
| Staged batch | Nonnegative contract numbers, unlevered confirmed-cash policy, one direction per symbol. | [BatchPolicy](/home/supasuge/Documents/amat-workspace/executor/src/executor/batch.py:46), [initialization](/home/supasuge/Documents/amat-workspace/executor/src/executor/batch.py:176) |

This is appropriate accounting for the strategy actually run. It is **not sufficient functionality for evaluating an adaptive direct-long/short strategy**. A negative signal cannot become a short merely by changing a TOML Boolean or bypassing validation.

Buying an inverse ETF would be a positive share quantity with bearish economic exposure. The existing long cash ledger could represent owning that security if it had genuine observed price/action data and an explicit universe/profile. It is not equivalent to shorting QQQ. A valid experiment must use the actual inverse ETF's daily-reset history, expenses/tracking and distributions, inception availability, liquidity and sizing; negating or multiplying QQQ returns does not supply that history. The current profile includes no such instrument.

## 3. Native backtest and simulated-live accounting

The [native runner](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:1019) creates a flat cash ledger, applies a completed-session decision with at least one session of lag, settles pending trades, then makes the next decision. Signals use adjusted closes; fills use raw observed 09:30–09:35 VWAP, and portfolio marks use raw closes. Missing/zero-volume execution windows produce missed fills rather than fabricated prices. In-flight instructions are netted to avoid selling the same holding twice under delayed execution. See [execution model](/home/supasuge/Documents/amat-workspace/amat/src/amat/execution/__init__.py:1).

Transaction costs charge both purchases and sales as `abs(notional)*bps/10000`, recorded under `fees`. This is a combined commission/spread/slippage assumption, not an empirical broker fee statement. The next-session gap between the decision price and actual window fill affects market P&L. A zero `slippage` attribution field does not mean the simulated price equals the prior close. The default profile uses 2bps per side; campaign comparisons need explicit 2/6/10bps runs. For volatility-conditional switching, the runner binds the switch hurdle to the supplied cost scenario, so rerunning at higher fees can change trades as well as subtract larger costs. See [cost accounting](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/accounting.py:185) and [cost binding](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:1144).

The accounting identity is equity = cash + marked holdings, with changes attributed to market P&L + distributions + cash yield − fees − slippage. Dividends credit cash against opening holdings, and splits restate quantities and marks. Adjusted signals plus raw valuation and explicit actions avoid double-counting distributions. **Important limitation:** a missing vendor corporate action can reconcile perfectly while producing wrong economic returns. The explicit warning in [accounting.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/accounting.py:13) correctly states that reconciliation is not proof of corporate-action completeness. Dividends are credited on the modeled ex-date rather than a separate broker payable/payment schedule, another timing approximation to consider in tight cash budgets.

No direct short borrow fee, locate/inventory, recall, forced buy-in, short rebate, margin loan, maintenance margin or short dividend liability is modeled in this native long-only ledger, because no native short is allowed. The absence of these expenses is not biasing an actual native short position; there is none. It becomes a critical omission if this ledger is mislabeled as long/short. Taxes, queue position, size-dependent impact and exchange-specific financing are also outside its fixed-bps execution contract.

`amat simulate` uses [SimulatedLiveAccount](/home/supasuge/Documents/amat-workspace/amat/src/amat/simulation/__init__.py:1). It replays history from inception each step, rather than reconciling actual broker holdings. It is a hypothetical account, distinct from Alpaca paper execution and the remote session monitor. A broker's current open-position profit should never be equated with this simulation's total return.

Two live-simulation gaps deserve engineering experiments before strategy claims:

1. [SimulatedLiveAccount.step](/home/supasuge/Documents/amat-workspace/amat/src/amat/simulation/__init__.py:218) calls `run_backtest` without the 1% observed opening-volume entry cap; the [historical default CLI study](/home/supasuge/Documents/amat-workspace/amat/src/amat/cli/main.py:1881) supplies that cap for adaptive rotation. The runner's default cap is `None`. Identical configuration files therefore do not guarantee identical execution assumptions across entry points.
2. Live-appended sessions load corporate actions only through the frozen snapshot end. The [report caveat](/home/supasuge/Documents/amat-workspace/amat/src/amat/simulation/__init__.py:254) discloses this, but its phrase “understates total return” is too narrow for a missed split, which can distort position value in either direction. The research workflow should require verified action coverage over the entire measurement interval, not merely this warning.

## 4. Native planning versus paper execution parity

[build_plan](/home/supasuge/Documents/amat-workspace/amat/src/amat/planning/__init__.py:46) calls the native decision function on completed history with actual account positions/equity, then emits versioned intents. Existing tests compare its intended quantities to the native scheduler for two synthetic paths, three cost scenarios, flat and inherited fractional holdings. That supports **decision and pre-fill instruction parity in those cases**, not universal equality of realized executions.

The [staged execution contract](/home/supasuge/Documents/amat-workspace/executor/src/executor/batch.py:176) preserves original authorization, cumulative fills, remaining intent, pending cash, expiry, account identity and policy identity. Sales must become terminal before available cash is budgeted for bounded limit purchases. It retains residuals rather than creating replacement authority. These are useful controls against duplication and spending assumed sale proceeds.

However, exact historical-to-paper fill parity remains unproven:

- Historical native fills use a whole-window VWAP proxy, with a 1% observed entry-volume cap when supplied. The staged broker buys use bounded day limits, real order statuses and cash observations; no matching 1% historical opening-volume control is visible in the staged batch. Realized partial/missed fills and time ordering can differ.
- `whole_share_buys=True` in the [historical simulator](/home/supasuge/Documents/amat-workspace/amat/src/amat/execution/__init__.py:282) rounds buys **and partial rebalance sells** down to integer quantities, while preserving fractional full position-transition exits. The [staged broker code](/home/supasuge/Documents/amat-workspace/executor/src/executor/batch.py:449) forces buys whole but resolves sells using broker fractionability; a fractionable partial rebalance sell can remain fractional. Existing decision parity tests occur before these fill-time transformations. A matched fixture covering partial sells is needed before claiming complete whole-share sizing parity.
- The historical ledger credits ex-date distributions immediately; broker cash entitlement/payment events can arrive on a different schedule. Actual cash rounding and source activity coverage also matter.

A no-trade hold session confirms no-order compliance and identity, but cannot verify purchase/sale fill semantics. Do not infer these proofs from October 23's expected zero intents.

## 5. Existing signed research: reuse with explicit limits

[index_sleeves_study.py](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/improvements/index_sleeves_study.py:108) is an isolated equity long/short research engine. It supports signed raw quantities, previous-close whole-share targets, next-session observed VWAP, 1% volume budgets shared across opening/closing legs, sell/cover-before-reversal sequencing, 98% gross collateral on risk increases, signed dividends and splits. It charges calendar-day borrow on prior-close short notional at a fixed 3% annual rate, with 6% and unavailable-borrow scenarios. Short proceeds remain collateral. There is no cash yield, financing loan or borrow rebate. Its protocol explicitly says historical borrow inventory/rates are absent and it grants no live short authority ([protocol](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/improvements/index_sleeves_study.py:365)).

This engine uses floating-point arrays and scenario borrow availability, rather than the native Decimal account and actual stock-loan lifecycle. It records gross margin breaches but is not a broker maintenance-margin/recall simulator. It caps exits as well as entries, unlike the native strategy's uncapped risk-reducing exits. Thus its matched same-ledger no-sleeve control is essential: improved return can otherwise arise from different execution mechanics. [Opportunistic netted sleeves](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/improvements/opportunistic_sleeves_study.py:35) reserve core commitments, net positions before orders, and allocate residual capital. Their retention protocol preserves native and same-ledger controls and explicitly disclaims live parity. Previous results remain evidence, including rejected candidates; they must not be relabeled as a newly implemented native strategy.

Crypto perpetual research has separate signed P&L, discrete funding, margin, integer contracts and liquidation logic, with dedicated signed tests. Those capabilities do not transfer to ETF short selling: a perpetual contract has a different financing and collateral contract. The perps bot's active entry profile and latest signals need their own operational review. Similarly [stat_arb.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/stat_arb.py:1) is a diagnostic and explicitly disclaims trade costs, borrowing and forward validation; a pair z-score is not a deployable hedged portfolio.

## 6. Requirements before adding a directional strategy

The user has authorized research into **both direct shorts and inverse ETFs**, with separate validation before enabling either. Recommended sequence:

1. Freeze and reproduce the actual long-or-cash incumbent with complete signal/action history, equal initial capital, 2/6/10bps, consistent whole-share/volume/cash policy, and causal annual forward replays. Add QQQ total-return buy-and-hold on exactly the same investment interval, not QQQ price return or a differently funded account.
2. Repair or explicitly standardize entry-point and partial-sell parity first. Replay **all** controls under any repaired accounting/execution rules, retaining original evidence; do not compare repaired candidates against stale controls.
3. Decompose weak months into asset contribution, market direction, trend reversals/whipsaw, cash drag, delayed entry/recovery, turnover and execution costs. Define hypotheses from past-only observations. Targeting a known bad month alone is retrospective fitting.
4. Test one minimal adaptive long/cash change at a time before adding a short ledger. Evaluate drawdown, bad-month tails and recovery lag **together with QQQ-relative net return**, so a policy that is always in cash cannot win by drawdown alone.
5. For inverse ETFs, use observed instrument history and restrict first research to simple unlevered inverse exposure with explicit hedge budgets. No synthetic minus-index return substitution, pre-inception fills or hidden leveraged daily-reset assumptions.
6. For direct shorts, create a versioned signed domain/execution contract and collateral ledger: distinct sell-to-open/buy-to-cover intent, gross versus net limits, isolated/restricted proceeds, direction-aware costs, calendar borrow, signed distributions, inventory/locates, recall/forced cover, gaps, maintenance requirements and borrow-denied behavior. Unknown historical borrow requires explicit sensitivity and a limit on claims. No existing long guard should simply be removed.
7. Require long-only ablations, same-ledger no-overlay controls and tail-risk/short-squeeze stress; past-only fit/selection with purge/embargo for overlapping learned labels; independent challenge of economic rationale and acceptance criteria before reading outcomes. Learning changes need true rolling training/validation/test folds, not just annual report slices.
8. Only after the acceptance gate and native plan/backtest/accounting tests pass should a paper deployment be proposed. This audit made no trading or default changes.

## 7. Verification record

Focused tests exercised native execution/accounting, plan rejection of negative targets, cash/quantity behavior, current-default planner/scheduler comparisons, signed sleeve dividend/split/borrow/cap/causality rules, opportunistic netting and switch-cost binding. Executor batch and submission suite: **50 passed**. AMAT focused suite: **77 passed** from the AMAT project root.

An initial invocation from the workspace parent produced 74 passes and three file-not-found failures in tests that use project-relative `configs/strategy/...` paths. These were invocation working-directory failures, not a demonstrated strategy regression; the focused suite was rerun from the AMAT project root. No source files, broker settings or trading state were changed by the audit.

These are regression/fixture results. They establish neither profitable unseen returns nor exhaustive historical corporate-action completeness, historical borrow availability, or actual broker fill parity.


## Broker profit reconciliation and October 23 operations

At the October 23 **12:30 p.m. Detroit** observation, Alpaca returned these distinct quantities:

| Measure | Amount | Meaning |
|---|---:|---|
| Open-position unrealized profit | **$2,226.53** | Sum of broker `unrealized_pl` since each open position’s entry cost |
| Open-position intraday profit | $858.16 | Sum of broker `unrealized_intraday_pl` |
| Account equity change from broker previous close | $858.35 | Equity less `last_equity`, includes overnight changes; not flow-adjusted |
| Account equity change from 9:29 a.m. baseline | **$450.91 (+0.444%)** | The monitor’s regular-session comparison, including both cash and holdings |
| Current paper equity | $101,956.72 | Account valuation, not a profit figure |

The user’s TradingView observation of roughly +$2k is consistent with Alpaca’s **open-position unrealized profit**. The earlier monitor correctly calculated a different period’s equity change but did not display entry-to-current position profit. This was a presentation/coverage gap, not evidence that the broker’s positions lost the missing profit. The updated observer reads the actual `/v2/positions` fields as finite Decimal values and sums them without estimating missing values as zero. It also displays side, entry cost basis and market value. Session equity change is now explicitly labeled from 9:29 a.m.

| Position | Broker unrealized profit since entry |
|---|---:|
| DBC | $718.69 |
| SPY | -$261.64 |
| USO | $1,274.44 |
| UUP | $495.05 |

The exact sum is $2,226.526183; summing separately rounded displayed rows can differ by one cent. Broker account and positions are separate HTTP observations, not an atomic mark. Their roughly $0.19 marking difference at this snapshot should not be “fixed” by inventing realized profit or cash flows. Broker cost basis can change under its end-of-day accounting method. Unknown fees, transfers, and realized gains require their own ledger; equity minus original cash is not automatically a realized-profit calculation.

Primary definitions: [Alpaca position models](https://alpaca.markets/sdks/python/api_reference/trading/models.html), [Alpaca entry-price and cost-basis accounting](https://docs.alpaca.markets/us/v1.4.2/docs/position-average-entry-price-calculation). The broker read controls the numeric reconciliation; no TradingView account/session screenshot or UI read was supplied, so exact simultaneous display equality is not asserted.

### October 23 is not closed at this report cutoff

At 12:30 p.m., AMAT had zero planned/broker orders, missed intents, duplicate identities, partials or overfills. It retained the pinned hold plan using October 22 completed data and v14.3.0, enabled paper staged execution and whole-share purchases. Perps equity was $10,000 with zero session change, zero fills, $0 native simulated fees and $0 funding; the sampled latest decisions requested zero exposure. The separately saved morning event audit found 1,184 October 23 decision-cutoff polls, no orders or ignored carried-entry signals. That audit’s time cutoff must not be silently extended through the close.

**Final October 23 results are pending:** the market closes at 4 p.m. Detroit. The VPS minute collector continues independently of the desktop. This run added a tested deterministic post-close evidence review to that existing collector: at/after 4:25 p.m. it writes final findings from saved observations, preserves unknown fees, and calls the hold verified only with complete order coverage and matching pinned plan. It reports sampled perps target/fill observations distinctly from a complete event-ledger audit. A separate app follow-up is saved for 20:30 UTC (4:30 p.m. Detroit) to perform the fuller after-close review and pause afterward; that follow-up needs the desktop scheduler to be available. No future final result is claimed here.

The historical October 22 planning incident remains preserved. Two brief October 23 pre-market readiness alerts resolved at the next observation; current portfolio reconciliation is exact. A zero-order hold session cannot establish realized fill or short execution parity.

### Changes and verification in this run

Only read-only monitoring/reporting and its tests were changed. The position-profit GET addition was deployed to the VPS under the existing writer lock with updated source manifest and previous-source backup; neither trading bot was restarted or reconfigured. Missing position fields remain unavailable, non-finite values invalidate the additional position read, and short-position profit retains its broker-supplied sign. The deterministic final review cannot call a missing-order-coverage observation a verified hold. Current regression results: 28 monitor tests passed; the source audits additionally ran 77 AMAT and 50 executor tests. The frontend production build passed. Actual broker observations, source identities and reviewed monthly evidence are saved under the research-review output folder.


# Current strategy, weak months and QQQ — October 23, 2026 research review

This is a read-only review of saved, sealed simulations and the configured incumbent. No strategy experiment, fit, deployment or promotion was performed for this report. The operational default remains **v14.3.0, all groups + volatility-triggered cost-aware switching**. The latest study calls that incumbent `core_daily`; that label must not be confused with the older two-group daily-core strategy.

The present model has a materially lower historical drawdown than QQQ but has **not achieved QQQ-like absolute returns** in the snapshot reviewed. A fair, native, costed QQQ benchmark is still required. Broad protection or simply reducing trades has repeatedly lowered returns; the next experiments should isolate portfolio concentration, reaction to genuine adverse changes, and the cost of missing recoveries.

## Source and reproducibility contract

The main source is `amat/outputs/analysis/2026-10-20-cost-aware-annual-allocation/study/core_daily-{2,6,10}bps.json`, with the same directory's `protocol.json`. All four canonical SHA256 seals were verified against their sidecars. First/final equity reproduces each reported full-history return. This review writes extracted metrics, 15 worst months, asset price diagnostics, annual QQQ comparisons and source byte hashes to `amat/outputs/research-review-2026-10-23/month-diagnostics.json`.

These native results use flat $100,000, January 3, 2017–October 12, 2026, full 2016 signal warm-up, observed next-session 09:30–09:35 VWAP, dividends/splits, whole-share purchases, native 1% opening-volume entry cap, native exits, missed fills and zero cash yield. Corporate actions can create fractional entitlements on sale. Costs are fixed 2/6/10 bps per side, assumptions rather than reconstructed historical bid/ask spreads. History has already been researched. Chronological annual slices continue the same account; fixed-rule slices are not newly trained walk-forward folds.

The snapshot is `amat/data/snapshots/alpaca-sip-core-2026-10-13-verified/`. QQQ and cross-asset diagnostics use its `daily.parquet` adjusted closes; no external market narrative is needed for the measured price comparisons below.

## Current incumbent and QQQ objective

| Native incumbent, cost per side | Full net return | Calendar CAGR | 2020-onward net return | Max drawdown | Fees | Fills | Missed instructions |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2 bps | 296.39% | 15.14% | 211.89% | 12.59% | $8,962.67 | 1,961 | 2,692 |
| 6 bps | 259.80% | 14.00% | 192.72% | 12.84% | $24,604.44 | 1,929 | 2,868 |
| 10 bps | 221.88% | 12.71% | 177.60% | 12.98% | $37,259.04 | 1,907 | 3,010 |

At 6 bps average invested exposure was 86.26%, annualized volatility 12.30%, annualized traded-notional turnover 23.42 times equity and zero-risk-free Sharpe 1.13. The 74.51 percentage-point full-return difference between 2 and 10 bps is **not** just the difference in cumulative fee dollars divided by initial cash: changed fees alter cash, integer sizing and subsequent account paths. The assumed cost also changes fills/missed instructions. Missed counts are instruction events, not necessarily distinct desired trades, broker failures or evidence of wrong orders.

For the same December 30, 2016 prior-close to October 12, 2026 marks, **QQQ gross adjusted-close growth was 573.81%**, calendar CAGR 21.56%, daily-close max drawdown 35.00% and annualized volatility 22.74%. This is a useful objective gap, **not** a comparable native execution result: it omits first purchase at observed opening VWAP, whole-share cash residual, fees/caps/missed fills and explicit cash dividend versus reinvestment treatment. A claim that the incumbent matches QQQ is unsupported. Conversely, raw QQQ growth alone does not establish an equal-risk advantage; its realized volatility and drawdown were much larger.

The next workflow should publish both an unlevered investable QQQ benchmark and an optional separately labelled equal-risk diagnostic. An equal-risk comparison must not replace the user's absolute-return QQQ objective. Benchmark risk scaling, financing and cash yield must be preregistered; do not retrospectively lever AMAT to erase the gap.

| Year | Incumbent at 6 bps | QQQ gross adjusted-close diagnostic |
|---|---:|---:|
| 2017 | 9.63% | 32.62% |
| 2018 | 3.11% | -0.12% |
| 2019 | 8.74% | 38.96% |
| 2020 | 16.98% | 48.62% |
| 2021 | 28.22% | 27.42% |
| 2022 | 2.42% | -32.46% |
| 2023 | 5.74% | 54.85% |
| 2024 | 11.98% | 25.58% |
| 2025 | 33.89% | 20.77% |
| 2026 through October 12 | 20.20% | 21.86% |

The model protects materially in 2022 yet captures much less of several large equity up-years. Protection and upside participation must be evaluated together. Maximizing the number of nonnegative months alone would reward excessive cash and could worsen this objective.

## Worst months, exposure and activity

At 6 bps there are **43 negative months among 118 observed months**; October 2026 is partial. At 2/10 bps the counts are 41/41. Whole-share and path-dependent fills mean higher costs need not monotonically increase the number of negative months.

| Month | Net at 2 bps | Net at 6 bps | Net at 10 bps | Mean invested exposure, 6 bps | Fills, 6 bps | Fees, 6 bps | QQQ gross diagnostic |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2020-09 | -7.88% | -8.23% | -8.50% | 90.59% | 55 | $598.08 | -5.64% |
| 2020-03 | -7.40% | -7.53% | -7.65% | 84.21% | 27 | $278.38 | -7.29% |
| 2018-10 | -7.10% | -7.33% | -7.33% | 79.93% | 39 | $406.86 | -8.60% |
| 2023-02 | -5.18% | -5.28% | -5.37% | 83.88% | 24 | $285.34 | -0.36% |
| 2026-06 | -3.83% | -3.98% | -4.02% | 94.93% | 19 | $243.79 | -0.15% |
| 2019-05 | -3.89% | -3.88% | -3.81% | 99.14% | 0 | $0.00 | -8.23% |
| 2023-10 | -3.16% | -3.56% | -3.61% | 91.20% | 38 | $458.98 | -2.06% |
| 2026-03 | -3.33% | -3.31% | -3.27% | 79.24% | 27 | $489.86 | -4.84% |
| 2023-09 | -3.07% | -3.10% | -3.08% | 92.07% | 24 | $195.37 | -5.08% |
| 2021-11 | -2.91% | -2.92% | -2.94% | 99.15% | 6 | $45.21 | +2.00% |

These data disprove a single explanation that bad months are merely excessive trading: May 2019 lost 3.88% with no fills or fees. They also show that a QQQ-only bad-market detector is insufficient as a universal protection mechanism: February 2023 and June 2026 lost substantially while QQQ was nearly flat.

## What is observed, and what is still a hypothesis

The saved incumbent session rows contain aggregate equity, exposure, fees, fill/missed counts and turnover. They do **not** contain enough holdings/fill detail to reconcile current-incumbent monthly loss to each asset. The asset returns below are observed co-moves, not proof that the model owned those assets during their falls. Do not present them as causal portfolio attribution.

- **September 2020:** QQQ -5.64%, SPY -4.05%, SLV -17.50%, GLD -4.17%, USO -7.40%; incumbent exposure remained 90.59%. This suggests testing response to a synchronized reversal and correlated commodity/metal risk, after exact holdings attribution. The largest loss month also has 55 fills, so both risk and whipsaw/cost attribution deserve review.
- **March 2020:** SPY -12.62%, SLV -15.97%, DBC -17.32%, USO -55.45%, while IEF +3.73%. Broad shock and rapidly changing relative risks are visible in the actual snapshot; a model-specific failure mechanism requires per-session inventory and entry/exit attribution.
- **February 2023:** SLV -11.96%, GLD -5.37%, IEF -3.27% versus QQQ -0.36%; incumbent -5.28%. A technology-index-only trigger could miss non-equity weakness.
- **June 2026:** SLV -21.75%, GLD -11.68%, DBC -9.57%, USO -17.55% versus QQQ -0.15%; incumbent -3.98%, average exposure 94.93%. The analogous non-equity co-move merits investigation across all historical occurrences, not a rule keyed to this calendar month.
- **November 2021:** QQQ +2.00% while SLV -4.66%, DBC -8.77%, USO -16.17%; incumbent -2.92%, 99.15% exposure. This highlights possible opportunity-selection/rotation loss even in an equity up-month.
- **November 2022:** incumbent -2.51% with only 20.71% average exposure while QQQ +5.54%. Remaining asset losses and delayed re-entry must be separated; low exposure alone does not prove the cause. Recoveries are a crucial counter-test for every defensive proposal.

An older, separately reconciled 2023 attribution in `docs/research/2023-loss-and-native-recovery-2026-10-19.md` identifies silver as a major detractor for the **previous daily-core and all-groups variants** and shows that admitting all groups raised QQQ/SPY participation. That result motivates attribution work but must not be relabelled as this current whole-share incumbent's contribution table. Its older fractional execution contract and strategy differ.

## Recovery and stress constraints

At 6 bps the worst current-default episodes are:

| Peak | Trough | Recovered | Peak-to-recovery sessions | Drawdown |
|---|---|---|---:|---:|
| 2020-02-19 | 2020-03-31 | 2020-07-22 | 106 | 12.84% |
| 2026-01-29 | 2026-02-05 | 2026-05-14 | 72 | 12.60% |
| 2022-06-08 | 2023-03-15 | 2024-03-01 | 433 | 12.38% |

The 2022–2024 recovery duration is a distinct weakness even though the calendar-year 2022 result is positive. Stress returns are -7.07% in 2018 Q4, -9.54% in the COVID window, and +2.42% in 2022. A defensive improvement should be tested on drawdown depth, time under water, early recovery capture and missed upside, rather than a calendar-year result alone. A recovery policy should cap risk and use observed return/eligibility evidence to restore exposure; a monetary recovery quota or loss-chasing increase in risk has no evidentiary basis here.

## Previously tested ideas that must not be renamed and repeated

Source: `docs/research/performance-campaign-results-2026-10-19.md`, especially corrected whole-share cycles 013–015 and 021–025; `docs/research/quality-annual-allocation-results-2026-10-20.md`; `docs/research/cost-aware-annual-allocation-results-2026-10-20.md`.

- Own-asset 80th/90th/95th volatility-percentile sizing, fixed volatility/weak-trend sizing and group/QQQ variants already failed all-cost frontier tests. Do not sweep neighboring percentile thresholds under a different label.
- The persistent 30% combined-metals cap improved 2023 and drawdown but sacrificed full/forward return; it is not an untried solution to silver losses.
- Six positions, unanimity across trend horizons, shorter 21/63/126-day trends, a $250 trade minimum, market-wide QQQ protection and slower five-session selection were rejected. The $250 threshold produced identical histories. Fewer trades/fees did not automatically mean better outcomes.
- 40% maximum position size with quality improves the incumbent in several measures; combining it with other changes is not automatically additive. Existing 2%/3% quality-parent buffers and annual recipe transfers have already been measured.
- The one-year cost-aware annual screen rejects every saved update and duplicates its quality parent. The three-year screen worsens 2/6-bps returns and duplicates the parent at 10 bps. Repeating those trailing-window screens is not a novel research idea.

The later source audit is more current than the closed twelve-hour campaign report: source review of 44 registries, 354 daily records and 239 distinct decision inputs is complete, with 717 cost replays. **Shared-contract coverage remains incomplete** for missing assets in the 38-ETF study and signed/index-sleeve accounting. This distinction prevents stale claims that the entire source review is still pending, and also prevents unjustified global-best claims.

## Ranked next steps for the interactive research workflow

These are proposed mechanisms for independent relevance, duplicate and order review. None is preregistered or measured by this report. Exact economic hashes and source-registry search must run before admission; promising names alone do not establish novelty.

1. **Measurement foundation:** build a native, execution-matched QQQ buy-and-hold control and current-incumbent per-asset/per-fill attribution for all months. Reconcile cash, dividends, splits, actual missed fills, drawdown and fee effects to the sealed account to fixed tolerances. Add upside/downside capture and recovery diagnostics. This engineering step cannot earn strategy promotion on speed or reporting alone.
2. **Separate cash/participation from signal quality:** use attribution to identify whether a weak month comes from holdings that remain eligible, delayed entry after recovery, or capital blocked by failed fills. Choose the next experiment based on repeated mechanism counts across periods, not the most dramatic isolated month.
3. **Correlation-shock risk budgeting:** only if concentration evidence supports it, test one past-only downside co-movement detector that reduces the *joint* budget of currently held correlated groups when correlations rise abruptly. Keep original trend eligibility and exits; define an explicit, causal restoration rule. Compare with a matched static-risk ablation. This differs economically from fixed metals caps or independent volatility-percentile sizing, but needs full deduplication. Test missed upside in metals rallies and equity recovery windows.
4. **Separate de-risking from re-entry hysteresis:** if whipsaw attribution supports it, test a single fixed asymmetric restoration mechanism after an actual protective exit. Never recreate expired or already filled authorization. A parent feature-off control and a matched exposure ablation distinguish recovery participation from simply taking more risk. Include gap/reversal periods; do not optimize restoration against 2023 or June 2026.
5. **Opportunity-cost-aware allocation:** if repeated strong-equity underparticipation survives attribution, test one explicit and bounded change to the cash/eligible-equity competition instead of a fixed 20% QQQ sleeve. Portfolio-level netting, exposure, available cash and deterministic conflict resolution must be native and causally aligned. Compare with same-risk long-only controls. Existing rejected signed/opportunistic sleeves must first be reviewed so the new proposal is economically distinct.
6. **Learned exit-risk indicator only after simple controls:** predict a decision-relevant forward loss or adverse-selection event, conditional on current eligible holdings, using a small fixed past-only feature set. Require calibrated probabilities, nested rolling training/validation/test, matured labels, purge/embargo, locked thresholds, feature/cutoff mutation tests and a simple fixed-rule competitor. The decision threshold must include costs and missed recovery opportunity. Compare incremental value against volatility, trend and correlation features already in the engine. A high classification score alone is not a trading gain.
7. **Portfolio netting/execution efficiency:** only if ledger attribution finds meaningful avoidable transactions, reduce opposing or redundant target changes without altering original authorization. Fair engine-control replays are mandatory. Do not compare repaired accounting to stale results or imply paper fills match historical VWAP.

Each strategy experiment should lock one hypothesis and a small fixed variant set before outcome measurement, keep the unchanged incumbent and relevant parents, use all three costs, run chronological/stress/monthly/recovery analysis and native planning/accounting parity, then obtain independent review before interpreting promotion gates. Rejecting an active profile must preserve its strongest valid comparable historical return and source identity in the frontier. A comparison against QQQ should become a displayed target, with the user choosing whether it is an immediate hard promotion gate or a long-term objective alongside strict incremental gains.

The existing frontier requires full-history and 2020-onward dominance at all costs over every valid prior comparable candidate, not just the operational default. The latest 6-bps frontier already includes smaller-position annual quality at 287.81% full and 217.93% forward, while 2-bps and 10-bps cells can belong to other rejected arms. No new objective should be silently substituted for the user's prior acceptance policy. An interactive workflow should explicitly settle the practical tradeoff between absolute QQQ return, drawdown budget and statistically credible incremental progress before experiments start.

## Limits of this review

This report establishes descriptive weaknesses and measurement priorities. It does not prove a predictive adverse-regime classifier exists, promise positive performance in every month, verify short-account economics, supply new strategy return evidence, or authorize a new deployment. Current default behavior and the paper bots are unchanged. The full functionality/long-short audit and actual October 23 broker-session review are separate sections of the parent report for this run.


## Confirmed research policy

The user confirmed: **beat QQQ total return while limiting drawdown**, with drawdown **no worse than the current verified default and lower than QQQ**. Investigate direct shorts and inverse ETFs separately, requiring their own validation before enabling either. Prepare **ten incremental experiments with at most three fixed variants**, then stop for a full independent review, analysis and report. This workflow conservatively includes the unchanged control within those three arms, leaving at most two candidate variants.

Each experiment uses the latest verified default as its baseline. After an independently reviewed, exactly reproduced winner passes every required gate, create its versioned profile/receipt and make it the next experiment’s baseline. Retain the original v14.3.0 anchor, all intermediate controls and QQQ so successive changes cannot hide cumulative risk or stitch favorable history. Rejected or unresolved candidates never advance the pointer.

The user also requested intelligent conditional diversification and per-asset/market-specific strategies. The independently reviewed synthesis therefore replaces the weaker robust-volatility proposal with a small causal per-asset recipe-routing experiment. Different tickers or strategy labels do not establish diversification; assess covariance, economic exposures, joint stress losses and recovery. Use a tiny frozen library and shared parameters, not unrestricted per-asset fitting.

Incremental improvement and QQQ outperformance remain separate claims. The user explicitly allows incremental promotion even if an older rejected candidate is stronger in a return cell. The old all-frontier dominance veto is prospectively superseded; retain every older candidate as a visible benchmark and preserve all other cost/forward/drawdown/causality/accounting/parity gates. No new strategy experiment, model fit, promotion or remote trading change was made in this audit run. The complete ready-to-use fresh-chat prompt is saved in `docs/research/next-chat-adaptive-research-prompt-2026-10-23.md`.


# Independent research workflow review — October 23, 2026

This is an independent source and methodology review for the next research chat. It proposes work; it reports no new strategy measurements and grants no broker authority. AMAT is the research scope. Perps remains an operational observation and accounting scope unless the user expands it.

The user’s selected objective is to beat **QQQ total return while limiting drawdown**, through ten prioritized, small research experiments. Direct shorts and inverse ETFs may be investigated separately; neither may be enabled simply because the other works. The later confirmed policy is drawdown no worse than the latest verified default and lower than QQQ, with ten experiments and at most three fixed variants, then a full review. The reviewed-queue synthesis supersedes this initial proposal order and replaces robust volatility with conditional per-asset routing. Compute/provider limits and prospective evaluation duration must still be specified before dependent work. A failed hypothesis is a useful completed experiment; ten proposed experiments need not become ten executed experiments.

## What the repository already supplies

The current strategy is all groups plus volatility-triggered cost-aware switching, v14.3.0. Its historical source, economic configuration and current paper deployment must be verified again at the start of the next chat rather than inferred from a filename or a dashboard label.

| Capability | Evidence inspected | Implication for the new workflow |
|---|---|---|
| Immutable native studies | `src/autoresearch/improvements/switching_study.py` registers source/config/data identities and rejects an existing output directory | Extend the native study pattern; do not substitute a short generic search objective for full validation |
| Single campaign writer and bounded lifecycle | `scripts/run_performance_campaign_cycle.py` uses a file lock, state/PIDs, frozen sources and deadlines | Reuse the principles, but create a new protocol; the old twelve-hour campaign is closed |
| Exact incumbent replay and historical frontier | `campaign_cycle_study.py` requires parent session-hash reproduction at all three costs and includes rejected candidates | A candidate that beats only today’s default cannot be called the best historical result |
| Original condition and overlay identities | `campaign_audit.py` binds source-produced masks and diagnostic replays to their original hashes | Hashing the visible TOML alone is insufficient when masks, annual recipes or fit artifacts change decisions |
| Whole-share native annual strategy and exit contexts | `amat/backtest/runner.py`, `amat/planning/scheduled.py`, and `docs/research/native-annual-scheduling.md` | This capability now exists. The earlier finite-campaign report’s claim that annual contexts were absent is historical |
| Annual activation safety | `_prepare_schedule` validates ordered activation strictly after selection cutoff, identity and fixed accounting fields; arrays are copied | Declaring a cutoff is still not proof that externally supplied arrays/models are causal |
| Past-only fitted exit research | `validation_study.py` includes standardized logistic regression, annual mature-label training and ten-session label purge | “Add ML exit detection” is not a new hypothesis. A new model must explain the information or target that differs |
| Month/stress/recovery attribution | Native studies retain annual, monthly, stress and recovery records | Diagnose the *current whole-share default* first; older daily-two-group failures are not current-default evidence |
| Specialist review and synthesis | `docs/research/research-team-workflow.md` describes independent first-pass reviews, chief synthesis, evidence catalogs and budgets | Use real independent reviews with identical frozen evidence, disclose unavailable provider reviews, and preserve objections |
| Controlled recipe extension | `docs/research/research-controller.md` describes parent-relative component changes, budgets and no implicit promotion | Prose recommendations are not authorization to widen the experiment or retune after observing outcomes |

The native annual implementation is research-only. Its planner returns no execution authority. Research scheduling capability does not establish deployed broker execution parity. October 23’s zero-order hold session cannot validate actual fills.

## What changed after the finite campaign closed

Read the later October 20 annual studies before relying on the campaign’s final report:

* Native annual scheduling was implemented and checked against unchanged histories, whole-share replay and observed-prefix plans. The annual recipe candidates failed promotion despite some gains over the default.
* All 44 legacy registries were source-reviewed. The later review records 717 native whole-share economic replays from 239 distinct decision inputs, plus 15 fitted/control cost replays. Exact duplicates retain all identities.
* Remaining common-contract gaps are explicit: the broader 38-ETF study lacks 15 required histories in the fixed shared snapshot; two signed/index-sleeve studies retain incompatible execution/accounting assumptions. A source review is not the same as economic comparability.
* Smaller-position yearly quality updates raised the protected 6-bps full-history and forward cells. Therefore the finite-campaign report’s older frontier table is not the latest frontier. Read and verify `outputs/analysis/2026-10-20-cost-aware-annual-allocation/frontier.json` and its prior bindings.
* The one-year cost-aware annual screen rejected all nine updates and matched the parent. The three-year screen worsened some realized outcomes. Neither is evidence that more frequent retrospective recipe optimization should be added.

The old frontier, safeguards and execution contract must not be quietly weakened to make the new objective feasible. The user may choose a revised research objective prospectively; record it as a new protocol and show both old and new decisions. A lower-drawdown contender can be valuable on a Pareto frontier without being promoted under the old return-dominance rule.

## Methodology gaps to close before alpha research

1. **QQQ is not an explicit promotion benchmark in the existing campaign gate.** QQQ appears in signals and older comparisons, but the retained gate compares the strategy with historical candidates. Build a reproducible QQQ total-return account on identical dates and clearly separate investor buy-and-hold from the strategy’s capped executable benchmark. Entry-volume caps can prevent a benchmark from becoming fully invested: report this instead of presenting constrained entry as frictionless buy-and-hold. Reinvested dividends, idle cash, fees, final liquidation and benchmark timing must be declared. Show both with and without final liquidation if relevant, consistently for all accounts.
2. **Clarify the performance quantity.** Unrealized open-position P&L, current-day P&L, change since the 09:29 monitor baseline, strategy return since a chosen inception, and account return net of external cash flows answer different questions. Reconcile them; do not use the most favorable number as the headline return. Mark-to-market gains are part of total return even if no sale occurs.
3. **Research and paper execution use different fill models.** Observed opening VWAP plus a native participation limit is a historical model. Cash-confirmed staged bounded-limit paper execution can differ. Measure implementation shortfall and missed-fill opportunity rather than declaring price parity from equal intended quantities.
4. **Historical reuse is extensive.** Annual continuing-account slices are chronological diagnostics, not newly unseen folds. Nested annual fitting helps enforce causality but does not make inspected outer years untouched. Lock a future paper evaluation date before the new batch; retain every failed search and trial count.
5. **Diagnose loss mechanism before regime labels.** A negative month can result from directional exposure, group exclusion, correlated concentration, delayed exits, reentry whipsaw, cash drag, turnover, execution or distributions. “Volatility was high” alone does not identify which intervention should help.
6. **Economic deduplication needs more than config identity.** Include data, original gates, signal arrays, fitted model, annual schedule, accounting/cost contract and implementation hashes. Also deduplicate exact session histories while retaining aliases. A renamed rejected rule is not an independent experiment.
7. **Direct shorts require a separate accounting and operational contract.** Borrow inventory/cost, dividend liability, collateral/short proceeds, recalls, margin, short sale eligibility, side transitions and reduce-only cover behavior cannot be inferred from a negative weight. Inverse ETFs require their own raw prices, actions, inception, liquidity and daily reset/compounding; do not simulate them as the negative of QQQ returns.

## End-to-end scientific procedure

**Stage 0 — Repository and account reality.** Read applicable instructions, current dirty work, current default hashes, recent monitor evidence, data manifests and latest study receipts. Produce the functionality report first: actual supported signal direction, allocation, exits, signed accounting, historical execution, paper execution and missing capabilities. Record a capability as implemented, tested, research-only, operational-only or absent. Snapshot code/config/environment identities; no secret output or executor changes.

**Stage 1 — Reproduce and establish baselines.** Reproduce the incumbent at 2/6/10 bps over the established 2017-01-03 through 2026-10-12 contract with 2016 warm-up. Retain this anchor. If extending data through October 23 or earlier history, create a new versioned data contract and replay all meaningful comparators; keep the old date range visible. Build QQQ total-return and executable comparators, preserve every valid prior stronger candidate, and resolve the remaining incompatible signed/broad-universe studies before any overall-best claim.

**Stage 2 — Diagnose.** Build a complete monthly table and per-asset/group contributions for the current default, plus costs, turnover, exposure, cash, beta, participation misses, overnight gaps and exits/reentries. Reconcile attribution exactly to NAV. Rank losses by dollar/return contribution, not just number of red months. Examine all comparable regimes, including rebounds that risk filters can miss. Report the distribution of loss/recovery duration, worst rolling 5/20/60 sessions and downside versus QQQ. Use historical labels for explanation only; a trade-time regime feature must be knowable then.

**Stage 3 — Propose and independently review.** A proposer supplies ten ranked mechanism cards and the evidence each addresses. An independent reviewer scores novelty, causal plausibility, data availability, implementation gap, testability, likely opportunity cost and comparison bias without seeing proposer preference rationales initially. An execution/accounting reviewer vetoes unsupported assumptions. A synthesizer records both reviews, disagreements, prerequisites and final order. No agent reviews its own patch as the only reviewer. Parallel analysis is useful; freeze experimental decisions before a single serialized measurement writer runs.

**Stage 4 — Preregister one small batch.** Default batch size is one mechanism, with a no-change control and at most two prespecified variants. Seal mechanism, exact parent-relative delta, failure prediction, economic/config/source/data hashes, fixed thresholds, trial budget, train/selection/test boundaries, purge/embargo, cost assumptions, uncertainty procedure, acceptance criteria and stop rules. Full outcomes must remain hidden from proposal/selection code. Reject duplicate economics before measurement. A bug fix that changes accounting requires fair replays of all affected controls and frontier members.

**Stage 5 — Implement the minimum and verify.** Unit/invariant tests target economic failure modes: corporate actions, sign changes, cash, fees, borrow/collateral, gap risk, integer buys, missing liquidity, rejected/partial fills, expired intent and restart idempotence. Prefix replay and later-input mutation test causality. Match planner decisions with native replay on real observed prefixes. Preserve original authorization, cumulative fills, remaining intent, pending cash, expiry and account/policy identity. Do not compare a repaired candidate ledger with an unrepaired benchmark.

**Stage 6 — Measure.** Use continuous accounts, common prices/actions/calendars and all three costs. Report gross and net return, CAGR, maximum drawdown, volatility, downside, turnover, fees, missed fills, exposure, worst months and recovery. Show calendar-year and stress windows, including 2018 Q4, COVID and 2022, with other detected failures chosen by the diagnosis. A learned model needs nested chronological fitting/selection with mature labels and physical purge/embargo; record all trials. Model metrics such as AUC are supplementary to portfolio P&L and abstention opportunity cost.

**Stage 7 — Falsify and challenge.** Independent reviewers receive sealed complete results including underperformers. Require ablation, modest fixed parameter perturbations, extra costs/borrow unavailability where relevant, and comparison with simple cash/no-change/QQQ alternatives. Use paired shared blocks and a declared multiple-comparison family across all tried variants/costs; report uncertainty and limitations. If an experiment fails, archive it and update the failure map. Do not optimize a new threshold within the same registered test.

**Stage 8 — Decide, reproduce and prospectively verify.** Separate engineering retention, research advancement, promotion eligibility and deployment. A result can merit prospective paper observation without being a proven improvement. Promotion requires every prospectively agreed gate, latest frontier coverage, independent review, exact frozen-profile reproduction and end-to-end planner/paper parity. Default promotion and remote rollout are separate receipts. Preserve paper-only operation. Any direct-short or inverse-ETF activation needs its separately verified capability contract and user authorization.

**Stage 9 — Communicate and iterate.** Publish the same sealed results to the dashboard: strategy versus QQQ, drawdown, month/regime attribution, 2/6/10-bps scenarios, forward folds, execution-cost waterfall, uncertainty, keep/reject/pending reasons and data gaps. Show “no improvement found” when appropriate. The next hypothesis must cite the newly learned failure rather than accumulate overlays automatically. End each batch with updated experiment registry, receipts, unresolved issues and a fresh-chat handoff.

## Provisional ten-experiment queue

These are mechanism proposals, not fitted values or promised gains. Their final rank must follow current-default attribution, signed-ledger review and registry deduplication. Existing volatility-percentile tuning, unconditional inverse-volatility sizing, generic group shock filters, old QQQ sleeves and the existing logistic exit must not be rerun under new labels. An exact or economic duplicate is replaced by the next reviewed mechanism before registration.

| Rank | Small experiment and mechanism | Expected falsification / required comparison | Dependency and independent review concern |
|---|---|---|---|
| 1 | **Portfolio-level marginal-cost rebalance decision.** Apply a no-trade decision to the net portfolio change rather than a per-symbol rank-mean hurdle; required risk exits remain immediate | Reject if turnover savings are outweighed by exposure drift, costs at any scenario or missed recoveries; compare with the existing volatility-only switch and unchanged trade band | Verify this differs economically from prior cost/rank/buffer studies. Use a bounded common native policy, not a new optimizer/search |
| 2 | **Correlation-concentration risk budget.** Attenuate only the incremental allocation that breaches a past-only combined risk budget for highly correlated held assets | Reject if concentration-loss protection disappears after matched average-exposure control, or strong trends are unnecessarily capped | First attribute whether existing symbol/cluster caps actually left correlated concentration in bad months; reject if equivalent to tested metals/group caps |
| 3 | **Robust volatility estimate for position size.** Replace one volatility estimator with a fixed robust estimator, preserving target risk and all switching triggers | Reject if it adds lag after shocks, understates true risk or fails the cost/frontier gates; compare unchanged estimator on identical signals | This is an estimator hypothesis, not recycled volatility threshold tuning. Verify resulting allocations and data timestamps |
| 4 | **Gap-aware risk-increase limit.** After an observed overnight gap, limit new risk increases for one declared interval while retaining native exits | Reject if avoided entry losses are smaller than rebound opportunity cost; test direction/size attribution and gap versus continuous volatility ablation | Needs a defensible prior-observed gap feature and actual tradable next-session timing. Cannot condition today’s fill on an unseen opening window |
| 5 | **Asymmetric recovery state after a valid risk exit.** Restore exposure according to improvement in the asset’s original signal, without a profit target or leverage increase | Reject if delayed reentry costs more than avoided whipsaws or if gains are exposure-only; compare immediate native reentry and the existing staged-recovery rule | This must differ from prior pause/healthy-close quotas. No requirement to win back losses within a calendar deadline |
| 6 | **Native-netted QQQ opportunity allocation.** Test a single fixed policy for cash left by native risk/eligibility constraints using the actual common ledger | Reject if results fail current default/QQQ/frontier or depend on altered sell caps/collateral/accounting; include unchanged native account and long-only opportunity control | First repair the prior signed/netted sleeve comparison gap. Do not reproduce the rejected sleeve with a cosmetic label or assume free extra beta |
| 7 | **Small defensive direct-short sleeve.** One index, one fixed causal bearish condition and a bounded independent risk budget | Reject if borrow/unavailability/recall stress eliminates benefit or long-only/cash ablation is superior; score full-cycle return, short squeeze loss and rebound cost | Research-only until native signed accounting, borrow, margin/collateral, short dividends, fills and executor parity are verified. Historical borrow absence limits conclusions |
| 8 | **Matched inverse-ETF defensive sleeve.** Compare an actual unlevered inverse product with the same signal and risk budget as experiment 7 | Reject if daily reset, tracking, costs and turnover erase benefit; compare cash, direct short and inverse holding periods separately | Requires actual inception-correct raw/adjusted data/actions/opening liquidity. No synthetic negative QQQ history or unrestricted pre-inception backfill |
| 9 | **Strategy-conditioned exit opportunity model.** Predict the incremental net benefit of exiting versus holding the *current strategy’s* intended asset, including reentry opportunity cost | Reject if calibration or economic benefit fails nested tests, or a simple fixed rule performs as well; retain a no-ML and existing portfolio-adversity logistic control | The existing ten-day NAV-adversity logistic model is already implemented. A new target must be separately specified with mature counterfactual labels, past-only fit and sample-size limits |
| 10 | **Interaction test of two individually validated improvements.** Combine only the first two independent winners, with frozen unchanged parameters | Reject if the combined effect does not survive costs or destroys either mechanism’s protection; compare parent, A, B and A+B factorial arms | Blocked until two components independently pass. If none passes, use the remaining budget for independent reproduction/prospective observation, not forced complexity |

## Research decisions and remaining details

The human chose incumbent-relative and QQQ-relative drawdown guards, ten experiments, a full final review and sequential verified-baseline updates. The final queue is in `research-2026-10-23-reviewed-queue.md`. Establish the actual numerical bounds from reproduced controls; whether this is an absolute bound, incumbent-relative guard, QQQ-relative goal, or several explicit limits; and how much return can be sacrificed for protection. Specify QQQ benchmark dates, dividend reinvestment and investor versus executable comparison. Agree maximum experiments/variants, compute and provider budgets, and the paper validation horizon. Decide whether the new campaign preserves the old all-cost/all-frontier promotion gates or adopts a separately versioned QQQ-first policy. In either case, show the prior gate outcome transparently.

The immediate work should be capability/accounting analysis and benchmark reproduction, followed by loss attribution and independently reviewed preregistration. It should not start by enabling shorts, selecting the most flattering historical month, increasing leverage after losses or promising to eliminate red months.

## Primary local references

* `docs/research/native-annual-scheduling-results-2026-10-20.md`
* `docs/research/quality-annual-allocation-results-2026-10-20.md`
* `docs/research/cost-aware-annual-allocation-results-2026-10-20.md`
* `docs/research/index-sleeves-results-2026-10-20.md`
* `docs/research/opportunistic-sleeves-results-2026-10-19.md`
* `docs/research/2023-loss-and-native-recovery-2026-10-19.md` — older two-group attribution; useful causal history, not current-default monthly results
* `docs/research/research-controller.md`, `docs/research/research-team-workflow.md`
* `src/autoresearch/improvements/{campaign_audit,campaign_cycle_study,switching_study,validation_study,conditional_switching_study,efficiency_study}.py`
* `src/amat/backtest/runner.py`, `src/amat/planning/scheduled.py`

The independent reviewer inspected these sources and reports. No new backtest, hosted model review or prospective return evidence was generated for this methodology document.


# Reviewed research queue and sequential baseline policy

The human selected: beat QQQ total return while limiting drawdown to no worse than the current verified default and lower than QQQ; investigate direct shorts and inverse ETFs separately; ten incremental experiments, at most three fixed variants each, then a complete review/report. Use the latest verified default as each next experiment’s baseline, advancing it only after a verified winner.

Use a conservative three-arm limit: **one unchanged control plus at most two candidate variants**. Reproduction and archived-comparator checks are evidence obligations, not permission for additional searched variants. Preserve the original v14.3.0 anchor, the current control, QQQ and all historical return-frontier competitors. Do not stitch favorable periods from different versions into a return curve.

## Independent review and synthesis

Three independent analyses inspected actual sources: directional/execution feasibility, current-default monthly evidence, and scientific methodology. The proposer supplied an initial ten-mechanism queue; the other reviewers independently assessed it. Both moved correlation/recovery ahead of generic trade reduction. Both rejected a churn-only explanation because May 2019 lost 3.88% without trading, and rejected a QQQ-only adverse detector as sufficient because February 2023/June 2026 losses occurred with nearly flat QQQ. Neither inferred causal holdings attribution from asset co-movement alone.

The execution reviewer put native-netted QQQ opportunity allocation fourth for relevance to the return gap, and inverse ETFs before direct shorts for lower native implementation burden. The monthly reviewer preferred causal gap analysis before that opportunity sleeve, but agreed that the sleeve’s old accounting comparison must be repaired first. The synthesis uses readiness gates, not a rigid requirement to run an unavailable project next. The human’s new per-asset adaptation request replaces the lower-priority robust-volatility-estimator proposal; that change was independently reviewed for causality, overfitting and existing annual-recipe duplication. No outcome was used to select these new variants.

## Mandatory measurement foundation

Before experiment 1: reproduce current default and latest valid frontier, establish investor and executable QQQ controls, obtain exact current-default per-asset/fill/month attribution, resolve or standardize simulation entry-cap/action/partial-sell parity, and seal the risk/promotion policy. Engineering changes that alter economics require fair benchmark replays. Data gaps and incompatible old signed studies block dependent overall-best claims. Foundation work does not count as an alpha win.

## Conditional queue

| Priority | Hypothesis | Why test it / what would falsify it | Prerequisites |
|---|---|---|---|
| 1 | Conditional joint downside-correlation risk budget | Test synchronized portfolio losses; reject if benefit disappears after matched exposure or if recovery/upside losses outweigh protection | Holdings attribution must confirm joint concentration. Distinguish dynamic covariance from rejected static metals caps |
| 2 | Signal-based restoration after actual risk exits | Test slow recovery and whipsaw; reject if it delays rebounds or reproduces prior staged recovery | Define actual exit state and restoration without profit quotas, leverage escalation or recreated authorization |
| 3 | Conditional per-asset strategy routing and diversification | Test whether a small shared causal router among existing recipes improves asset/condition fit; reject if simple no-routing or allocation-only control explains gains | Two or at most three frozen verified recipes, shared features/parameters, no per-ticker search; past-only recipe selection; allocation/signal ablations within budget |
| 4 | Native-netted QQQ opportunity allocation | Test foregone equity upside from unused eligible capital; reject if added beta or changed accounting explains gains | Review rejected unused-cash sleeves; native common ledger; reserve core commitments; same-risk control and economic deduplication |
| 5 | Portfolio marginal-cost switching | Test avoidable net turnover; reject if saved costs are outweighed by drift and missed opportunity | Actual attribution supports churn; dimensionally valid economic benefit versus cost, mandatory exits preserved; prior switch/rank/band deduplication |
| 6 | Causally timed gap-aware risk-increase limit | Test demonstrable gap-related entry loss; reject if lost rebounds dominate | Use a prior observed gap at the next decision, or separately version a real execution-time policy. No future opening VWAP/volume in an earlier decision |
| 7 | Actual inverse-ETF defense | Test bounded bearish participation while retaining a long cash ledger; reject if reset/tracking/costs erase benefit | Real inception-correct raw/adjusted/actions/liquidity; compare cash and net economic exposure; no synthetic negative QQQ series |
| 8 | Native direct-short defense | Test whether a small short sleeve adds value beyond cash/inverse alternatives | Signed accounting, collateral, borrow inventory/cost/unavailability, dividends, recalls, margin, squeezes, transitions and native parity. Research scenario claims only where real inputs are absent |
| 9 | Strategy-conditioned ML exit benefit | Predict incremental net benefit of exit versus hold including re-entry cost; reject if existing logistic/simple rules are as good | Distinct target from existing model, enough episodes, nested past-only training/selection, mature/purged/embargoed labels, calibration and abstention |
| 10 | Separate OpenRouter free-model portfolio-agent arm | Test incremental net value of timestamped proposals for stops, targets, volume-aware sizing, cash opportunities and hedges; reject if costs, latency or simple rules explain gains | Pinned zero-price model and bounded calls; deterministic research-only action validation; prospective evidence; separate short/inverse/intraday gates; no production authority |

These are **proposals, not ten verified improvements**. A blocked/duplicate idea is not authorization for a larger sweep. Do not force a combination if fewer than two win; perform reproduction or stop for review. A direct-short or inverse-ETF capability project may be too large for the first small batch and should remain blocked rather than use unrealistic shortcuts.

## Scientific acceptance and baseline transition

At each 2/6/10bps cost, compare continuous full-history and 2020-onward accounts against the latest verified default and the stable original anchor. Require a strict substantive return gain in at least one required cell without losing another required cell, drawdown no worse than the same-cost baseline and below the declared QQQ comparator, and preserved original-anchor risk limits. Apply the risk guard to both full and forward histories unless an explicitly versioned policy states otherwise. These are historical acceptance limits, not promises of a live maximum loss; active operational risk enforcement must be separately verified. Use the newly authorized incremental policy: historical-frontier dominance is no longer a veto, but all historical results remain visible. Retain 2023, stress, accounting, causality and parity checks, with all-cost/forward improvement and drawdown guards against the current verified baseline. Report uncertainty and recovery/upside tradeoffs, not only an endpoint gain.

QQQ outperformance is the destination. An incremental improvement can be valuable before reaching it, but must be labeled accordingly. No new “best” from a partial fold, selected month, uncosted short, renamed old candidate or repaired-candidate/stale-control comparison. If robustness or a required gate is unresolved, keep the baseline unchanged.

For a verified winner: independent reproduction → sealed versioned profile and promotion receipt → coherent genuine default update → atomic next-control pointer. Every following experiment reproduces that control. Full backtests of different versions start from identical declared initial capital; never concatenate their best periods. Research promotion and remote broker deployment are separate, and no short/inverse/live activation follows automatically from a research receipt.

After ten experiments, stop and produce the requested full report/analysis, including failures and the chain from original to final baseline. Retain the report, updated failure map and verified baseline as the starting evidence for the next incremental research run.


## Hourly campaign addition

The user authorized Codex-controlled hourly research for twelve hours, with20–40minutes as needed per run and a hard40minute ceiling. Campaign:2026-10-23T16:40:10Z to2026-10-24T04:40:10Z. Experiment10 is now reserved for the separate free-model agent arm, replacing the optional interaction experiment within the ten-experiment budget. Interaction research requires remaining registered budget or a later authorized run. Preserve the original proposal/reviews as history and use this final synthesis. See the hourly protocol for locking, deadlines, provider budgets, current-model historical contamination and shadow-only trading authority.


# Hourly campaign and separate portfolio-agent research arm

The human authorized a new Codex-controlled twelve-hour research campaign, **October23 at12:40p.m. throughOctober24 at12:40a.m. Detroit** (2026-10-23T16:40:10Z to2026-10-24T04:40:10Z). The initial opportunity performs measurement foundation and engineering validation; eleven future opportunities are scheduled hourly. The last scheduled wake is October23 at11:40p.m. Detroit and must finalize within40minutes. Each wake may use20–40minutes as necessary, with a40minute maximum and no blocking hour-long sleep. Short useful validation steps are not padded with artificial waiting.

The saved heartbeat is `amat-hourly-adaptive-research-12-hours`. It runs through the Codex app and needs its host available. The independent VPS broker monitor continues when this computer is asleep; that does not imply the separate Codex research scheduler runs while asleep. Missed research opportunities must remain missed, never invented. The old twelve-hour campaign remains closed.

Maximum budget: **ten experiments; at most three total arms per experiment** (unchanged control plus at most two fixed candidates), then a full report and review. The human explicitly approved **incremental promotion over the current verified default**, even if an older rejected candidate is stronger in a return cell. Retain historical candidates visibly, but the old all-frontier dominance veto no longer applies prospectively. Cost, forward, drawdown, stress, 2023, causality, accounting, uncertainty, independent review and execution-parity requirements remain. Advance the next control only after a verified winner; preserve the original anchor and all receipts.

## OpenRouter arm

Experiment10 is reserved for the separate free-model portfolio agent, replacing optional winner-combination work within the existing budget. The agent can propose hold, stop loss, take profit, resize, open, close and hedge research actions. This includes investigating liquidity/volume-aware sizing, unused-cash opportunities, per-asset recipes and separately validated short/inverse/intraday behavior. A proposal validator pins the decision/execution/expiry clocks, evidence, account/policy identity, original intents/fills/remaining intent, pending cash, cost reserve, gross/net/symbol limits and instrument capability. It rejects overlapping authority, malformed/code fields, unsupported shorts, speculative sale funding and non-whole risk increases. Its result is **admissible for research replay only**, not broker authority.

This run built the **contract/validation prototype**, not an autonomous portfolio manager. Model-to-current-portfolio integration, real intraday action replay, empirical slippage and prospective agent P&L remain unfinished. Daily OHLC cannot determine which stop/target triggered first when both are touched. Short borrow/collateral/dividend/recall and inverse-fund histories still require their own contracts. The agent may abstain or hold cash: maximizing percentage invested is not the same objective as maximizing net risk-controlled returns.

## Provider availability and evidence

The public model catalog listed18free endpoints at the check. Authenticated account access succeeded. The selected named model was `nvidia/nemotron-3-super-120b-a12b:free`, with zero prompt/completion price and declared structured-output capability. The first synthetic hold-interface probe returned **rate_limit**. No successful hosted portfolio proposal, backtest or performance is claimed.

The campaign cap is24inference dispatches, at most2percycle, including failures; no paid fallback. Requests have a45–60second timeout and bounded output tokens, and prompts/responses/model identities must be retained. A current catalog and account GET do not establish remaining request capacity or inference availability. No retries or other accounts are used to evade limits.

Current LLMs may know subsequent historical outcomes. Historical direct-model action replay is exploratory even with hidden tickers/dates; it cannot alone justify promotion. A generated deterministic native policy can be frozen and evaluated like other rules, with all reused-history limitations. A discretionary LLM policy requires genuinely prospective timestamped shadow evidence and validated execution. Twelve wall-clock hours, partly after the market closes, cannot manufacture a long prospective track record.

## Initial validation and independent findings

The first bounded foundation measurement passed47tests for the initial action-contract/runner prototypes; its sealed command, source archives and receipt remain preserved. Independent review then found a multi-action risk-price defect and a detached-child lifecycle defect. Both were treated as engineering failures requiring regression repairs, not strategy gains. The aggregate price-book repair now retains all prior action limit/mark valuations; its38focused tests pass. The detached-process, finite-clock and fail-closed receipt repairs passed29runner tests; together with38action-contract tests, an independent reviewer reran67tests successfully. A second bounded verification preserves those repaired results separately from the initial47test prototype. Process cleanup covers trusted descendants retaining the run token, not hostile or environment-scrubbing workers.

The receipt consumer does not independently calculate investment gates merely because a JSON object says `gates=true`. Automatic baseline updates are blocked by default until typed measurement binding and actual gate calculation are integrated and independently verified. This is a prerequisite for future promotion, not a reason to skip the human’s requested performance checks.

No alpha experiment or promotion has completed in this opening run. Shared QQQ benchmarking, exact current-default attribution, source/control reproduction and execution parity remain pending. The state records these blockers explicitly so the next hourly run starts with required measurement work.

Machine protocol/state/events: `amat/outputs/research-campaigns/2026-10-23-adaptive-12h/`. Implementation/design: `amat/scripts/run_incremental_research_cycle.py`, `amat/src/autoresearch/improvements/agent_portfolio_arm.py`, `docs/research/incremental-research-cycle-controller.md`, and `docs/research/openrouter-portfolio-arm-2026-10-23.md`.

Official provider sources: [model catalog](https://openrouter.ai/api/v1/models), [free variants](https://openrouter.ai/docs/guides/routing/model-variants/free), [account/rate limits](https://openrouter.ai/docs/api_reference/limits), and [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs). The authenticated inference receipt, rather than catalog advertising, controls the availability claim.


## Confirmed host choice

The human explicitly chose **this laptop, not the VPS**, after considering remote authentication. The proposed migration was cancelled before activation; staged runtime/source archives were removed and no model credentials were provisioned. The existing local Codex heartbeat remains ACTIVE with the original schedule/deadline. The Performance dashboard reads local campaign state. The VPS paper-session monitor is unaffected and remains remote.


# Final October 23 operational review

Reviewed after the 16:25 Detroit threshold, using the saved 16:00 close and independent committed perps-ledger audit. Full updated report: `docs/research/october-23-paper-session-review.md`; sealed evidence: `amat/outputs/paper-monitor/2026-10-23-final-review/`.

AMAT followed the pinned v14.3.0 hold plan in enabled native whole-share paper mode with complete broker-order coverage and zero unexpected, missed, duplicate or overfilled orders. Closing equity $102,015.78; +$509.97 (+0.5024%) from09:29. Open-position since-entry unrealized profit was separately+$2,285.78, while previous-close account equity change was+$917.41 (+0.9074%). AMAT costs remain unavailable.

Perps closed the common window at$10,000 with no change and zero native fees/funding. A read-only consistent SQLite audit covered1,710recorded midnight-to16:00steps across16distinct hourly decisions: zero nonzero targets, ignored carried-entry signals, orders and fills. Repeated polls are not independent signals. The old October22 alert and all observed collection/readiness incidents remain preserved; no-trade holds do not establish actual-fill parity. Remote collector and paper bots continue independently; no lifecycle/configuration/order changes were performed.
