# Reusable regime mathematics: feature plan

Status: breadth, volatility shock, and volatility-of-volatility implemented in the
v4 research registry; remaining new feature families are planned. Date: 2026-09-11.
See the [implementation guide](regime-router-implementation.md).
Companion: [strategy and integration plan](regime-router-strategy-plan-2026-09-11.md).
Symbols below use information available at decision t; model parameters are fitted
before the forward evaluation block. Thresholds are research parameters, not facts
about market behavior.

## Prioritized feature backlog

| Priority | Feature | Calculation | Intended use | Required control |
|---|---|---|---|---|
| P0 reuse | Directional efficiency | Absolute endpoint movement / sum of absolute path movements | Distinguish clean trend from noisy travel; condition existing patterns | Existing momentum alone |
| P0 reuse | Variance ratio | Var(q-session returns) / (q × Var(one-session returns)) | Continuous persistence/reversal descriptor | Efficiency and simple autocorrelation; no automatic significance claim |
| P0 reuse | Downside variance share | Sum of squared negative returns / sum of squared returns | Distinguish downside stress from upside activity | Ordinary volatility |
| P0 expose | Breadth | Fraction of available eligible assets with positive trailing return; minimum coverage required | Market-wide confirmation for asset patterns | Existing per-asset momentum and static market gate |
| P0 new | Volatility shock | log(sigma_short / sigma_long) | Flag changes in risk conditions and failed breakouts | Trailing volatility percentile and CUSUM |
| P0 new | Volatility of volatility | Rolling dispersion of log trailing sigma | Identify unstable conditions even when volatility level is moderate | Volatility level and shock ratio |
| P1 new | Filtered regime state | P(S_t=k given observations through t, frozen fit) | Soft sleeve routing and regime-conditioned feature diagnostics | Rule router and static mixture |
| P1 new | State entropy | -sum(p_k log p_k) / log K, K >= 2 | Identify ambiguous regime assignments | Uniform attenuation and exposure-matched results |
| P1 new | State persistence | sum(p_k × A_kk), for row-stochastic transition matrix A | Avoid acting on transient classifications | Existing confirmation and minimum-hold rules |
| P1 new | Recent change evidence | Posterior mass of run length <= a declared small positive window | Flag breaks and reset confidence | CUSUM and volatility shock |
| P2 new | Forecast volatility ratio | One-step forecast sigma / trailing realized sigma | Forecast normalization and optional conservative attenuation | EWMA and current volatility |
| P2 new | News novelty/recency | Deduplicated event count and exp(-log(2) × age / half-life) | Simple callouts, event cohorts, and price-confirmed rules | Counts alone and price/volume-only rules |
| P3 data-gated | Order-flow imbalance | Signed best-bid/ask queue changes aggregated over a declared interval | Execution cost/markout research first | Spread/depth and volume baselines |
| P3 data-gated | VPIN-style imbalance | Mean absolute classified buy/sell volume difference / fixed bucket volume | Exploratory liquidity-risk diagnostic | Trade intensity, volatility, and classification variants |

P0 exposes or extends existing measurements; P1 needs the new fitted-regime
adapter; P2 needs separate incremental evidence; P3 needs new market data and
execution simulation. All new features require registry metadata, units,
availability, clipping, required history, and an explicit missing-data policy.

## Mathematical implementation notes

### HMM filtering and uncertainty

For A[j,k] = P(S_t=k | S_(t-1)=j), compute:

    predicted[k] = sum_j(previous_filtered[j] * A[j,k])
    filtered[k] proportional to likelihood(x_t | state k) * predicted[k]

Normalize with stable log-space arithmetic. Store fitted scaler, emissions,
transition matrix, seed, state mapping, training cutoff, and convergence results.
Begin with a bounded 2/3-state model using a small, nonredundant feature subset.
Do not label states as profitable regimes using forward-block returns. If a
training-based economic mapping is unstable, retain anonymous states and fall
back to rules. Probability entropy measures assignment uncertainty; it does not
measure model correctness or the probability of a profitable trade.

Estimate persistence from the fitted transition matrix as a diagnostic. The
geometric duration 1/(1-A_kk) is meaningful only under that model and becomes
unstable near A_kk=1; cap displayed values and disclose the assumption. Refit
boundaries require consistent state mapping and replay of filter state.

Use filtered/predicted APIs explicitly. The [statsmodels Markov prediction
documentation](https://www.statsmodels.org/stable/generated/statsmodels.tsa.regime_switching.markov_regression.MarkovRegression.predict.html)
distinguishes them from smoothed probabilities and defaults to the latter.

### BOCPD: detect changes, not trading direction

Track the posterior distribution over time since the latest change. Use a robust
predictive distribution, a declared hazard assumption, and bounded run-length
storage with recorded truncated mass. Test abrupt mean changes, variance changes,
outliers, and stationary noise independently. This design follows the run-length
filtering approach in [Adams and MacKay](https://arxiv.org/abs/0710.3742).

Do not copy the screenshot's abbreviated equation into code. Define exactly
which posterior statistic is exposed. In the original recursion with constant
hazard, zero-run-length mass can equal the hazard rather than provide a useful
varying alarm. Test that edge case explicitly; consider recent positive run-length
mass or posterior predictive surprise, with each named according to its meaning.
An alarm indicates changed observations, not a profitable long/short direction.

### Conditional volatility

For an initial GARCH(1,1) experiment:

    sigma_squared[t+1] = omega + alpha * epsilon[t]^2 + beta * sigma_squared[t]

Use finite positive variance, admissible parameters, fit diagnostics, consistent
return units, and a deterministic EWMA fallback. Compare one-step forecasts with
aligned future squared returns using a declared volatility loss, then separately
test any strategy improvement. A good variance forecast need not improve returns.
Fit GJR asymmetry or switching volatility only as later ablations. The official
[arch forecasting guide](https://arch.readthedocs.io/en/stable/univariate/univariate_volatility_forecasting.html)
documents analytical, simulation, and bootstrap forecasting; the chosen method
and seed belong in the artifact.

Volatility ratios used for score normalization and portfolio sizing can attenuate
exposure twice. Test these placements separately before combining them. Do not
automatically introduce Kelly sizing from the screenshot; estimated edge and
covariance errors require their own capital-allocation experiment.

### Order flow and VPIN data gate

Best-quote OFI requires ordered quote updates with bid/ask prices and sizes, a
consistent market/venue definition, duplicate handling, and gap detection. It
does not inherently require full-depth order books, but daily bars or aggregate
execution-window volumes cannot reconstruct it. [Cont, Kukanov, and Stoikov](https://arxiv.org/abs/1011.6402)
study best-bid/ask supply-demand imbalance and short-interval price impact.

VPIN-style estimates additionally need trades, reproducible buy/sell
classification, fixed-volume bucket construction, and handling of trades that
span buckets. Store classification uncertainty. Do not present the resulting
number as a verified probability of informed trading. First evaluate incremental
markout/cost prediction conditional on volume and volatility.

Collect and validate a small forward quote/trade sample before considering a
historical purchase. Confirm entitlements, licensing, timestamps, and retention.
Intraday tests require spread, latency, partial fills, and adverse-selection
modeling; the daily 2/6 bps research cost model is not sufficient evidence here.

## Reuse across AMAT

| Consumer | Integration |
|---|---|
| Existing pattern research | Conditions such as compression -> positive breakout, gated by breadth or low recent-change evidence |
| Weighted/ridge signals | Additional registered continuous columns with training-only fitted transforms |
| New Regime Router | Soft routing weights plus separately tested uncertainty attenuation |
| Diagnostics | Conditional feature usefulness, coverage, redundancy, matched/unmatched cohorts, regime transitions |
| News callouts | Show event evidence, expiry, source coverage, market state, and price confirmation separately |
| Portfolio risk | Investigate forecast-volatility changes in an explicit risk experiment, preserving the existing risk contract by default |
| Executor research | Later quote/trade markout and cost summaries; no runtime model dependency in the executor |

Example research hypotheses:

- Recovery path + rising volume works better when market breadth improves.
- A pullback signal fails after persistent downside shocks; adding change evidence
  reduces those losses beyond an exposure-matched constant gate.
- A contract-award callout followed by positive relative strength has different
  outcomes from either a headline or relative strength alone. Theme-to-ETF
  mappings must be explicit inferred exposures, not direct article mentions.
- A 252-session trend reviewed monthly may retain more net value than a fast
  state switcher; test only after the long-horizon profile is implemented.

## Implementation packages and acceptance

1. Extract/reuse pure structure inputs; register breadth, volatility shock, and
   volatility-of-volatility. Add hand-calculated fixtures, zero/missing cases,
   prefix invariance, and redundancy diagnostics.
2. Add regime-fit artifacts and a causal filter lifecycle; expose probabilities,
   entropy, and persistence through a shared session/symbol trace. Verify state
   permutation, refits, restart parity, and degenerate-fit fallback.
3. Add BOCPD separately with constant-hazard and synthetic-shift tests. Freeze
   hazard, predictive family, truncation, and alarm interpretation per experiment.
4. Add GARCH only after an EWMA comparison; save fit failures and fallback usage
   rather than silently discarding difficult periods.
5. Build a frozen news bridge using existing append-only storage and taxonomy;
   distinguish missing coverage from zero events, and preserve revision timing.
6. Defer OFI/VPIN until quote/trade coverage and a suitable simulator pass their
   data gates. They are not dependencies of the daily Regime Router.

For every family, run a single-feature experiment before interactions, compare
with the closest existing feature, then ablate it from the complete strategy.
Use the same dates, universe, portfolio constraints, and costs for paired tests.
Retain mathematical features only where incremental evidence justifies them;
negative findings remain in the research ledger.
