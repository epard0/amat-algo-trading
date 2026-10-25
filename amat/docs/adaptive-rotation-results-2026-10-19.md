# Adaptive rotation implementation and fixed-arm results

The opt-in implementation is `configs/strategy/amat_v14_adaptive_rotation.toml`.
The original design, baseline configuration, data, executor configuration and
promotion state were preserved. `adaptive_rotation` is excluded from canonical
serialization when disabled, preserving existing strategy identities.

## Implemented behavior

- The native portfolio engine reads an explicit exchange-session calendar.
  Group and member rankings read only completed weeks: the previous week's last
  observed session becomes available at the first session of the following week.
  This conservative delay handles a holiday-shortened week without consulting a
  future price row or using an incomplete Friday bucket.
- Three economic groups partition the same eleven instruments as v13. Robust
  twelve-week group performance relative to QQQ selects up to two eligible groups;
  at most two members per group and four members total survive. Group strength
  does not require defensive assets to beat equities before they can be held.
- Persistent group-adjusted ranking uses the mean of aligned weekly asset-minus-
  group returns divided by robust dispersion, with a numerical floor and clipping.
  It is explicitly a persistent relative-strength score, not regression alpha or
  the upstream latest single residual Z-score. Native absolute trend and horizon
  eligibility still apply daily.
- The price-only regime compares QQQ with its 26 completed-week average, reduces
  exposure after a 3-session decline of at least 3%, and uses a ten-session fast
  cooldown plus slow-regime recovery persistence. It has no VIX input. Risk-off
  and fast multipliers are 0.5 and 0.3. Reductions apply **after** volatility sizing;
  the allocator cannot scale them back up to its risk target. These are decision
  triggers, not guaranteed loss ceilings or broker stops.
- A two-rank incumbent bonus supplies selection hysteresis. Incumbency refers to
  deterministic selected membership, not inferred actual broker holdings.
- Native covariance, caps, residual cash, next-session execution, corporate actions,
  missing-window behavior and participation constraints remain in force. Planning
  and the full-panel scanner use the same adaptive cache. A single-asset duration
  screen refuses adaptive configs because group context is required.

The combined configuration uses all four new components. The study also evaluates
them separately, the existing structure filter, volatility-aware allocation and
hysteresis added to daily selection. No knobs were fitted after these results.

## Method and provenance

`autoresearch.improvements.adaptive_study` registers fixed recipes and uses the
source-bound autoresearch evaluator. `scripts/run_adaptive_study.py` freezes source
and configuration before measurements, then runs each approach at 2/6/10 bps per
side. This is a bounded fixed-arm study, with no model calls, search-driven winner
selection, promotion or broker orders.

Evidence: `outputs/analysis/2026-10-19-adaptive-rotation/study/`.
The protocol SHA-256 is
`167599fcbcdf17ef77097b5d7667df7f7f7471bfdbcc45f4543c8db28fab26e9`.
The frozen Python source is retained under the adjacent `frozen/` directory.
Every run JSON has a checksum and is bound to the protocol. All 30 runs completed
with no runner warnings. Native measurements use a 1% opening-window participation
cap and the verified core SIP snapshot through **2026-10-12**.

Full simulated paths begin in late 2016 after warm-up. The same portfolio continues
through each displayed period; period selection does not reset holdings. 2024,
2025, 2026 YTD, recent and full-history views are retained. These dates have prior
research exposure and do not constitute a fresh holdout.

The rerun v13 and daily-selection projected session paths and metrics agree with
all six retained reference/cost records. Five of six full internal-record hashes
also match. The v13 6-bps internal hash differs while every retained session value
and metric is identical; byte-identical full internal records are therefore not
claimed for that case. Both studies retain their original hashes.

## Results: 2026 YTD, through October 12

All returns are net of the specified simulation cost. Drawdowns are magnitudes.

| Approach | Return at 2 bps | Drawdown | Fills | Return at 10 bps |
|---|---:|---:|---:|---:|
| v13 reference | 11.39% | 15.01% | 136 | 10.65% |
| Daily selection | 17.12% | 13.40% | 258 | 14.04% |
| Group selection only | 27.92% | 12.19% | 140 | 26.81% |
| Group-adjusted ranking only | 5.94% | 16.72% | 195 | 4.49% |
| Slow/fast price-risk only | 8.63% | 14.21% | 147 | 7.53% |
| Turnover buffer only | 11.29% | 15.17% | 128 | 10.32% |
| Adaptive rotation combined | 10.38% | 15.57% | 162 | 9.21% |
| Existing trend-quality filter | 14.27% | 13.84% | 167 | 13.31% |
| Existing volatility-aware allocation | 10.82% | 12.48% | 131 | 8.63% |
| Daily selection + turnover buffer | 15.99% | 11.83% | 171 | 13.23% |

Group selection is the strongest 2026 arm, with lower average invested exposure
(82.9%) than v13 (88.4%) or daily selection (89.2%). That is descriptive evidence,
not a risk-matched causal proof. Its 2025 return is 38.27% versus 31.20% for daily
selection, but its 2024 return is lower: 8.35% versus 9.54% for daily selection and
10.08% for v13.

Over the full available simulated path, group selection returned **254.96%** versus
**255.95%** for v13 and **247.72%** for daily selection. The 2026 advantage therefore
does not establish an unconditional improvement over v13. The combined strategy's
full-path return was 107.00%; its lower exposure did not recover the lost upside.
Combining individually plausible features is not evidence that their interaction
helps. The residual-ranking component and price-risk overlay reduced 2026 return.

Daily selection plus hysteresis reduced 2026 fills from 258 to 171 and drawdown
from 13.40% to 11.83%, while lowering return from 17.12% to 15.99%. The existing
structure filter is another useful isolated candidate. These trade-offs are shown
without automatically changing the reference strategy.

## Dashboard and verification

The dashboard defaults to **Adaptive rotation · individual tests**, with the
combined implementation selected. Choosing another approach keeps v13 and daily
selection in the growth comparison. It shows monthly returns, drawdown, fills,
average invested exposure, all-arm return/drawdown bars, and a three-cost table.
The earlier study remains selectable. Source checksums and the benchmark partition
are checked before display; partial or corrupted cost evidence is not presented
as a complete comparison.

Regression coverage includes neutral-selector equivalence, future mutation and
prefix invariance, partial/holiday-week exclusion, immediate post-sizing shock
reduction, group/symbol/gross limits, missing context and benchmark refusal, and
configuration/combined-recipe equivalence. A native v14 historical plan was built
without invoking an executor. Browser coverage checks all ten arms, reference
curves, cost and period controls, and 390/1024/1440-pixel layouts.

This study does not establish tick/event-level broker fill parity. It also does
not include a newly expanded tradable ETF universe, independently verified dated
cash carry, prospective observations or development-fitted risk-matched controls.
Those are separate follow-ups to the controlled first-stage design; no result
here silently grants trading authority.

Final checks: 166 targeted engine/research tests passed; 49 frontend API tests
passed (one existing Starlette/httpx deprecation warning); focused mypy checks
passed across four source files; targeted Ruff and whitespace checks passed.
The TypeScript/Vite production build passed. Real-data browser checks passed for
both studies, cost/period/strategy controls, chart inspection, guided tasks and
responsive layouts. The study's 30 native backtests are additional measurements,
not included in the unit-test counts above.
