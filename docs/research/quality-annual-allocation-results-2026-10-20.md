# Yearly allocation updates on quality strategies

Completed October 20, 2026. Neither candidate passes all gates; the current default remains unchanged. No active candidate profile was created.

Two fixed transfers apply saved 2024/2025/2026 allocation recipes to the smaller-position and wider-buffer quality strategies. Both retain their 40% symbol cap, original quality filter and respective 2%/3% trade buffers. Saved recipes alter trend deadband, position count, cadence, target volatility and allocation volatility power where selected; no annual exit overlay or fitted feature is introduced. This is a replay of prior past-only selection, not newly trained annual optimization.

## Net returns

| Strategy | Cost per side | Full 2017–2026 | 2020 onward | Max drawdown | 2023 | Fees |
|---|---:|---:|---:|---:|---:|---:|
| Current default | 2 bps | 296.39% | 211.89% | -12.59% | 6.55% | $8,963 |
| Smaller positions + quality | 2 bps | 313.04% | 225.99% | -11.79% | 6.84% | $8,759 |
| Smaller positions + yearly updates | 2 bps | 304.68% | 219.39% | -11.79% | 6.84% | $7,443 |
| Wider buffer + quality | 2 bps | 317.78% | 232.83% | -12.10% | 6.86% | $8,297 |
| Wider buffer + yearly updates | 2 bps | 302.92% | 220.99% | -12.10% | 6.86% | $6,909 |
| Current default | 6 bps | 259.80% | 192.72% | -12.84% | 5.74% | $24,604 |
| Smaller positions + quality | 6 bps | 283.25% | 214.19% | -12.20% | 7.73% | $24,942 |
| Smaller positions + yearly updates | 6 bps | 287.81% | 217.93% | -12.20% | 7.73% | $21,462 |
| Wider buffer + quality | 6 bps | 278.46% | 214.38% | -12.52% | 5.96% | $23,193 |
| Wider buffer + yearly updates | 6 bps | 267.04% | 204.90% | -12.52% | 5.96% | $19,700 |
| Current default | 10 bps | 221.88% | 177.60% | -12.98% | 5.07% | $37,259 |
| Smaller positions + quality | 10 bps | 249.40% | 195.32% | -12.82% | 6.98% | $38,581 |
| Smaller positions + yearly updates | 10 bps | 250.93% | 196.62% | -12.82% | 6.98% | $32,920 |
| Wider buffer + quality | 10 bps | 247.35% | 199.40% | -13.19% | 5.53% | $35,887 |
| Wider buffer + yearly updates | 10 bps | 248.42% | 200.32% | -13.19% | 5.53% | $30,991 |

## Decision

Smaller-position annual updates improve full-history and 2020-onward returns at 6 and 10 bps, but lose at 2 bps and trail the protected 2020-onward frontier at 10 bps. Wider-buffer annual updates improve at 10 bps but lose at 2 and 6 bps. Both are rejected as overall replacements. Stronger per-cost observations are retained in frontier.json for future comparisons, even though these candidates are rejected.

The existing broad-universe data gap and signed-sleeve accounting gap remain unresolved. No overall-best claim or promotion is permitted while these gaps remain.

## Yearly attribution at 6 bps

| Strategy | 2024 | 2025 | 2026 through October 12 | Fills | Turnover |
|---|---:|---:|---:|---:|---:|
| Current default | 11.98% | 33.89% | 20.20% | 1929 | 228.39x |
| Smaller positions + quality | 12.44% | 37.32% | 20.89% | 2111 | 225.34x |
| Smaller positions + yearly updates | 14.62% | 36.31% | 20.89% | 1988 | 204.49x |
| Wider buffer + quality | 13.96% | 36.64% | 22.42% | 1714 | 212.31x |
| Wider buffer + yearly updates | 12.28% | 36.37% | 20.74% | 1587 | 192.44x |

## Validation and limitations

- 9 unchanged control histories exactly reproduce the prior sealed study across all three costs.
- 6 candidate reruns reproduce exactly; zero fractional purchases.
- 72 actual observed-prefix planner comparisons match native instructions, missed fills and embargo state.
- All candidate sessions before 2024 match their respective quality parent exactly. Saved past-only recipe selections and native causal annual activation are unchanged.
- Preserved 40% position cap, original quality filter and 2%/3% trade buffers. Annual exits remain disabled as in the saved allocation recipe.

- Three snapshot partitions and all 297 native source files match pinned hashes. Saved annual selection files match original raw SHA256 receipts.
- Protocol, fixed economic configurations, source, data and bootstrap specification were preregistered. Initial digest encoding was rejected before any measurement and corrected without economic changes; event retained.
- Full native continuous-account replays use whole shares, observed next-session opening VWAP, native 1% opening-volume cap, corporate actions, missed fills and 2/6/10 bps per side. No annual capital reset or cash yield.
- Chronological 2020–2026, monthly/recovery and 2018 Q4/COVID/2022 stress results are saved in every study record. Annual updates first activate in 2024; earlier stress windows exercise the unchanged parent.
- Paired circular block bootstrap uses 5/20/60-session blocks, 10,000 draws, seed 20261020 and Bonferroni 95% intervals across 18 tests. These are diagnostics on reused history.
- No hosted proposal/review was used. The finite campaign remains paused. No executor restart, orders or broker-authority changes.

## Reproduction

Run the source-bound run_quality_annual.py with --study study --arm <arm> --cost <2|6|10> --output <new immutable file>, using the frozen runtime referenced in study/protocol.json as PYTHONPATH. The sealed planning protocol and check_planning.py reproduce observed-prefix execution comparisons. Dashboard publication is a sealed projection of these records.
