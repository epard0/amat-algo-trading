# Retained strategy: behavior, performance and limits

The retained equity incumbent is `amat-group-selection-volatility`, v14.3.0. The exact raw configuration hash is `4a8c54e90ae362a5cca4f9e8ae4b1820c916615514e53532a3bc5d758d0f20e8`. The latest completed October 23–24 campaign retained it after five preregistered hypotheses and ten rejected variants. This is a statement about project selection rules and measured history, not global optimality or future profitability. [Project history and primary references](project-sources.md#s1--retained-equity-strategy-and-campaign-results).

## Measured sample and execution assumptions

- Snapshot: `alpaca-sip-core-2026-10-13-verified`; 2016 data seeds signals.
- Account: flat $100,000 from January 3, 2017 through October 12, 2026; continuous account paths, no annual capital reset and no invented terminal liquidation.
- Signals: adjusted completed history, explicit corporate actions, long-or-cash targets across eleven ETFs.
- Historical fills: observed next-session 09:30–09:35 America/New_York VWAP, whole-share purchases/native exits, actual 1% opening-volume entry cap and recorded cash/rounding/liquidity misses.
- Costs: assumed 2/6/10 basis points per side. One basis point is 0.01%; these are modeled combined trading frictions, not measured historical broker spread/slippage. Cash earns zero in this study.
- Evaluation: the 2020-onward slice continues the same account. All historical periods have prior research exposure. The chronological slice is not an untouched out-of-sample discovery.

The [final campaign report](../amat/docs/research-2026-10-24-adaptive-campaign-final.md) contains the full fixed experiment matrix, annual/stress/month/recovery diagnostics and source/reproduction index. [The curated numerical extract](assets/evidence/strategy-summary.json) retains the selected baseline/benchmark metrics, original evidence hashes and units without private machine paths. It was copied from existing sealed evidence, not remeasured during README preparation.

## Baseline and benchmark statistics

| Cost per side | AMAT full return | AMAT 2020 onward | AMAT max drawdown | Modeled AMAT fees | Native QQQ full return | Native QQQ max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 bps | 296.39% | 211.89% | 12.59% | $8,962.67 | 555.07% | 34.75% |
| 6 bps | 259.80% | 192.72% | 12.84% | $24,604.44 | 555.08% | 34.75% |
| 10 bps | 221.88% | 177.60% | 12.98% | $37,259.04 | 555.04% | 34.75% |

At 6 bps, AMAT records 1,929 fills, 2,868 missed instructions and 86.26% average exposure. Modeled terminal equity is about $359,797 from $100,000; cumulative return of 259.80% means approximately 3.598 times initial equity, not 259.80% every year. No Sharpe, CAGR or win-rate number is inferred or invented for the README.

The native QQQ benchmark has a constant 100% target, native whole-share/cash/entry-cap rules and explicit actions. It deliberately exceeds AMAT diversification limits. The uncapped whole-share QQQ reference is identical at this account size because the cap does not bind. The idealized fractional total-return QQQ reference uses adjusted-close ratios after the observed initial opening entry; its 6 bps full return is 569.18% with a 35.00% maximum drawdown. It is not a native executable strategy or an interchangeable risk gate.

AMAT therefore had lower measured drawdown and lower growth than QQQ. These references are not matched for risk, concentration or exposure. Small nonmonotonic QQQ values across costs arise from discrete quantity/cash paths; do not “correct” them by smoothing the evidence.

## What the retained rules do

[The TOML](../amat/configs/strategy/amat_group_selection_volatility.toml) covers US equities (SPY/QQQ/IWM), international equities (EFA/EEM), rates (IEF), metals (GLD/SLV), commodities (DBC/USO), and the US dollar (UUP). It uses 63/126/252-session trend horizons, a 0.2 deadband, at least two agreeing horizons, daily cross-sectional selection, at most four positions and at most three active groups. Signals use at least 252 completed sessions of warm-up.

Volatility configuration blends 20- and 60-session half-lives, with a 5% annualized floor. Covariance uses a 120-session Ledoit–Wolf estimate. The configuration targets 20% annualized portfolio volatility, caps one symbol at 45% and total gross at 100%, and applies individual cluster caps. It does not relever after clipping or promise full investment. The separate structure, risk-budget and learned-regime overlays are disabled in this profile; optional research modules should not be described as active incumbent alpha.

In [adaptive.py](../amat/src/amat/portfolio/adaptive.py), only completed exchange weeks become ranking observations, avoiding partial-week lookahead. The volatility switch gate activates when completed-close fast volatility annualizes above 25% or exceeds 1.5 times slow volatility. For discretionary within-group replacement, a five-day projection of the trailing 20-session relative log-return difference is compared with the configured round-trip hurdle. This is a fixed opportunity proxy, not a calibrated forecast. Invalid or excluded incumbents can exit immediately. Rebalance bands and minimum trade notional are 2% and $25 in the retained configuration.

The inert planning API binds dates, config/snapshot/code identity, sizing inputs, intents, misses and expiry. The Alpaca executor independently checks account/safety/ownership and native execution policy. Historical VWAP accounting cannot establish identical fills under DAY limit orders, cancellation latency and live book liquidity. [The later native-plan audit](../amat/docs/research-2026-10-24-native-plan-broker-parity.md) verifies research-envelope mechanics while explicitly blocking unsupported broker execution parity.

## Rejected candidates and evidence limits

Ten fixed variants examined joint-downside risk, stalled-trend routing, portfolio buy-cost gates, recovery forecasts and completed-gap delays. Some improved a selected return cell or reduced fees/drawdown. None satisfied every all-cost full/forward return, 2023, risk, uncertainty and native compatibility requirement. The return-only buy-cost control illustrates why highest return in one table cell is not synonymous with retained best strategy.

The campaign has 42 comparison rows and 39 unique full histories: duplicate native/uncapped QQQ identities are retained rather than counted as independent evidence. Descriptive bootstrap intervals do not remove repeated-history selection. Later namespace and adapter maintenance improved verification, not the strategy's reported returns. Source-pin mismatches and unknown capabilities remain blockers.

Perpetual paper performance is separate: DOGE/SOL has different signals, fees, funding, account sizing and execution data. No perpetual forward experiment or synthetic model adapter result is used to inflate the equity table. Obtain your own data, review the [strategy API](strategy-api.md), and reproduce frozen assumptions before drawing conclusions from these numbers.
