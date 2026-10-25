# Opportunistic unused-cash index sleeves — 19 October 2026

**Decision: reject all eight candidate sleeves. No new cash sleeve or signed-order executor feature was retained.** Research evidence and the common signed ledger remain isolated under autoresearch for audit/reproduction.

One $100k netted portfolio. Core target capital, including planned purchases, is reserved first. Sleeve gross budget is max(0,98% minus core target gross); no fixed allocation or book transfers. Overlay and core targets net by instrument before orders, share-volume limits and borrowing costs. Core-increase commitments reserve cash ahead of sleeve increases. Prior-close sizing, next-session fills; sleeve liquidation/refunding trades pay normal costs. Single coupled scale preserves pair ratios.

45% absolute symbol exposure; 85% absolute US-index cluster; 100% target gross, 98% fill-time collateral policy; joint forecast volatility <= max(20%, native core risk). Core weights are never increased to recover foregone risk. If original core already consumes a limit, no incremental violating overlay is allowed. Gap-driven excess is disclosed, not backdated.

Signed research ledger uses whole shares, collateral reserve and conservative sale-volume caps; its execution mechanics differ from native fractional long-or-cash ledger. A matched no-sleeve control prevents crediting those differences to overlay alpha. Native and conditional references remain independent absolute return floors. This does not assert live execution parity.

# Opportunistic netted index sleeve study

| Approach | Status | Return 2bps | Return 6bps | Return 10bps | Drawdown 6bps |
|---|---|---:|---:|---:|---:|
| Daily core groups · native reference | incumbent | 262.75% | 220.32% | 187.59% | 16.57% |
| Retained conditional switching reference | reference | 274.87% | 234.89% | 200.71% | 15.61% |
| Core in netted ledger · execution control | reference | 241.08% | 204.73% | 172.12% | 15.89% |
| Unused cash · QQQ directional trend | rejected | 248.56% | 208.97% | 172.47% | 13.37% |
| Unused cash · SPY slow trend | rejected | 227.64% | 188.23% | 153.16% | 19.67% |
| Unused cash · IWM breakout | rejected | 249.50% | 209.92% | 174.53% | 17.10% |
| Unused cash · tailored index basket | rejected | 241.49% | 201.84% | 166.82% | 16.87% |
| Unused cash · QQQ pullback/bounce | rejected | 229.45% | 191.87% | 158.59% | 16.38% |
| Unused cash · QQQ/SPY relative momentum | rejected | 238.20% | 195.13% | 157.95% | 16.45% |
| Unused cash · QQQ long-only control | rejected | 259.60% | 219.54% | 183.98% | 12.83% |
| Unused cash · index long-only control | rejected | 247.18% | 208.93% | 174.95% | 14.46% |

## Interpretation

Netted QQQ long-only improved the matched research ledger at 6bps from 204.73% to 219.54%, but still failed native core at 220.32% and the retained conditional reference at 234.89%. This matters: the matched ledger differs in whole shares, collateral reserve and conservative sell-volume caps. One cannot attribute that execution-model difference to alpha or claim the best overlay beats native core. All candidates failed the preregistered cross-reference/cost/drawdown guards, so none was retained.

The study uses one net book, not an 80/20 segregated allocation. Core planned commitments reserve capital before sleeve increases; overlays use only residual target capital, and normal trading fees apply when capital is withdrawn. Shared instruments net before trades and only actual net negative shares pay borrow fees. Pair signals scale together; unavailable borrowing permits an offsetting hedge that remains net long, but forbids a net negative leg. Target symbol/US-cluster/gross/forecast-risk limits bind jointly. Supplementary index positions can add positions beyond the core’s own four-position selection; no claim is made of an aggregate four-position execution mandate.

Match or exceed native daily core, retained conditional-double reference AND same-ledger no-sleeve control returns in full history and 2020 onward at every 2/6/10bps; 6bps full drawdown no worse than all three. Repeat full/walk-forward return and drawdown floors at 6bps with 6% annual borrow. No-borrow scenario is disclosed. No threshold tuning. At least one strict return gain over same-ledger control required.

Next-session observed 09:30-09:35 VWAP; 1% window volume cap; 2/6/10bps per side; borrow 3% annual, stressed at 6% and unavailable, calendar-day accrual; raw shares, splits and signed cash dividends; zero cash yield or short rebate. Historical borrow inventory/rates absent: conditional research only, no live short authorization.

Forty candidate scenarios plus three matched-ledger controls were simulated; six sealed native/conditional reference series were reused. All 51 evidence JSON objects, source hashes and data partitions verified. All 40 candidate ledgers were independently reconciled for share/split continuity, cash, observed fill prices, close equity and aggregate 1% window-volume caps. No live parity or historical borrow-availability claim is made.

```sh
amat/.venv/bin/python amat/scripts/run_opportunistic_sleeves_study.py --output <new-directory>
```

Dashboard comparison: “Unused cash · portfolio-netted index sleeves”. Rejected variants appear only in its audit, including borrow-cost stresses. Evidence: `outputs/analysis/2026-10-19-opportunistic-netted-sleeves/study/`.
