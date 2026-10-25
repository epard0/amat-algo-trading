# Adaptive asset selection and a conditional ML indicator

## Iteration contract

The decision is which members of the existing, frozen ETF universe receive a
permission to add capital. This iteration adds an optional daily selector to
research signals, with a separate portfolio-membership mode for comparison. It
does not change the paper executor's
authorized configuration or make a research candidate deployable.

The hypothesis is that a small, chronologically fitted ridge signal can identify
favorable short-horizon conditions using trend, candle close location, signed
volume surprise and volatility shock. Selection then withholds new purchases or
increases in assets with weak observed liquidity, excessive or insufficient
relative volume, or redundant cluster membership. A small preference for
yesterday's selected assets limits
unnecessary switching. These thresholds are initial hypotheses, not optimal values.

The prediction unit is one asset at a completed session close. The target is the
existing five-session, next-session execution-window return after modeled costs,
normalized by volatility known at the decision. Training, feature selection and
regularization selection use the existing purged chronological fitter. The score
is a bounded preference, not a calibrated probability of profit.

Success requires improvement against the unchanged baseline and the identical ML
signal without selection, after costs. Guardrails include drawdown, exposure,
yearly consistency, delayed execution, nearby settings, accounting integrity,
and the existing trial-adjusted paired uncertainty tests. A failed gate means no
promotion. Historical checkpoint results are previously inspected evidence, not
a pristine holdout. No profitability improvement is assumed in advance.

Unacceptable mistakes are future data entering features or labels, unavailable
liquidity authorizing a purchase, liquidity gates blocking exits, a changed frozen
universe, or a backtest being described as broker execution. Abstention and cash
are acceptable outcomes. Missing inputs fail closed. Existing configurations
with selection omitted keep their signal and allocation logic. Disabling
selection is the fallback; the existing frozen runtime preserves earlier research
sessions and their execution assumptions.

## Data limits

Daily panels include adjusted high, low and close plus actual daily and execution
window volume. They do not include daily opens or historical bid/ask depth.
Close location measures where a candle finished within its range; it cannot
identify full candle bodies or engulfing patterns. Dollar turnover and execution
window turnover are liquidity proxies, not quoted spread or executable depth.

The frozen ETF set also carries historical universe-selection limitations.
Selection within that set does not establish point-in-time discovery of all
assets that were available in the market.

## Implemented research contract

New sessions use `strategy-development/7`, feature registry `causal-features/5`
and research engine `v1-frozen+research-signals/6`. Version 7 applies a fixed 1%
buy participation limit to the observed execution-window share volume, for the
baseline, candidate and every control. This execution assumption is frozen, not
available for proposal optimization. Version 5/6 programs retain their earlier
behavior when interpreted by their own frozen runtime. Exit orders are not
clipped by this entry limit; a missing or zero-volume exit bar still records a
miss. Sell impact and quoted liquidity remain unmodeled.

At close `t`, `signal.selection` performs these operations in order:

1. Require a finite positive policy score and the existing entry permission.
2. Require complete positive daily and execution-window observations over the
   preceding `lookback` sessions and the current session.
3. Apply `min_dollar_volume` to the smaller of today's raw close times share
   volume and the mean of that quantity over the **preceding** window.
4. Apply `min_relative_volume` and `max_relative_volume` to today's share volume
   divided by the preceding mean share volume. This measure uses reported shares;
   splits can temporarily alter it. Dollar turnover floors do not have that share
   unit discontinuity.
5. Apply `min_window_dollar_volume` to the smaller of today's execution-window
   VWAP times window volume and its preceding mean.
6. Rank surviving scores plus `retention_bonus` for assets selected at the
   preceding close. Break ties alphabetically by symbol. Retention cannot bypass
   a failed eligibility check and does not increase the score used for sizing.
7. Approve up to `top_k` assets, subject to `max_per_cluster`; leave unused slots
   empty. Apply the selected mode while preserving volatility sizing and caps.

The supplied example explicitly uses `mode="entries"`: selection gates only
purchases and increases, after the existing portfolio sizing. All assets retain
their original signal/risk targets, so a liquidity failure cannot by itself force
a sale. Existing holdings can decrease or exit when their signal or risk rules
call for it. Capital reserved by the allocator for an unapproved increase stays
in cash. `top_k` and `max_per_cluster` limit approved additions, not the number
of existing holdings; portfolio weight caps continue to limit holdings.

`mode="portfolio"` zeros unselected scores before portfolio construction. It can
force exits and override the signal policy's minimum hold. This is the default
when the field is omitted, preserving the semantics of the first recorded
selector experiment. The first experiment showed excessive turnover from this
behavior, which motivated the explicit entry-only example. Selected membership
does not guarantee a fill. Cluster limits are a coarse diversity control, not a
measured correlation limit.

The candle feature is the complete trailing mean of
`(2 * close - high - low) / (high - low)` on a shared adjusted-price basis. It is
zero for a valid flat candle, unavailable for malformed or missing candles, and
bounded between -1 and 1 before optional transformations. Five-session close
location summarizes buying/selling pressure near the range boundary without
inventing an opening price.

The supplied `adaptive-ml.example.json` combines normalized momentum, close
location, signed volume surprise and volatility shock. Two interactions let
ridge condition trend on volatility shock and candle close location on volume.
The existing fitter selects regularization and feature count on a purged inner
chronological split, then refits every 126 sessions using only matured labels.
The label's five-session horizon is a forecast horizon; hysteresis can produce a
longer realized holding period.

## Evidence and controls

`selection.json` gives development-period reason counts, the mean number of
selected assets, cash-only preference sessions, and the final development-close
decision by symbol. `selection.npz` contains every session's eligibility, rank,
membership, reason, turnover measurements and selected score, together with the
session and symbol axes. `signals.npz` retains the pre-selection model/policy
scores and the portfolio-mode post-selection `effective_scores`. In entry mode,
`effective_scores` preserve the underlying scores because exits still consume
them; `selection.npz` separately records entry eligibility. Existing `fits.json` records
the model coefficients, transformations and exact training/label boundaries.

Every selector receives `selection_disabled` and, when applicable,
`selection_no_retention` controls, plus the alternative selection mode
(`selection_force_exits` or `selection_entries_only`). It also receives nearby position-count and
liquidity-floor controls. The identical signal without selection is matched to
the candidate's exposure for a paired, trial-adjusted block-bootstrap comparison.
An unresolved exposure match or insufficient evidence adds
`selection_has_no_incremental_value` to the failed gates. These controls use the
same 1% entry participation limit as the candidate. Model proposals receive the
selection grammar and the resulting control evidence.

The new selector does not add a broker plan adapter. The test suite validates
causal selection, next-session simulated fills, the entry participation cap,
recorded partial/missed fills and executable exits. The independent executor
suite tests its existing offline v6 plan-to-submission-to-report contract against
a fake broker. Neither is evidence of a paper-account fill for this ML candidate.

## Reproduce one experiment

From the `amat` package directory, use an unused research directory outside this
checkout. Model fitting and backtesting are local and need no broker credentials:

```bash
uv run --locked autoresearch init /tmp/amat-adaptive-example \
  --name adaptive-example \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --train-end 2022-12-30 --validation-end 2026-08-10 \
  --timeout-seconds 900 --robustness extended

/tmp/amat-adaptive-example/research.sh run-one \
  configs/autoresearch/adaptive-ml.example.json
```

The optional narrative summary chain configured by the CLI may invoke model
providers if credentials are present; leave it unconfigured for a fully offline
run. The verification study used the Python initialization API with
`summary_chain=None`, the September 11 historical checkpoint end, a frozen
50-trial budget, and one synthetic bootstrap path. It evaluated the portfolio
selector first, then the entry selector after tracing excessive forced turnover.
The two attempts used the same features, thresholds, data, dates and execution
limits, with independent frozen source contracts. No checkpoint performance was
needed after the development gates rejected the hypotheses.

## Following iterations

Prioritize empirical failure analysis before adding more learners. The most
useful next additions are historical bid/ask spreads and depth for a cost/size
feasibility test, point-in-time universe membership, correlation-aware selection,
and a versioned research-to-plan adapter with identical inference in backtests and
paper planning. Daily-open ingestion is needed before testing candle-body or
engulfing features. A calibrated probability of net success needs a separate
label/calibration contract and enough independent outcomes; the current ridge
preference should not be presented as one.

Chronological validation is consistent with the ordering and gap rationale in
the [scikit-learn time-series split documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
AMAT additionally purges overlapping forward-label outcomes. Alpaca's
[paper-trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)
describes simulation limitations, including market impact and liquidity; paper
fills alone would not establish executable capacity.
