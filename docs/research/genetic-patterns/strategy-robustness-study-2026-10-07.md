# Strategy robustness, optimization, and failure attribution

This research separates two questions: whether a configuration earns more after
costs, and whether its features add information beyond changes in portfolio risk,
exposure, holding periods, and execution. Neither an exhaustive finite grid nor a
large Monte Carlo campaign can prove a globally optimal strategy for unknown
future markets.

The current study uses v13 as its main competitor. A fresh replay reproduces the
retained P13 baseline metrics exactly over 2,698 sessions, 2016-01-04 through
2026-09-25. The full-calendar 12.50% CAGR includes initial indicator warm-up;
the 2024–2026 comparison window has 17.20% CAGR and 15.01% maximum drawdown at
2 bps one way. These are different measurement windows, not conflicting results.
The later `rotation5_16` return-target study did not establish a superior replacement.

**Iteration compact**

- Goal: choose robust strategy configurations and identify why modifications help
  or hurt the continuous portfolio after modeled costs.
- Decision owner: the user selects the objective, search design, and compute cap.
- Baseline: v13, with its exact universe, data identity, execution assumptions,
  participation cap, historical origin, and inherited state preserved.
- Success: incremental economic benefit on separately evaluated periods and worlds,
  within the chosen risk constraints and stable across nearby parameter values.
- Guardrails: tail loss, drawdown, exposure, turnover, execution feasibility,
  leakage, incomplete data, selection bias, and computational resource limits.
- Unacceptable mistakes: selecting on final evaluation outcomes, treating a failed
  run as zero return, silently dropping a difficult world, or presenting simulated
  frequencies as probabilities of real future performance.
- Evidence limit: all retained historical dates have already been researched.
  A new chronological split can evaluate a procedure retrospectively, but cannot
  restore pristine holdout status to these dates.
- Completed: a fresh historical baseline, 34 fixed-baseline synthetic replays,
  one inert-configuration perturbation replay, and paired uncertainty/interaction
  analysis of 76 verified existing component paths, without new parameter selection.
- Follow-on execution: the user subsequently approved the nested experiment design.
  The objective, domains, folds and seeds are now frozen in the
  [2026-10-08 registration](/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-08-nested-strategy-search/registration.md).
  That bounded study uses the recommended robust-growth objective and two-hour cap.
- Follow-on result: the daily campaign completed 1,234 portfolio replays and 960
  GP expression slots across five fitted prefixes. Exact GP removal improved both
  selected historical GP configurations. The latest fold retained v13 in every
  family. A joint-proposal domain bug was documented, corrected under a separate
  amendment, and labeled exploratory because original outcomes had been opened.
  Read the [completed findings](/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-08-nested-strategy-search/findings.md)
  and [full results](/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-08-nested-strategy-search/report.md).
  This remains daily evidence; the minute-data scope and next research objective
  have been presented as separate user choices.

**Requested design choices**

| Choice | Recommended | Alternatives and tradeoffs |
|---|---|---|
| Definition of best | Robust net growth constrained against baseline drawdown and tail risk | Highest growth with a 25% historical drawdown ceiling permits more risk; an unranked Pareto shortlist defers the choice |
| Search | Broad mixed-parameter sampling, local refinement, matched interventions, separate final evaluation | Exhaustive small grid gives complete declared-grid coverage but limits dimensionality; a matched-budget optimizer comparison costs additional trials |
| Resources | Benchmark first, then cap at 2 compute hours and 10 GB of new artifacts | 8 hours/25 GB supports more seeds; 30 minutes/5 GB supplies preliminary diagnostics |

The table records the choices originally presented with the pilot. The subsequent
registered daily study fixes its own objective and resource bounds before selection.
No paid compute or execution deployment is part of this research request.

Measured results, confidence intervals, charts, and reproduction commands are in
[the completed pilot report](/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-07-robust-strategy-study/report.md).

**How to determine whether a component helps**

Use the same dates, asset universe, initial capital, cost/impact assumptions,
market draws, and inherited portfolio state for each comparison. Retain the
complete calendar, including warm-up and cash periods. Generate each world once
and run every compared configuration on that same world. Common random numbers
reduce comparison noise; they do not turn correlated scenario variants into
independent samples.

Separate effects with four layers:

1. Fixed-parameter removal: hold the selected recipe constant and remove only
   the feature, exit rule, or timing mechanism. This measures contribution to
   that particular recipe.
2. Retuned removal: give each reduced model the same search budget and select
   it on the same development/selection partitions. This asks whether the
   component remains useful when alternatives are allowed to adjust.
3. Matched exposure/risk controls: compare a simpler model at comparable ex-ante
   risk and capital usage. Any risk-matching calibration must use only earlier
   data, never the final evaluation volatility.
4. Interaction experiments: evaluate combinations of feature on/off, exits
   on/off, allocation choice, ranking cadence, and—once implemented—refitting.
   A five-factor binary design has 32 cells; this is a useful tractable full
   factorial, unlike all permutations of all numerical parameters.

Distinguish the GP program from a conventional indicator, the timeframe blend,
and the overall selected policy. P11 used different methods in different years:
the return profile used GP in 2024 and conventional momentum/variance-ratio
features later. Removing all MTF contributions therefore does not isolate GP.

For each final paired contrast, estimate uncertainty using shared contiguous
time-block resampling and variation across independently generated worlds. Use
several block lengths and respect the effective dependence horizon. Declare a
minimum economically useful effect before selection. Classify evidence as helpful,
harmful, practically equivalent, or inconclusive; failure to find significance
is not proof of equivalence.

Bootstrap intervals for a previously selected fixed strategy are conditional
intervals. To evaluate the adaptive process, repeat fitting, search, selection,
and portfolio replay inside each outer chronological split or generated world.
Keep the trial ledger, including rejected configurations. Use a multiple-testing
method appropriate to the registered family of comparisons; a deflated Sharpe
diagnostic can supplement this but is not a replacement for separate evaluation.
[Bailey and López de Prado, Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).

**Search design**

Ordinary gradient descent assumes a useful local derivative. Discrete lookbacks,
tree topology, ranking changes, position caps, thresholds, and stop activation
make this portfolio objective piecewise flat and discontinuous. The prior code
already uses random global proposals followed by smaller mutations around good
candidates; it is a derivative-free local search, not gradient descent.

Start with a declared mixed search space and explicit family coverage. Use
space-filling/random samples for broad coverage, then TPE or population-based
search for refinement. TPE supports conditional search spaces; differential
evolution is useful for numerical parameter blocks without requiring derivatives.
Benchmark the more elaborate optimizer against random search at an equal number
of portfolio evaluations rather than assuming it is better.
[Optuna TPE documentation](https://optuna.readthedocs.io/en/stable/reference/samplers/generated/optuna.samplers.TPESampler.html),
[SciPy differential evolution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html),
[Bergstra and Bengio, Random Search](https://www.jmlr.org/papers/v13/bergstra12a.html).

Search the following blocks independently first, then jointly with meaningful
conditional combinations:

| Block | Parameters and interventions |
|---|---|
| Core signal | Trend horizons, deadband, clipping, confirmation, conventional feature families |
| GP | Grammar/terminals, program complexity, effect size, polarity, population budget; fit programs inside training only |
| Timeframes | Individual daily/weekly/monthly/intraday views and normalized mixtures with explicit availability |
| Selection/allocation | Ranking cadence, number of positions, volatility divisor, target risk, caps and capital usage |
| Trade management | Rebalance band, stop/take-profit levels, holding period and reentry embargo |
| Adaptation | Refit interval/window, warm start, acceptance and expiry; unavailable until a causal adaptive implementation exists |
| Operational sensitivity | Costs, slippage, missed fills, timing, liquidity and data availability; stress assumptions are not optimized away |

Canonicalize inactive parameters: an unused stop threshold or a timeframe weight
under a disabled component is not a distinct economic strategy. Retain search
costs and failures. Allocate equal budgets across independent strategy families,
then give the joint search its own registered budget. Test combined signals through
the common portfolio allocator; averaging separate equity curves is not a valid
joint portfolio backtest when capital and positions overlap.

The frozen-v1 engine intentionally ignores the legacy `risk` overlay. A fresh
perturbation replay changed its maximum drawdown and daily-loss thresholds to
0.1%, its position limit to one, and Kelly to false: the entire equity path
remained exactly unchanged. Exclude these inert fields from this engine's search.
The active allocation and cross-sectional limits are separate. In particular,
the legacy 20% drawdown-halt value does not constrain these backtests.

Do not trim evaluation to favorable years to save computation. Reduce candidate
count or seed count while preserving the registered calendar. Candidate screening
can use earlier data and approximate metrics, but final portfolio evidence must
come from the authoritative accounting engine.

Prefer parameter regions with stable performance over a narrow best point. Report
nearby perturbations, effect size by regime, and optimizer-seed stability. A selected
configuration is best within the tested domain and objective, not universally optimal.

**Diverse simulated markets**

The available daily generator covers bull, bear, mean-reverting range, whipsaw,
crash/recovery, correlation break, Gaussian log returns, Student-t(3/5), Laplace,
skewed shocks, volatility mixtures, clustered volatility, positive/negative gaps,
liquidity crisis, and a flat control. The initial pilot uses all 17 families at
2 and 10 bps, with one common seed per family. This is 34 portfolio replays and
17 distinct modeled paths, not 34 independent markets or a tail-probability estimate.

The larger campaign should vary independent seeds, volatility, cross-asset
correlation, tail severity, persistence, trend strength, reversal speed, liquidity,
and shock timing. Keep development, selection, and evaluation seeds separate;
hold out some scenario parameter regions or families to test generalization.
Calibrate market generators only from the prefix available at each anchor.

Include both:

- Joint block-resampled historical markets, with price moves, cross-asset dependence,
  opening gaps, volume, and candle context sampled together and the strategy replayed.
- Explicit parametric processes, including geometric Brownian motion as a transparent
  control and non-Gaussian/state-dependent alternatives for stress testing.

For a zero-arithmetic-drift GBM null, the log-return drift needs the corresponding
`-0.5 * variance` correction. The existing Gaussian control has zero log drift
and must not be described as a martingale with zero expected arithmetic return.
A clean trend with low noise is an idealized positive control; it is not a realistic
estimate of future opportunity. Simulated scenario weights are design choices,
not estimated occurrence probabilities.

Place transient shocks after the tested model has activated, preserve pre-shock
holdings, and allow enough recovery time. The earlier 544-path coherent experiment
placed its transient shocks in 2020 before P11 adoption in 2024. Its persistent
regimes remain useful evidence, but those transient cases do not test active-policy
crash response. The new daily pilot starts its synthetic suffix in January 2024;
its offset-63 shocks occur while v13 is active.

The pilot is daily portfolio execution with daily synthetic context. It does not
test native minute inference, 250-minute refitting, quote-level stops, or coherent
multitimeframe microstructure. Those claims require their respective engines and
data contracts; sampling daily returns cannot establish them.

**Why performance is poor: diagnostic questions and experiments**

| Suspected cause | Evidence to examine | Experiment that distinguishes it |
|---|---|---|
| Too little or poorly allocated exposure | Cash time, ex-ante risk, asset/cluster constraints, market beta | Match risk/capital policy before comparing feature value |
| Costs consume the signal | Gross/net contrast, turnover, spread/impact sensitivity | Same strategy on a cost ladder, retaining a zero-cost diagnostic only |
| Lagging trend in range markets | Losses around reversals, trade duration and repeated replacements | Matched regime slices and timing/threshold interventions |
| Exit/feature conflict | Stop/take-profit events, embargo duration and missed subsequent winners | Full feature × exit factorial plus retuned reduced models |
| Overfitting | Development-to-evaluation deterioration, sharp parameter peaks, seed instability | Nested selection, untouched prospective observations, null controls and trial-aware statistics |
| Drift or missing features | Distribution/prevalence changes, coverage and saturation | Prefix-only availability controls and frozen-versus-adaptive comparison |
| Weak execution assumptions | Misses, participation caps, gaps, partial fills and delay | Execution perturbations while retaining identical decisions where appropriate |
| Implementation error | Accounting residuals, future-prefix sensitivity, replay disagreement | Exact ledger reconciliation and parity tests before interpretation |

Treat plots of feature importance, correlations, and regression intercepts as
diagnostics. The main causal evidence inside this simulator comes from matched
interventions with all other conditions held fixed. That evidence is still
conditional on the simulator and its market assumptions.

**Outputs and verification**

The study directory is
`/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-07-robust-strategy-study`.
It retains the fresh baseline receipt, frozen diagnostic driver, source/data
identities, verified historical comparisons, bootstrap intervals, component
interactions, generated market paths, and per-path replay receipts. The report
must distinguish newly executed paths from imported verified evidence and from
statistical resamples of existing returns.

Before selecting a new configuration, freeze the approved objective, meaningful
effect threshold, search space, cost assumptions, split/seed partitions, resource
budget, and failure policy. Replays should verify source/data identity, matching
calendars, full accounting, no future-prefix influence, and correct activation.
Failures remain in the study denominator; they are never replaced by a luckier seed.

No changes to the active strategy, executor, or deployment are part of this study.
