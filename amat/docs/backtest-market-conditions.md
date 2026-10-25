# Broader market-condition testing

New research sessions use `strategy-development/7`. The strategy, feature, and
router paths share historical regime reporting and an extended scenario battery.
The current report is `market-robustness/2`, with generator version 2. Old frozen
sessions retain their original code and settings; a new session is required to
use the expanded coverage.

## What runs

Every new experiment reports historical behavior in SPY-defined bull/bear and
quiet/volatile regimes. Labels use only completed data and are lagged one session
when attributing account returns. Counts and a 60-observation sufficiency flag
accompany each cohort. These are interleaved observations, so the report does not
invent a drawdown by concatenating them into a fictional portfolio path.

Initialize with `--robustness extended` to add:

| Scenario | Assumption |
|---|---|
| Positive drift | 12% annual log drift plus correlated innovations |
| Negative drift | -20% annual log drift plus correlated innovations |
| Range | Mean-reverting log-price displacement, persistence 0.93 |
| Whipsaw | Alternating 10-session directional pressure with noise |
| Crash/recovery | A declared common shock 63 sessions into the measured interval, a large execution gap, and a 20-session recovery component |
| Correlation break | Cross-asset shock correlation rises from 0.35 to 0.95 after 126 sessions, with higher volatility and negative drift |
| Zero-drift Gaussian | Constant volatility and zero log drift; a reference for the other return families |
| Student's t, df 5 | Correlated heavy tails, scaled to unit innovation variance |
| Student's t, df 3 | Heavier tails with finite variance and an infinite theoretical fourth moment |
| Laplace | Exponential tails from a shared exponential variance mixture |
| Negative skew | Centered, standardized skew-normal common and asset factors, with shape -4 |
| Volatility mixture | An 8% high-volatility state, with Gaussian scale factors 0.65 and 2.5 normalized to unit variance |
| Volatility clustering | Causal GJR-GARCH: alpha 0.08, beta 0.88, negative-shock coefficient 0.04; initial variance 1 |
| Gap down / gap up | Separate common -0.25 and +0.25 log-price shocks, also applied to the execution window at offset 63 |
| Liquidity crisis | Student's t shocks; 20 sessions at 2.5x volatility and 1% volume, a -0.18 common log shock, five zero-volume execution windows, and 24 bps costs throughout the run |
| Flat | Unchanged closes and zero execution gaps, exercising zero-volatility behavior |
| Joint bootstrap | Two paths by default, with mean block lengths 20 and 60 sessions; more paths rotate through 20/60/126 |
| Missed fills | Every 20th or 10th attempted fill withheld, at 6 bps one-way costs |
| Higher costs | Separate 12 and 24 bps one-way costs on the real price history |
| Delayed signals | Two additional sessions of delay at 6 bps one-way costs |
| Combined execution | Two additional sessions of delay, every tenth attempted fill withheld, and 24 bps one-way costs |

The default extended suite has **25 paired cases**: 17 synthetic, two resampled,
and six historical execution stresses. Every case evaluates candidate and baseline.
`--scenario-synthetic-paths N` repeats each synthetic recipe with N different seeds
(1–8, default 1). Total cases are `17 * N + bootstrap_paths + 6`. With N=2 and the
default bootstraps, this is 42 paired cases. Seed assignments remain stable when
the repetition count increases.

Names describe generating assumptions, not guaranteed realized returns. A small
set of cases is not a probability distribution of future performance. Positive and
negative drift refer to log returns: zero log drift does not imply zero expected
simple return. A 0.25 downward log shock corresponds to approximately a 22% simple
price decline before the session's other innovations.

Each case reruns the candidate and frozen baseline on the same world. Features,
router weights, chronological model fits, policy state, portfolio sizing, and
accounting are recomputed; original signals are not pasted onto alternate prices.
The existing cost, delay, exposure, static-blend, and other controls remain active.

All five innovation families have theoretical mean zero and unit marginal
variance before per-asset volatility scaling. The base pairwise correlation is
0.35. Student's t, Laplace, and the volatility mixture use shared radial scales,
so large moves can affect several assets together. Skewed shocks combine
independent standardized common/asset factors. Volatility clustering evolves
from past shocks only; its parameters are declared assumptions, not a historical
GARCH fit. The correlation-break case changes its correlation separately.

The separate `amat stress-heavytail` command retains the existing empirical
correlation calibration, Pareto jump process, and four-state Markov regimes.
It covers an additional stress axis, including Pareto jump magnitudes with
infinite variance below tail index 2. Such distributions cannot honestly be
normalized to unit variance, so the Student's t generator requires df > 2.

## Data and interpretation

Synthetic paths retain the original calendar and calibration history. Their
volatility and base volume use only observations before the measured anchor.
Prices, execution windows, ranges, and full-session volume are generated together.
The scenario code, parameters, and seeds are frozen and the resulting panel has
a recorded content digest.

Bootstrap paths resample whole joint rows of returns, gaps, execution volume,
high/low ratios, and full-session volume. The existing bootstrap implementation
previously omitted context columns, which prevented range/volume strategies from
being tested correctly; those columns now follow the sampled price row.
Execution prices are normalized using the same session's raw close and its
adjusted return. This preserves the execution-price/close ratio across splits
and dividends, instead of introducing an extra artificial gap. Missing windows
and zero volumes remain untradable after resampling.

Alternate worlds embed total returns in their prices and use raw=adjusted with
no additional corporate-action cash flows. Historical execution stresses retain
the original prices and action ledger. No scenario consumes the validation
checkpoint: the source ends at the frozen development cutoff.

Historical results, historical execution stresses, synthetic cases, and bootstrap
cases have separate labels in `robustness.json`. Bootstrap ordering can draw from
the entire development sample and breaks dependence across block boundaries; it
is a diagnostic hypothetical world, not new independent validation. Short blocks
can erase precisely the longer trends a strategy relies on.

Scenario profitability does not increase the main research score. Warnings or
nonfinite scores fail the scenario integrity gate; execution exceptions prevent
a completed measurement. Losing money in a declared bear case is reported as
a limitation, not automatically used to disqualify every long-only strategy.
Agents receive compact scenario outcomes to formulate subsequent experiments.

The report also records expected versus completed cases and checks whether each
scheduled event and its recovery fit inside the evaluation window. A short path
that ends before a correlation break or recovery cannot pass the coverage gate.
Numerical overflow in an extreme generated path raises an explicit error; tails
are neither clipped nor redrawn until a favorable seed appears.

## Distribution and tail diagnostics

Every candidate/baseline scenario result includes `return_distribution`. Synthetic
cases also include `market_return_distributions`, one series per asset. These
contain observation counts, return quantiles, sample skewness, excess kurtosis,
lag-one squared-return correlation, and 95%/99% VaR and CVaR (expected shortfall).
Scenario CSV exports retain scalar return diagnostics, distribution names, path
indices, and coverage flags. Proposer feedback and run-review evidence include
coverage and 99% tail losses alongside the existing return/drawdown comparisons.

The inputs are simple session returns, excluding the initial equity anchor.
VaR is the inverse empirical lower-tail quantile. CVaR averages exactly the worst
5% or 1% of empirical probability mass, including a fractional boundary
observation. For example, a 30-return sample has 1.5 observations in its 5% tail;
the second observation receives half weight. Tied zero-return days cannot dilute
the tail. Loss values are positive, floored at zero, and are not annualized.

Tail observation counts show how thin the evidence is at either level. Shape
statistics use population divisors and are descriptive sample moments. Constant
series have null skewness, kurtosis, and squared-return correlation; their tail
losses remain finite. Reports serialize as strict JSON without NaN or infinity.

## Launch

```bash
cd /home/supasuge/Documents/amat-workspace/amat
export RESEARCH_DIR="/home/supasuge/Documents/amat-research/conditions-$(date +%Y%m%d-%H%M%S)"
uv run --locked autoresearch init "$RESEARCH_DIR" \
  --name market-conditions --robustness extended \
  --scenario-bootstrap-paths 2 --scenario-synthetic-paths 1 \
  --scenario-seed 20260911 --max-experiments 50 --timeout-seconds 1800

"$RESEARCH_DIR/research.sh" run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/breadth-router.example.json
"$RESEARCH_DIR/research.sh" status
```

The short research command enables extended coverage by default:

```bash
./research 1 --scenario-synthetic-paths 2 --no-open
```

`autoresearch init` defaults to historical coverage unless `--robustness extended`
is supplied. Extended mode defaults to an 1800-second worker limit. Path counts
are frozen at initialization and each is bounded to 1–8. More repetitions cost
more engine reruns; the timeout still applies, and reaching it does not count
as completed coverage.

Each candidate directory contains `robustness.json` plus full accounting results
under `robustness/<scenario>/<candidate-or-baseline>/`. Intervention metadata in
each run's config includes scenario identifiers and the fits computed on that
path. The source snapshot, frozen generator, seed, and panel digest permit replay.
All saved files participate in the existing artifact hash checks.

## Method references

- [NumPy's Student's t generator documentation](https://numpy.org/doc/stable/reference/random/generated/numpy.random.Generator.standard_t.html)
  describes the degrees-of-freedom parameter and the distribution's heavier tails.
- [Robert Engle's Nobel lecture](https://www.nobelprize.org/uploads/2018/06/engle-lecture.pdf)
  explains fat tails, persistent volatility, and ARCH models. The specific GJR
  coefficients in this suite are our declared stress assumptions.

See [automatic LLM run reviews](research-run-summaries.md) for the explanation
printed after each completed experiment.
