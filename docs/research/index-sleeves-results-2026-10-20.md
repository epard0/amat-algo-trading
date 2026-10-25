# Tailored index long/short design and research decision

**Decision: reject all eight new index mixes. No index sleeve or live shorting feature was added to active strategy selection or the executor.** The existing core and retained conditional-switching strategies remain unchanged. Research-only code and sealed measurements are retained for reproducibility and audit.

## Recommended design

Use a common strategy-intent interface with signed target exposures, instrument identity, decision timestamp, signal explanation and risk budget. Let QQQ, SPY and IWM have distinct, fixed strategies instead of forcing every instrument through one timing rule. Compare each directional strategy with a long-only ablation. A relative-momentum pair must be treated as a coupled intent: if its short leg cannot be borrowed, both legs stay flat.

A production portfolio allocator would combine sleeve intents, net overlapping instruments, enforce portfolio-wide gross/net, symbol, beta and liquidity limits, reserve collateral and cash, and produce one versioned execution intent per instrument. Borrow eligibility, borrow-rate bounds, locate expiry, recalls, partial fills, dividend liabilities and signed-quantity/account-policy identity must be carried into execution reconciliation. Short-sale proceeds cannot be counted as additional unrestricted spending power. The existing long-or-cash executor remains unchanged until a signed strategy earns retention and execution parity is implemented and tested.

The research implementation deliberately uses two independently funded books: $80,000 native core and $20,000 index sleeve, with no later transfers or implicit daily rebalancing. Allocations drift with results. No cross-book netting benefit or extra leverage is assumed. Native core is actually rerun with $80,000 rather than simply scaling the $100,000 benchmark. This is a conservative funded-sleeve experiment, not a claimed implementation of production broker netting or live short execution.

## Fixed strategies and accounting

QQQ 63-day/200-day trend; SPY 126-day/200-day trend; shorts also require SPY below 200-day mean and own 20/63-day decline. IWM prior-55-day breakout/20-day mean exit. QQQ 5-day pullback/bounce in 126-day regime. QQQ/SPY 126-day beta-hedged 63/21-day relative momentum. Trend positions target 15% annual vol using trailing 60-day sigma, capped 98% gross. Basket equally averages tailored QQQ/SPY/IWM rules. Long-only ablations clip shorts; 2% drift/$25 increase minimum. Fixed 20% seed funding, no optimized mixing weights.

The signed research ledger values actual raw shares, applies split share multipliers, credits long dividends and debits short dividends exactly once, accrues borrowing on calendar days, and separately tracks cash and short liabilities. Orders use prior-close share quantities and execute at next-session observed opening VWAP; reversals close before opening the other side and share a 1% volume budget. Risk increases are clamped to 98% marked gross exposure after fees, using whole-share quantities. Close-to-close gaps can temporarily raise marked exposure; those observations are reported rather than retrospectively corrected. This is a research collateral policy, not a broker margin simulator.

## Registered results

# Index long/short sleeve study

| Approach | Status | Return 2bps | Return 6bps | Return 10bps | Drawdown 6bps |
|---|---|---:|---:|---:|---:|
| Daily core groups · default reference | incumbent | 262.75% | 220.32% | 187.59% | 16.57% |
| Retained conditional switching reference | reference | 274.87% | 234.89% | 200.71% | 15.61% |
| Core + QQQ directional trend | rejected | 239.23% | 210.67% | 176.67% | 11.69% |
| Core + SPY slow trend | rejected | 229.43% | 201.33% | 167.74% | 14.69% |
| Core + IWM breakout | rejected | 208.23% | 181.01% | 148.21% | 15.09% |
| Core + tailored index basket | rejected | 223.29% | 195.55% | 162.09% | 13.72% |
| Core + QQQ pullback/bounce | rejected | 212.57% | 184.81% | 151.53% | 14.70% |
| Core + QQQ/SPY relative momentum | rejected | 208.57% | 180.43% | 146.68% | 15.03% |
| Core + QQQ long-only control | rejected | 244.41% | 216.42% | 182.94% | 11.57% |
| Core + index long-only control | rejected | 226.21% | 198.61% | 165.50% | 12.88% |

## Borrowing stress at 6bps

| Approach | 3% annual borrow | 6% annual borrow | Borrow unavailable | Total borrow fees at 3% |
|---|---:|---:|---:|---:|
| Core + index long-only control | 198.61% | 198.61% | 198.61% | $0.00 |
| Core + tailored index basket | 195.55% | 195.18% | 198.61% | $364.71 |
| Core + IWM breakout | 181.01% | 180.79% | 181.82% | $180.84 |
| Core + QQQ long-only control | 216.42% | 216.42% | 216.42% | $0.00 |
| Core + QQQ pullback/bounce | 184.81% | 184.77% | 185.68% | $32.66 |
| Core + QQQ directional trend | 210.67% | 210.05% | 216.42% | $471.95 |
| Core + QQQ/SPY relative momentum | 180.43% | 179.48% | 184.81% | $1,000.27 |
| Core + SPY slow trend | 201.33% | 200.54% | 208.46% | $535.29 |

## Interpretation and removal

At 6bps the QQQ long/short mix returned 210.67% versus 216.42% for its long-only control and 220.32% for core. The tailored basket returned 195.55% versus 198.61% for its long-only control. Borrow costs contributed, but higher borrow fees alone do not explain the return gap: the short signals also failed to add enough opportunity. QQQ mixes materially reduced drawdown, but the user objective required matching or improving total returns. No lower-drawdown exception was made.

Match or exceed BOTH daily core and retained combined-double conditional reference returns in full history and 2020 onward at each 2/6/10bps; 6bps full drawdown no worse than either reference; repeat return/drawdown floors with 6% annual borrow at 6bps. No-borrow scenario is disclosure, not a return gate. No threshold tuning.

Every new mix failed even the daily-core return floor, before considering the stronger retained conditional-switching reference. No new deployable strategy profile, scanner option, default or live signed-order path was retained. Candidate IDs are rejected by the active analysis-selection API. The frozen research archive and collapsed dashboard audit preserve the discarded measurements; retained references remain selectable. No inference is made that all possible long/short designs must fail, and the study was not retuned after observing results.

## Costs, history and limitations

2016 warmup; flat account January 3 2017 through October 12 2026; continuing annual forward slices 2020-2026, all previously researched history, no untouched holdout.

Next-session observed 09:30-09:35 VWAP; 1% window volume cap; 2/6/10bps per side; borrow 3% annual, stressed at 6% and unavailable, calendar-day accrual; raw shares, splits and signed cash dividends; zero cash yield or short rebate. Historical borrow inventory/rates absent: conditional research only, no live short authorization.

There are 24 primary candidate mixes, 16 borrowing-stress mixes, three newly simulated $80k native core books, and six verified reference series reused from the preceding study. The primary histories and borrowing stresses are exposed in the dashboard with monthly, yearly, stressed-period, growth and drawdown views. Flat fee/borrow assumptions are not historical quote, locate or broker-margin data. Whole-share trades, zero cash yield and no broker netting are conservative model choices. Taxes and intraday maintenance/recall events are not modeled.

The SEC describes the borrower’s dividend obligation and borrowing/margin mechanics in its [introduction to short sales](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-51). This study uses explicit assumptions rather than treating a short return as simply the negative of a long return.

## Reproduction and verification

```sh
amat/.venv/bin/python amat/scripts/run_index_sleeves_study.py --output <new-directory>
```

Completed evidence: `outputs/analysis/2026-10-20-index-sleeves-v2/study/`; sibling `frozen/` contains exact source/configuration. All 51 JSON evidence objects, their canonical sidecars, frozen source identity and snapshot partitions were verified. Signed sleeve sessions include cash, shares, liabilities and individual transactions; equity identities and execution notionals were checked against those records. Maintained source later received type annotations without changing the frozen measurements. A first attempt failed before sealing any index candidate because a NumPy integer could not be serialized; its duplicate incomplete freeze was removed after preserving its protocol/failure note. The successful rerun also makes the pair fail closed when borrowing is unavailable. No strategy thresholds were retuned.

The dashboard defaults to “Core groups · tailored long/short index sleeves”. Failed candidates are visible only in the collapsed audit, including the borrow-stress table. No broker order was submitted.
