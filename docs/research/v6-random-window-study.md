# V6 randomized short-horizon robustness study

Run 2026-08-13 against immutable snapshot `alpaca-sip-2026-08-11`. All sessions
through 2026-08-10 are burned research. Randomization changes which known
history is summarized; it does not turn that history into a live holdout.

## Protocol

- Seed `20260813` selects 12 one-month, 12 three-month, and 12 six-month
  calendar windows from the final three years.
- Each duration draws four quiet, four normal, and four volatile SPY-realized-
  volatility windows, so turbulence cannot be omitted by chance.
- Half of each duration/regime group was declared development and half
  validation before strategy or parameter measurements.
- One full causal account is run with pre-window warm-up and positions. Window
  returns are sliced from that account, so a sample does not reset to cash.
- Every strategy uses next-session 09:30-09:35 raw VWAP, corporate actions,
  missed fills, 0% cash yield, and all-in one-way frictions of 2, 4, 6, and 10
  bps. The bps charge is a combined commission/spread/slippage proxy; queue
  position, taxes, broker-specific fees, and calibrated nonlinear market impact
  remain outside the model.

The complete output is
`outputs/v6/random-windows/v6-random-windows-20260813T231103Z.json`; its SHA-256
is `7f8b8cf7dcc9671a70771b6ffba8c344eefa129ee8fa38da01bcd79e5671a2db`.
Companion summary and window-level CSVs use the same timestamp.

## Aggregate result at 2 bps

| Strategy | 1m positive / median | 3m positive / median | 6m positive / median | 1m / 3m / 6m fills |
|---|---:|---:|---:|---:|
| v1 | 83% / +1.98% | 83% / +2.60% | 92% / +2.51% | 40 / 125 / 202 |
| v2 29-asset core | 92% / +1.57% | 83% / +2.37% | 83% / +3.30% | 58 / 181 / 301 |
| v3 news | 83% / +1.98% | 83% / +2.60% | 92% / +2.51% | 40 / 125 / 202 |
| v4 switching | 83% / +1.40% | 92% / +2.48% | 83% / +2.97% | 36 / 121 / 194 |
| v5 breadth | 100% / +1.71% | 83% / +2.53% | 83% / +2.78% | 47 / 140 / 246 |
| v6 regime experiment | 83% / +1.33% | 75% / +1.04% | 92% / +2.04% | 34 / 103 / 164 |
| **v6 static** | **83% / +1.42%** | **83% / +2.05%** | **92% / +2.04%** | **37 / 121 / 188** |

The archived v2 managed overlay was removed after rejection. Both v2 files are
therefore measured as their surviving 29-asset core and are numerically
identical; `use_kelly` is inert. V3 is identical to v1 because its point-in-time
news scalar contains no de-risked session.

V5's one-month result is attractive but does not overturn its rejected
full-history evidence: it uses a wider universe, had only 12 sampled one-month
windows, and falls back to 83% at three and six months. It earns further forward
observation, not promotion.

## Friction and transaction-count findings

Canonical v6 at 2 bps had a 32%, 25%, and 28% round-trip win rate over the
one-, three-, and six-month aggregates. At 10 bps those fell to 22%, 20%, and
24%. Three-month positive-window frequency fell from 83% to 75%, and its
10th-percentile return worsened from -0.65% to -1.08%.

This falsifies the simple objective "make many small profitable trades." The
strategy can make more trades, but the extra trades are not reliably
profitable after friction. The trend payoff remains dependent on a minority of
larger winners. The correct optimization objective is positive expectancy and
downside stability after costs, with transaction count and win rate as
diagnostics rather than primary targets.

The 36-cell development-only parameter grid crossed volatility targets
{4%, 5%, 6%, 7%}, deadbands {0.15, 0.20, 0.25}, and rebalance thresholds
{1%, 2%, 3%} at 6 bps. Canonical 6% / 0.20 / 2% ranked first. It produced 89%
positive development windows, +0.50% development 10th-percentile return, and
2.14 median development Sharpe. Because the training winner is the existing
configuration, no parameter change is indicated. Lower deadbands and 1%
rebalance thresholds raised fills but did not win the safety-first ranking.

## Regime result

The dynamic per-asset regime overlay remains rejected. Against static v6 it
reduced fills but lowered the three-month positive-window rate from 83% to 75%
and the median return from +2.05% to +1.04% at 2 bps. In volatile windows at
6 bps, its three-month median was +0.44% versus +0.49% for static v6, with a
worse downside tail. Regime labels remain auditable context, not capital timing.

## Expanded per-asset stop/target learning

The contextual-bandit grid expanded to five stop losses
{3%, 5%, 8%, 10%, 15%} and six reward/risk ratios
{0.75, 1.0, 1.25, 1.5, 2.0, 3.0}: 31 actions including no explicit exit, per
asset, measured at 6 bps. Training ends 2024-12-31; a later paired validation
lower-confidence bound with `z=2.64` gates activation.

Zero of 12 rules activated. TLT selected 5%/5% in training but its validation
LCB was -0.00024; IEF selected the same pair but its LCB was exactly zero and
did not clear the +0.001 minimum. No learned stop or target belongs in v6.

## Entry-liquidity safeguard

The uncapped v6 replay found a maximum fill equal to 8.84% of observed five-
minute window notional, concentrated in UUP and DBC. A historical VWAP cannot
support a no-impact assumption at that participation.

V6 now supports an optional entry-only participation cap. Buys are clipped to
a declared share of observed window volume and the remainder is recorded as a
missed fill; sells are never clipped, so an execution guard cannot trap risk.
The sensitivity artifact is
`outputs/v6/random-windows/v6-participation-sensitivity-20260813T231503Z.json`.

The 0.5% buy cap is the best next hypothesis. At 10 bps it changed three-month
positive-window frequency from 75% to 83%, its 10th-percentile return from
-1.08% to -0.95%, and its median from +1.56% to +1.56% while slightly reducing
cost. At 6 bps in volatile windows it improved three-month median return from
+0.61% to +0.84% and six-month median from +1.35% to +1.47%. Reported maximum
overall participation can remain above 0.5% because risk-reducing sells are
intentionally uncapped.

The cap was selected after viewing burned data, so it is implemented but not
enabled in canonical v6. Forward paper testing should compare uncapped and
0.5%-capped entries without revising the threshold.
