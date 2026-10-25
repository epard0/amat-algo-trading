# Historical long/short perpetual study

This study uses `autoresearch.crypto_perpetual_study` to route every fill, funding event, mark, and liquidation through `PerpetualSimulator`. It does not estimate leveraged returns by multiplying spot-return series.

Run from `amat/`:

```sh
.venv/bin/python scripts/download_crypto_perpetual_data.py
.venv/bin/python scripts/repair_crypto_perpetual_marks.py
.venv/bin/python -m autoresearch.crypto_perpetual_study --output outputs/NEW-PERP-STUDY
.venv/bin/python scripts/attribute_perpetual_pnl.py outputs/NEW-PERP-STUDY
```

The data commands use a dated local cache. The daily repair is required because the monthly mark archives have missing days. Downloaded ZIPs and provider checksums are retained; merged CSVs are bound to the manifest by SHA-256. This pinned study includes 486 monthly archives and 12 daily mark supplements. Rerunning downloads may retrieve updated provider archives; preserve an existing manifest and output bundle before creating a new study. Funding gaps are fatal; missing rates are not silently set to zero. A zero-trade-volume hour retains observed mark prices for accounting but prevents order fills and invalidates its signal candle.

## Frozen research design

- Common history: January 2020–September 2026, BTCUSDT and ETHUSDT linear USD-M perpetuals. Training: 2020–2021; validation: 2022; test: 2023; extended test: January 2024–September 2026. A separate continuous January 2022–September 2026 replay carries equity through all post-training regimes without annual resets.
- 48 candidates: hourly/four-hour signals, 5/10/20-day entries, exit lookbacks half/quarter the entry lookback, full/20%-asset-volatility sizing, and 1x/2x policy exposure multipliers. Opposite breakouts can reverse directly; exits otherwise go flat.
- Rank by **training net P&L**, requiring at least 20 fills and no training liquidations. Save one winner per timeframe/leverage and a global winner before any holdout replay. Retain failed and liquidated candidates in training results.
- Native full exposure is 0.98x or 1.96x to leave an execution/collateral buffer. Volatility sizing multiplies those exposures by `min(1, 0.20 / trailing_asset_volatility)`. Thus a policy labeled 2x is not continuously invested at 2x; inspect actual exposure statistics. Account maximum leverage matches the candidate's 1x/2x setting. Position exposure may drift between rebalances, as in a real marked position.
- Each replay starts with 10,000 USDT split between independent BTC and ETH collateral sleeves. No cross-sleeve transfers. A liquidated sleeve halts permanently within that replay. Negative equity is a visible deficit, not silently clipped or reset.
- Fixed price/quantity assumptions: .001 base-asset units per linear contract, integer contracts, .01 quote-price tick. These are explicit simulation choices, not historical exchange-filter reconstruction.

## Event accounting

Signals use completed candles and execute at the next bar's trade-open proxy. All portfolio accounting remains hourly even for four-hour signals. At an hour boundary: mark the old position, apply any funding event to the pre-order position using the actual archived rate and timestamp, execute the planned boundary order, check the hour's adverse mark-price extreme, and mark the close. Funding timestamps in this dataset differ from exact hour boundaries by at most 47 milliseconds; order timestamps are placed at boundary +60.001 seconds to make funding-before-order sequencing explicit. The fill still uses the hourly opening-price proxy, so this timestamp is an accounting order convention, not evidence of a measured fill one minute later.

Funding notional uses hourly boundary mark prices, because the rate archive does not provide an exact event mark. Signed contracts determine who pays or receives. Funding impacts equity and subsequent position sizing. Liquidation checks use real hourly mark highs/lows and a conservative adverse-extreme fill. They do not reconstruct the venue's liquidation engine or exact intrahour chronology. In particular, an extreme might precede a boundary order's simulated delay; this convention can overstate liquidation risk.

Primary execution costs are 5bps commission plus 3bps adverse spread/slippage allowance per side. Stress totals are 15/30bps per side. Commission is anchored to the published regular-user taker example, not a reconstruction of every historical fee tier: [Binance futures fee calculation](https://www.binance.com/en-NG/support/faq/detail/360033544231). Full liquidation incurs the configured normal commission plus an additional 50bps liquidation fee. Maintenance margin is a fixed 0.5% of marked notional. Fee/margin assumptions are scenarios, not verified account-specific terms. No quotes, depth, impact, insurance fund, ADL, taxes, or collateral interest are modeled.

## Matched-risk spot comparison

Original locked spot breakout selections remain unchanged. Both perp and spot exposures are scaled down toward 12% annualized portfolio daily-return volatility using **2020–2021 training only**. Scales are frozen before later-period evaluation; spot is never levered and perp exposure is never increased beyond its native policy. Training calibration is near 12%, not an assertion of identical later realized risk. Spot uses its existing 15bps primary and 30/50bps stress costs; perpetuals use 8/15/30bps. Equal-total-cost rows can also be compared in the saved metrics.

Reports distinguish native P&L from matched-risk P&L and disclose realized volatility, daily drawdown, costs, actual funding cash flows, margin rejections, liquidation counts, and asset-level results. Paired 30-day moving-block intervals describe fixed selected policies. They do not correct for all original/adaptive research. Later spot-market regimes have already been inspected in this workspace; this is temporal holdout evidence with prior-access limits, not pristine unseen or prospective evidence.
