# Alpha and return improvement review

Review of retained evidence and the current working tree, September 30, 2026 (local date).

The strongest evidence favors preserving the v13 baseline while researching portfolio construction, executable funding, concentration control, and economically distinct signals. It does not favor immediately deploying the current genetic, multi-timeframe, entry-filter, or annual switching variants. Improving absolute return and establishing repeatable alpha are different objectives: more exposure can improve the former without improving the latter.

## Scope and evidence standard

The inventory covers **70,268 files, approximately 21.69 GB**, across the main output tree, research workspace, executor state, documentation, and the linked isolated P13 output tree. The audit mechanically screened all **270 Markdown documents (246 unique contents)** and examined the major result families, selected machine-readable comparisons, current source, and P13 baseline accounting records in depth. It did not manually inspect every raw row in every artifact or rerun all historical searches. Duplicated frozen runtimes, repeated profile aliases, tests, and bootstrap paths are not independent strategy evidence.

The main working tree has extensive pre-existing modifications and untracked source. Its HEAD is `7fd145a411e8f8a925b28cd4343dbf4f9a98a950`; that commit alone does not identify the current implementation. P13 continuous replay exists in `/home/supasuge/Documents/amat-p13-workspace`, with different runner and exit source hashes. Its results are relevant research evidence, but its implementation is not silently assumed to exist in the main checkout. This review changes only this audit directory.

Supporting artifacts: [inventory](inventory.csv), [mechanical report screen](report-screen.json), [calculations and source hashes](calculations.json), and [reproduction script](analyze_evidence.py). The screen is an index, not a second source of independent evidence.

## What the results actually show

| Evidence family | Observed result | Implication |
|---|---|---|
| v6 to v13, 2018–September 11, 2026 | CAGR 5.52% to 13.34%; volatility 5.46% to 13.38%; mean exposure 53.77% to 86.36%; conventional Sharpe 1.010 to 1.004 | A meaningful absolute-return improvement, substantially associated with a changed risk/allocation mandate; not proof of new persistent alpha |
| v13 exposure match against v6 | Development Sharpe-ratio difference +0.273; later difference −0.305 | The development improvement does not generalize on this diagnostic; matching gross exposure does not match factor exposures or volatility |
| 20% target study | `historical_target_met: false` | The retained study does not substantiate a 20% annual-return claim |
| Earlier 100-strategy batch | No escalation; re-ranking under the incremental objective found no candidate above the +0.05 margin | More proposals alone did not solve the economic problem; old and new score versions must not be mixed |
| Entry filters | Selected filter later CAGR 5.99% versus baseline 7.51%; worse drawdown | Filtering away apparent bad entries also loses valuable participation |
| Entry-quality ML | Default policy remained at baseline size because regime evidence was insufficient; pooled control failed uncertainty | The feature exists; neither implementation nor a small historical gain establishes usefulness |
| Adaptive ridge portfolio experiment | Training Sharpe −0.255 versus 0.745 baseline | No justification for making that ranker the default |
| Intraday batch | 26/50 candidates completed; every trading candidate in the retained table lost money; some candidates made no trades | Shorter holding periods need a new cost-supported mechanism, not a translation of daily indicators |
| P4 nested genetic study | Incumbent fallback; zero incremental outer return; family-adjusted null p 0.75 | Infrastructure worked, but it did not find incremental alpha |
| P10 | 2,305 training evaluations; unchanged baseline won validation over 12 searched finalists | Added MTF/exit overlays did not earn selection |
| P11 | 2,307 training evaluations; balanced/return won one of three outer folds; defensive won none | Selected parameters and feature signs are unstable across periods |
| P12 | MTF/exit removal sometimes helps and sometimes harms; 2025 neither-overlay recipe 37.94% annualized versus 29.37% full | Portfolio settings and interactions need separation; this retrospectively inspected ablation is not a validated winner |
| P13 continuous replay | Baseline beat every switching profile at 2/5/10/20 bps | State continuity does not rescue the current switching selections |
| P7 shadow campaign | Retained ledger has one historical backfill, is suspended, and has no prospective observations | Historical extension counts are not a live track record |
| Local paper reports | One cash-only dry-run observation | No measured paper alpha can be inferred from these files |

Primary evidence: [return-target review](../../amat/docs/return-target-review-2026-09-14.md), [exposure matches](../../amat/outputs/return-target-2026-09-14/selection-v3/exposure-matches.json), [20% decision](../../amat/outputs/return-target-20pct-01/decision.json), [score comparison](../../amat/docs/exposure-score-comparison-2026-09-12/report.md), [filter results](../../amat/docs/baseline-refinement-verification-2026-09-14.md), [entry-quality bundle](../../amat/outputs/entry-quality-2026-09-14/README.md), [intraday results](/home/supasuge/Documents/amat-research/intraday-50-final-20260912/report.md), [P10](../../amat/outputs/genetic-patterns/2026-09-29-p10/performance-report.md), [P11](../../amat/outputs/genetic-patterns/2026-09-29-p11/performance-report.md), [P12](../../amat/outputs/genetic-patterns/2026-09-29-p12/performance-report.md), [P13 foundation](../../amat/docs/genetic-patterns/p13-foundation-verification.md), [P7 status](../../amat/docs/genetic-patterns/p7-current-status.md).

For the P13 period **December 29, 2023–September 25, 2026**, the continuously carried portfolios returned:

| Policy, 2 bps | Cumulative return | Difference from baseline |
|---|---:|---:|
| Baseline | 54.50% | — |
| Balanced switching | 26.58% | −27.93 percentage points |
| Return switching | 39.59% | −14.91 percentage points |
| Defensive switching | 29.67% | −24.83 percentage points |

These are cumulative returns, not annualized alpha. P13's full-calendar baseline CAGR is 12.50%, including cash-only warm-up from 2016; it should not be confused with the earlier 13.34% figure on a different period and annualization basis. P11's independent recipe warm-ups cannot be concatenated into an executed switching portfolio; P13 is the relevant stateful comparison.

## Priority 1: Make the research objective measure incremental economic value

The current [objective](../../amat/src/autoresearch/objective.py) uses exposure-matched candidate Sharpe minus fixed baseline Sharpe, less a complexity penalty. This is useful and already implemented. It is not factor-adjusted alpha: equal gross exposure can conceal very different equity, duration, commodity, dollar, and precious-metals risks.

There is also a metric naming issue. [Core metrics](../../amat/src/amat/metrics/__init__.py) calculate `sharpe = CAGR / annualized_volatility`. The September return review separately calculates conventional arithmetic-return Sharpe. Those measures can rank strategies differently. The core implementation does not subtract a supplied cash-return series from daily returns; its zero-cash contract currently avoids that ambiguity. A new cash-yield study needs proper excess-return metrics.

Implement a versioned metric schema with conventional excess-return Sharpe, CAGR/volatility explicitly labeled separately, active return, information ratio, downside/tail measures, and factor attribution. Fit factor exposures on past windows and evaluate residuals on subsequent windows; use dependence-aware uncertainty and disclose collinearity. Keep investable benchmark replays at comparable costs alongside frictionless diagnostic benchmarks. A positive intercept in a small or selected sample is not enough.

Preserve two explicitly separate research tracks: (1) absolute net growth inside a declared risk budget, and (2) incremental information at matched risk/factor exposure. Existing return and eligible-score leaderboards are a foundation; do not simply lower the +0.05 threshold or overwrite historical score semantics. Establish drawdown, turnover, tail loss, and weak-period limits before selecting a return winner.

The [paired bootstrap](../../amat/src/autoresearch/diagnostics.py) already adjusts its tail using the session's declared maximum experiment count. Extend trial accounting across sessions, genetic searches, universes, feature grammars, and restarts. Use deflated Sharpe or whole-search null tests as additional diagnostics; do not pretend a new session resets prior discovery. The [Deflated Sharpe Ratio paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) specifically addresses selection bias and non-normal returns. The existing P4 whole-search null is useful but covers its declared family, not all project research.

**Acceptance:** compare unchanged baseline and each candidate using one frozen metric contract, multiple chronological folds, matched controls, global trial history, and later prospective observation. Evaluator changes require a new research program.

## Priority 2: Make intended allocations executable

Direct aggregation of P13's 2,698 baseline session records found **1,227 fills and 620 missed records: 490 insufficient-cash and 130 participation-cap records**. These are records, not a unique-order rejection percentage: a partially filled order can also generate a missed remainder.

In [simulated execution](../../amat/src/amat/execution/__init__.py), quantities are fixed using the previous close; sells execute first; buys are processed alphabetically; a purchase is rejected in full if its cost exceeds available cash. The participation limit clips buys before the funding check. This is deterministic and openly modeled, but it means alphabetical order and overnight gaps can affect realized portfolio composition. Fixing it may improve or reduce historical return; unfilled purchases are not automatically missed profits.

Implement a shared funding policy: estimate cash needs including fees and conservative gap reserves, allocate scarce purchase budget proportionally or by a predeclared marginal-utility rule, resize affordable quantities, and handle residual intent without duplicate orders. Use only information actually available at that execution decision; never size an opening order using the completed future five-minute VWAP. In simulation and executor, retain intended, authorized, submitted, filled, and missed quantities separately. Test capital scales and order permutations.

The [paper adapter](../../executor/src/executor/broker/alpaca_paper.py) submits market DAY orders; it does not replicate the historical five-minute VWAP contract. Add implementation-shortfall attribution from signal close to arrival, arrival to fill, missed quantity, fees, and timing. Estimate spread/impact by asset, time, volatility, and participation. Model exit liquidity too: the current participation restriction applies to buys. The [AQR trading-cost research](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs) supports treating trading costs as conditional on trade size and market characteristics, rather than one constant rate.

**Acceptance:** net-return and target-tracking improvement under realistic costs, no increase in hidden factor concentration, reconciled cash/positions, causal sizing, and executable paper parity. Do not count simulated perfect funding as free alpha.

## Priority 3: Control concentration by risk, including gaps

The raw P13 ledger permits direct attribution of the **January 30, 2026 loss of 9.867%**. There were no fills or distributions that day, so prior quantities times price changes reconcile the loss:

| Asset | Weight at prior close | Contribution to portfolio return |
|---|---:|---:|
| EEM | 21.61% | −0.48 percentage points |
| EFA | 21.37% | −0.20 percentage points |
| GLD | 22.18% | −2.28 percentage points |
| SLV | 24.21% | −6.91 percentage points |

GLD and SLV together represented **46.39% of settled equity** and approximately **93.1% of the day's loss**. The configured metals target cap is 45%; settled holdings can drift above it because of market movement and rebalance tolerances. This is not proof of a broken target-cap implementation. It is evidence that nominal caps and slow covariance estimates do not tightly bound tail concentration.

The allocator already has Ledoit-Wolf covariance, volatility targeting, symbol/cluster/gross caps, and residual cash. However, setting `risk_contribution_limit` raises an explicit not-implemented error. The v13 TOML also contains `max_daily_loss`, `max_drawdown_halt`, and `use_kelly`, but source search found their definitions and no current strategy consumers. They are legacy configuration fields, not active loss protection. A close-triggered loss rule could not guarantee avoiding an already-realized gap anyway.

Implement risk-contribution constraints and factor/cluster stress-loss budgets, evaluate faster downside covariance as a bounded overlay, and report settled versus target risk separately. Compare with a simply lower-risk baseline so that any benefit cannot be attributed only to holding less. Avoid automatic leverage restoration after a risk reduction. Remove or label dormant risk settings in reports, and fail validation when a user explicitly requests an unsupported mechanism.

**Acceptance:** improved net geometric growth or a clearly specified tail-risk/return tradeoff across several crisis and benign periods, including gap execution, against static risk-scaled controls. Do not optimize a special rule around January 30 alone.

## Priority 4: Replace rigid turnover rules with cost-aware selection

The baseline [selection schedule](../../amat/src/amat/portfolio/cross_section.py) refreshes top-four membership every five sessions, checks absolute eligibility daily, and continues daily sizing. Another research [selection module](../../amat/src/autoresearch/selection.py) already has a retention bonus, liquidity gating, and cluster-count limits. Therefore a generic proposal to add selection hysteresis would duplicate existing functionality.

The useful extension is an economically calibrated switch hurdle: replace an incumbent only when its challenger's expected incremental holding-period benefit exceeds round-trip costs, uncertainty, and transition risk. Use rank persistence and a turnover budget, with distinct treatment for mandatory risk exits. Begin with simple causal ranking rules; don't assume a noisy return predictor is necessary.

Weekly scheduling was not universally better: the later historical daily-ranking control earned 15.08% at 6 bps versus 12.58% weekly. That justifies testing the mechanism under fresh folds, not selecting daily ranking after inspecting that outcome. Fixed rebalance thresholds of 3–4% also failed earlier refinement tests; simply widening all bands is not supported.

**Acceptance:** stable net improvements versus daily, weekly, and existing retention-bonus controls, measured by traded notional and opportunity cost rather than trade count.

## Priority 5: Seek new information and complementary returns

Most current candidates transform the same price/volume information. P10 already covers 27 symbolic features at two lookbacks and five timeframes; adding another technical indicator is unlikely to create independent information just because its formula is different.

A higher-value research direction is a dated, point-in-time feature store for earnings surprises/revisions, relative valuation, macro surprises, rates/curve state, and economically meaningful carry proxies. Each requires actual publication/availability timestamps, revision handling, universe eligibility, and a mechanism hypothesis. Newsdesk and web-evidence tools already exist, but recent news cannot be backfilled as if it were historically available. Data acquisition is a prerequisite, not an immediate promised improvement.

Broaden the opportunity set by economic exposure and liquidity using point-in-time membership, including failed/delisted instruments where relevant. The inherited eleven-ETF universe already reflects prior selection; more correlated equity ETFs alone provide little diversification. Keep ETF-only additions distinct from futures/shorting, which need new financing, collateral, borrow/roll, and execution contracts. [Long-history trend research](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf) motivates broader cross-market research, but does not prove those results transfer to this ETF implementation.

For model design, test a small number of genuinely different sleeves and weight their marginal contribution to the combined portfolio. Candidate sleeves might include medium-horizon trend, cross-sectional relative strength, and a separately validated slower mean-reversion mechanism. The existing [stat-arb module](../../amat/src/amat/stat_arb.py) only measures pair structure; it emits no trades and is not a deployable market-neutral sleeve. The failed short-horizon intraday batch does not justify enabling one.

Shrink estimated sleeve utility toward baseline/zero, cap correlated contributions, and prefer slow bounded weight updates to choosing one annual winner. Preserve baseline fallback. A mixture is a hypothesis to test; P13's failed hard switching does not prove blending succeeds.

**Acceptance:** positive marginal portfolio contribution after costs, low residual correlation to existing sleeves, survival of feature ablation and held-out asset/time tests, and adequate forward evidence.

## Priority 6: Improve model training without repeating failed filters

Entry-quality ridge models, matured labels, causal fits, uncertainty penalties, regime validation, and opportunity-cost checks already exist. Sparse regime samples made the initial policy neutral, and a pooled variation failed uncertainty. Prefer hierarchical partial pooling across assets/regimes over fitting more tiny buckets. Predict relative opportunity cost or net incremental utility aligned with intended holding periods. Penalize missing a large trend as well as entering a loser. Compare a bounded sizing adjustment with hard rejection, using calibrated uncertainty and baseline fallback.

No evidence here justifies immediately replacing ridge with a large neural model or reinforcement learning. Sample size, label validity, costs, and independent information are more pressing constraints than model capacity.

## Priority 7: Close the research-to-observation loop

The engine already freezes runtimes and data, has failure memory, static/shuffled/delayed controls, uncertainty checks, and a best-strategy archive. Some old scenario failures were caused by controls dropping required context and producing zero fills. The [September 28 correction](../../amat/outputs/research-control-fix/report.md) fixed those controls; three replays became eligible but still failed the score margin. Their old gate failures should not be used as fresh evidence of economic weakness.

Build explicit supersession links so proposals and summaries consume corrected receipts, not stale narratives. Require one mechanism and one disconfirming control per experiment; reserve budget for portfolio-only and static controls. Stop spending a batch on nearly identical deadband/volatility changes after evidence saturates. Track proposal validity, duplicates, useful experiments per budget, and reproducibility rather than judging agent quality by persuasive commentary.

Generated engineering edits are currently only staged as `awaiting_isolated_validation` in [engineering.py](../../amat/src/autoresearch/engineering.py). Complete the isolated evaluator and code-aware experiment identities before treating those proposals as tested capabilities. This is research productivity work, not itself an alpha strategy.

P7 remains suspended on an IEF prevalence alert and its artifact does not bind to current source. Preserve that latch and lineage during any runtime migration. Collect genuinely prospective, completed observations under a frozen policy. Do not relabel its 1,945 historical extension sessions as forward observations. The local executor report is insufficient for performance inference; remote/Docker broker state was not queried by this audit.

## A bounded next experiment program

| Order | Experiment | Controls and decision rule |
|---|---|---|
| 1 | Versioned metrics, evidence lineage, and factor attribution | Same frozen baseline paths; reconcile metric definitions and superseded controls before ranking anything |
| 2 | Funding-aware allocation and executable cost model | Current alphabetical/all-or-nothing behavior versus predefined budget rules; multiple capital scales, gaps, actual available timestamps |
| 3 | Risk-contribution and cluster stress budgets | Current caps and static lower-risk baseline; measure growth, expected shortfall, drawdown and rebound participation |
| 4 | Cost-aware rank switching | Daily/weekly/retention-bonus controls; preserve mandatory exits and stateful transitions |
| 5 | Portfolio-only versus MTF versus exits | Extend P13's pending matched controls; preserve original holdings/state at intervention as well as full-history counterfactuals |
| 6 | One new point-in-time information family | Predeclare mechanism, train-only transformations, availability calendar, and feature-removal controls |
| 7 | Small complementary-sleeve mixture | Equal mix, fixed mix, baseline, and hard-switch controls; costs and joint tail correlation included |

Cash-yield implementation is a separate capital-efficiency experiment: use the yield actually attainable in the account, historical rates available at the time, instrument costs and execution. It cannot be counted as trading alpha. The current research Program explicitly requires zero cash yield, so this needs a new contract rather than a hidden benchmark change.

The most defensible near-term investments are better measurement, feasible portfolio implementation, and concentration-aware allocation. New signals should earn their place by improving the combined, executable portfolio. No return uplift is estimated for these proposals because no counterfactual implementation was backtested in this review, and the accumulated historical searches do not establish consistent prospective alpha.
