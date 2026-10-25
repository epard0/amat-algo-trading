# Cost-aware switching and rank stability — 19 October 2026

Daily core groups remains the overall default. Eight fixed challengers were evaluated in 27 native autoresearch backtests; all eight failed the registered retention gate. No challenger strategy profiles were added to active selection. Sealed rejected measurements remain available only for audit.

The rules hold an eligible incumbent within a still-selected group while a challenger either remains preferred for two/three consecutive daily selections or clears a cost hurdle. The hurdle compares five days of projected trailing 20-session mean log-return advantage against the round-trip cost (or twice that cost). This is an opportunity proxy, not a fitted return forecast. Each run uses its own known one-way fee assumption. Group exclusion and lost eligibility still cause immediate exit; unoccupied slots fill immediately. Native sizing, group/position caps, and next-session execution remain in force. These rules address membership switches; normal within-holding rebalancing remains native.


| Approach | Status | Full return (2bps) | Full return (6bps) | Full return (10bps) | Drawdown (6bps) | Fills (6bps) |
|---|---|---:|---:|---:|---:|---:|
| Daily core groups · reference | incumbent | 262.75% | 220.32% | 187.59% | 16.57% | 2024 |
| Confirm rank for two days | rejected | 232.88% | 202.79% | 173.63% | 17.05% | 1792 |
| Confirm rank for three days | rejected | 243.03% | 218.43% | 185.81% | 16.94% | 1730 |
| Switch only above round-trip cost | rejected | 257.87% | 229.85% | 211.73% | 16.01% | 1767 |
| Switch above twice round-trip cost | rejected | 262.13% | 234.56% | 207.92% | 15.59% | 1720 |
| Two days + cost hurdle | rejected | 226.22% | 200.28% | 182.16% | 15.67% | 1723 |
| Three days + cost hurdle | rejected | 239.78% | 213.94% | 189.30% | 15.09% | 1700 |
| Two days + double cost hurdle | rejected | 226.90% | 208.15% | 183.18% | 15.24% | 1692 |
| Three days + double cost hurdle | rejected | 239.47% | 220.00% | 187.14% | 15.08% | 1688 |

## Decision and interpretation

Retention required matching or exceeding daily core net returns over both the full history and the 2020-onward continuing-account slice at **each** of 2, 6 and 10 basis points per side, plus no worse full-history drawdown at 6bps. Rules were frozen before trials and were not tuned after results.

The double round-trip hurdle came closest: at 6bps its cumulative return was 234.56% versus 220.32%, drawdown 15.59% versus 16.57%, fills 1,720 versus 2,024, annual gross turnover 22.99x versus 30.21x NAV, and estimated total costs $21,880 versus $28,766. It nevertheless fell short at 2bps in both full history (262.13% versus 262.75%) and 2020 onward. Its paired three-month-block bootstrap 95% interval for annualized log-return improvement at 6bps was −0.20 to +1.10 percentage points, spanning zero. This does not establish a dependable improvement. Rank confirmation alone reduced fills but missed enough opportunity to lower returns. No exception was made for a near miss or a better high-cost result.

## Data and execution assumptions

Verified immutable Alpaca SIP snapshot: eleven core ETFs, 2016 signal warmup; a flat $100,000 account from 3 January 2017 through 12 October 2026. Native next-session observed 09:30–09:35 VWAPs, dividends/splits, a 1% opening-window participation cap, and recorded missed fills. Combined spread/commission/impact assumptions are 2/6/10bps per side; these are stress assumptions rather than measured historical quotes. Cash earns zero. Full-history monthly and annual results, 2020–2026 annual forward slices, and 2018 Q4/Covid/2022 stress windows are in the dashboard. Annual forward slices use continuing accounts without resets, and all these dates have prior research exposure; they are not fresh untouched holdouts.

## Evidence and verification

Evidence: `outputs/analysis/2026-10-19-allocation-switching/study/`, with protocol, 27 records, decisions and SHA-256 sidecars. The sibling frozen directory preserves the exact code/configuration used. The maintained source subsequently received formatting, removal of an unused copied risk helper, and description cleanup; frozen inputs and results were preserved. Frozen source identities, all snapshot partition hashes, and every evidence sidecar were verified. Baseline session records exactly equal the previous independently frozen efficiency study at all three costs.

The dashboard defaults to “Daily core groups · smarter switching”, includes growth/drawdown charts, monthly results, annual/stress comparisons, costs and turnover, and keeps rejected trials in a collapsed audit. No research challenger was promoted and no broker order was submitted.
