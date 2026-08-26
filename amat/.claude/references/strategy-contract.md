# AMAT ETF v1 normative strategy contract

## Universe and mode

`SPY, QQQ, IWM, EFA, EEM, TLT, IEF, GLD, SLV, DBC, USO, UUP`

Long or cash only. Fractional shares are permitted in simulation. No leverage, inverse ETFs, shorting, options, futures, or live orders.

## Observation and decision timing

- Daily features use completed exchange sessions.
- A decision labeled session `t` is formed only after the authoritative close for `t` is available.
- It may execute no earlier than the configured opening window of session `t+1`.
- Warm-up requires 252 completed valid sessions plus enough history for the covariance estimator.

## Returns and trend

For adjusted close `P`:

```text
x[i,t]   = ln(P[i,t] / P[i,t-1])
r[i,h,t] = ln(P[i,t] / P[i,t-h]), h in {21, 63, 252}
```

Annualized exponentially weighted volatility:

```text
sigma20 = EWMAStd(x, half_life=20) * sqrt(252)
sigma60 = EWMAStd(x, half_life=60) * sqrt(252)
sigma   = max(sigma20, 0.75 * sigma60, 0.05)
```

Normalized horizon trend:

```text
z[i,h] = clip(r[i,h] / (sigma[i] * sqrt(h/252)), -2, 2)
score  = tanh((z21 + z63 + z252) / 3)
score  = 0 when abs(score) < 0.20
long_score = max(score, 0)
```

The exact EWMA convention, minimum-period behavior, null policy, and timestamp alignment must be frozen in configuration and verified with hand-calculated fixtures.

## Portfolio construction

Raw exposure:

```text
q[i] = long_score[i] / sigma[i]
```

Estimate a 120-session annualized covariance matrix with Ledoit-Wolf shrinkage using only data through the decision session.

```text
forecast_vol = sqrt(q.T @ covariance @ q)
w = q * 0.08 / forecast_vol
```

Apply constraints in a deterministic order:

1. 20% maximum absolute symbol weight.
2. Cluster gross caps: US equities 40%, international 25%, rates 35%, metals 20%, commodities 20%, US dollar 15%.
3. 100% maximum gross exposure.
4. Configured risk-contribution limits, if enabled.
5. Recalculate forecast risk after clipping.
6. Leave residual in cash; never re-leverage beyond 100% gross to hit 8% volatility.

When every score is zero, the portfolio is 100% cash.

## Rebalance

Generate a trade only when a target/current weight delta is at least 2 percentage points, a zero/nonzero transition occurs, a constraint is breached, a corporate action requires it, or an earlier fill failed. Minimum simulated trade: $25 notional.

## Execution and costs

Canonical fill target is the next session's 09:30-09:35 America/New_York raw-price VWAP plus side-adjusted one-way cost. Baseline cost is 2 bps; required stresses are 0, 4, 6, and 10 bps. Missing/zero-volume execution windows produce a missed fill, never an imputed fill.

## Valuation and corporate actions

- Features: fully adjusted daily close.
- Execution: raw intraday prices.
- Valuation: raw close plus explicit share/cash corporate-action ledger.
- Never count the same dividend through both adjusted portfolio returns and a cash credit.
- Split mechanics must preserve equity apart from rounding.

## Cash

Canonical cash return is 0%. Historical three-month Treasury yield is a separate sensitivity and may not be merged into the primary result.
