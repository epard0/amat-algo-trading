# Performance review: a 10% annualized research target

The new **v13 research configuration achieved 13.34% compound annual growth after
modeled costs over January 2, 2018–September 11, 2026**, compared with **5.52%**
for the original v6 baseline. The selected recipe also exceeded 10% separately
in development and later historical validation, including the 6-basis-point
cost stress. This meets the registered **historical** target.

The improvement carries substantially more market risk. Maximum drawdown rose
from 7.94% to 15.01%, and the candidate lost 9.87% on its worst day. It does not
establish a future 10% return, a 10% return every year, or superior risk-adjusted
performance. The configuration is an experimental research profile; it has not
earned automatic deployment through the original research promotion gates.

## Results on the same data and execution assumptions

The primary runs use the same eleven ETFs, verified data, corporate actions,
next-session 09:30–09:35 raw VWAP execution, 2 bps cost per side, zero cash yield,
a 1% buy participation ceiling against observed window volume, and no leverage.
Unfunded purchases and participation-limited quantities are recorded as misses.

| Measurement | Original v6 | Selected v13 |
|---|---:|---:|
| Development CAGR, 2018–2022 | 4.07% | **13.30%** |
| Development CAGR, 6 bps per side | 3.53% | **12.56%** |
| Later historical CAGR, 2023–September 11, 2026 | 7.51% | **13.40%** |
| Later historical CAGR, 6 bps per side | 6.85% | **12.58%** |
| Full-period CAGR, 2 bps | 5.52% | **13.34%** |
| Full-period CAGR, 6 bps | 4.93% | **12.57%** |
| Full-period annualized volatility | 5.46% | 13.38% |
| Full-period maximum drawdown | −7.94% | −15.01% |
| Full-period mean invested capital | 53.77% | 86.36% |
| Full-period conventional Sharpe, zero cash rate | 1.010 | 1.004 |
| Full-period simulated fills | 2,920 | 1,165 |
| Full-period one-way turnover / year | 14.01× | 13.49× |

[Equity and drawdown chart](../../amat/outputs/return-target-2026-09-14/selection-v3/performance.png)
and [the plotted daily data](../../amat/outputs/return-target-2026-09-14/selection-v3/chart-data.csv).

The prior refinement report's development baseline was 3.91%. Its slicing
function used January 2, 2018 as the valuation anchor and therefore excluded
that day's return. This study uses the preceding December 29 close and includes
January 2. The 1,259 overlapping daily equity observations match exactly. The
difference is a measurement boundary, not a strategy improvement. CAGR uses
252 trading-session returns per year; partial 2026 calendar returns remain
labeled year-to-date.

## Why the project was underperforming its new return objective

### 1. The strategy mandate and the requested return target differed

V6 deliberately targets only 6% annualized volatility. Position caps then
reduce exposure further, and residual capital earns the explicitly assumed 0%.
During development it invested only 50.45% on average. During the later period
it invested 58.25%. A 10% absolute-return goal asks something materially
different from this conservative configuration.

The original automated research system also protects the risk target, universe,
and sizing caps from proposer edits. Its objective ranks **exposure-matched
Sharpe improvement**, with an additional complexity penalty. That is a useful
way to screen for signal information. It gives the search no direct route to
change the portfolio's return/risk mandate. More proposals within those bounds
cannot be expected to solve a mandate mismatch.

### 2. Several allocation restrictions interact

The original signal divides momentum by volatility; the allocator divides the
result by volatility again. This can favor low-volatility holdings. Symbol and
cluster caps then clip those allocations, with no redistribution to recover the
target. This behavior is intentional in the original contract, but it can leave
capital idle rather than committed to stronger trends.

Neither a higher risk target nor removing the second volatility adjustment was
a sufficient fix on its own. At the original caps, increasing the target from
6% to 12% raised development CAGR only from 4.07% to 5.06%. Widening caps reached
5.88%. Removing the second adjustment at the original risk target actually
reduced CAGR to 3.64%. The successful combination also changed trend horizons
and selection behavior.

### 3. Fast signals and frequent ranking changes consume the return

Replacing the 21-session horizon with 126 sessions improved the development
comparison. Selecting a few positive, confirmed trends improved it further.
Daily changes in relative ranks nevertheless created replacements and costs.
The new schedule holds the selected membership for five trading sessions while
still applying daily absolute eligibility, signal exits, risk sizing and caps.

This is a limited result: monthly refreshes performed worse in development,
and the daily-ranking ablation outperformed the selected weekly version in
later history at 6 bps: 15.08% versus 12.58%. Weekly ranking therefore has not
established an independent, persistent timing advantage. It was selected using
development and was not replaced after reading the later result.

### 4. Adding filters and models was not a demonstrated solution

Earlier documented ML and entry-filter experiments repeatedly reduced
participation, missed winners or failed incremental-value checks. Their
engineering controls are useful, but model capacity and the volume of research
activity are not evidence of higher portfolio returns. This iteration adds two
small allocation controls and tests their economic effects directly.

### 5. Costs and execution capacity still matter

The candidate makes fewer, larger changes. Its annual notional turnover falls
only modestly, despite the large reduction in fill count. Costs must therefore
be modeled against traded notional, not the number of trades. Its measured
full-period missed-fill count is 557 versus 148 for v6; greater target exposure
causes more cash and participation constraints to bind.

The cost ladder is a sensitivity test. Constant bps and historical window VWAP
do not reproduce spread variation, market impact, order queues, or a broker's
realized execution. The participation ceiling protects simulated buys; it is
not evidence that a broker implements the same execution policy.

### 6. The stored paper evidence cannot measure annual performance

The available August 17 paper report contains one observation, a fully cash
account and dry-run orders. That is insufficient to diagnose realized trading
returns or confirm that the historical simulation translates into fills.
The executor was checked offline, including its existing fake-broker tests.

## What changed

[The selected configuration](../../amat/configs/strategy/amat_v13_return_target.toml)
is a new, explicit strategy profile with the following differences:

| Setting | v6 | v13 candidate |
|---|---|---|
| Trend horizons, sessions | 21, 63, 252 | 63, 126, 252 |
| Second volatility divisor in allocation | Power 1 | Power 0 |
| Cross-sectional selection | Disabled | Up to four assets; two positive horizons |
| Relative-ranking refresh | Daily | Every five trading sessions |
| Volatility target before caps | 6% | 20% |
| Symbol target-weight cap | 20% | 45% |
| US equity cluster target cap | 40% | 85% |
| International / metals / commodities target caps | 25% / 20% / 20% | 45% / 45% / 35% |
| Gross target exposure | At most 100% | At most 100% |

The forecast target is not the realized volatility or a guaranteed loss limit.
Market moves and execution gaps can also move settled weights away from their
targets. Selection uses the same complete history origin in backtests and plan
formation so its five-session schedule remains aligned.

Source changes:

- [Validated optional configuration](../../amat/src/amat/config/models.py): default
  values preserve the old calculation and are omitted from old canonical
  serialization, preserving existing candidate identities.
- [Allocation](../../amat/src/amat/portfolio/__init__.py): controls the second volatility
  adjustment while retaining the same targeting and cap order.
- [Scheduled selection](../../amat/src/amat/portfolio/cross_section.py): retains relative
  membership between refreshes and checks absolute eligibility daily.
- [Return-target study](../../amat/scripts/return_target_study.py): freezes recipes,
  source and data identities, development selection and later validation.
- [Controls](../../amat/scripts/return_target_controls.py) and
  [audit](../../amat/scripts/audit_return_target.py): preserve parameter sensitivity,
  exposure comparisons, independent position reconstruction and uncertainty.
- [Operator shortcut](../../amat/scripts/research_cli.py): `./research returns --help`.

## Search history and validation

Twenty-five distinct candidates were tested in three adaptive development
rounds: thirteen initial allocation experiments, six additional risk/cap
variants, then six selection-schedule variants. Earlier rounds were not opened
on later history. All their outcomes remain saved, including failures. The final
selection maximized 6-bps development CAGR subject to better primary return than
baseline, maximum drawdown below 25%, and at least 60% positive development
years. These are declared research constraints, not personal risk preferences
or guarantees.

Only the final selected recipe was validated, alongside baseline. It cleared
the registered historical target of at least 10% CAGR in both periods at 2 and
6 bps, with positive stressed returns and drawdown within the research ceiling.

| Later-period execution test | v6 CAGR | v13 CAGR | v13 maximum drawdown |
|---|---:|---:|---:|
| 2 bps per side | 7.51% | 13.40% | −15.01% |
| 6 bps per side | 6.85% | 12.58% | −15.02% |
| 10 bps per side | 6.21% | 11.67% | −15.03% |
| One extra session of delay | 7.60% | 14.05% | −13.14% |
| Every fifth fill withheld | 7.46% | 13.48% | −14.24% |

Six additional controls were prescribed for the selected recipe. The four
neighbors—16%/24% targets and four-/six-session ranking intervals—returned
11.69%–13.38% in later validation at 6 bps, with median CAGR 12.43%. All retained
positive return and drawdown within 25%. The two remaining controls remove
weekly ranking and restore inverse-volatility capital weighting. None was used
to reselect the candidate.

### More return does not imply better risk-adjusted performance

Retrospective exposure matching reduces the candidate and reruns its trades at
2 bps; it does not multiply returns after the fact. Development matched returns
were 8.23% versus 4.07%, with a +0.273 change in the repository's Sharpe measure.
Later matched returns were 8.69% versus 7.51%, but Sharpe changed by **−0.305**.
Mean exposure matched within 0.91 percentage points in development and 0.02
percentage points later. The scalars are period-specific diagnostics, not
deployable decisions.

The full-period conventional Sharpe is approximately unchanged. The original
research system's requirement for improved exposure-matched Sharpe would reject
this candidate's later result. Meeting the separate absolute-return mandate
does not override that failure or constitute original-policy promotion.

## Benchmarks, bad periods and uncertainty

| Full-period comparison | CAGR | Annualized volatility | Maximum drawdown |
|---|---:|---:|---:|
| Original v6, after costs | 5.52% | 5.46% | −7.94% |
| Candidate v13, after costs | 13.34% | 13.38% | −15.01% |
| SPY total-return benchmark, frictionless | 14.55% | 18.76% | −33.79% |
| Equal-weight eleven-asset buy-and-hold, frictionless | 11.03% | 13.97% | −24.75% |

SPY also returned 22.22% annually in the later period, versus 13.40% for v13.
The equal-weight basket returned 19.69% with a 12.24% drawdown in that period.
These comparisons make the cost of active decisions visible. Benchmark adjusted
prices include reinvested distributions and omit trading costs; the strategies
receive distribution cash and pay modeled trading costs.

| Calendar period | Candidate total return |
|---|---:|
| 2018 | 2.43% |
| 2019 | 11.12% |
| 2020 | 22.25% |
| 2021 | 28.04% |
| 2022 | 4.76% |
| 2023 | 1.40% |
| 2024 | 10.08% |
| 2025 | 26.84% |
| 2026 through September 11, not annualized | 12.12% |

Only **51.3% of overlapping rolling one-year windows** exceeded 10%. The worst
rolling year lost 6.92%. The worst day lost **9.87% on January 30, 2026**; it
remains in every result. Contemporaneous reporting records the sharp gold and
silver decline on that date, supporting the plausibility of this market event
without independently verifying every vendor bar.
[AP's January 30 market report](https://apnews.com/article/42217b719cb3077b79648a05a75f5abf).

A 2,000-resample, 60-session block bootstrap gives a **6.19%–21.15%** full-period
90% CAGR interval. The corresponding later-period interval is **3.53%–26.66%**.
These are conditional historical diagnostics; they do not account completely
for adaptive selection, future regime changes or market-data errors. Their lower
bounds are below 10%.

This history was used by prior project research, and the inherited eleven-asset
universe already excludes TLT after an earlier performance review. Chronological
validation here is therefore **not a pristine holdout**. Repeated backtesting
can select apparent advantages that do not persist.
[Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
Backtested returns are hypothetical performance.
[SEC investor bulletin on performance claims](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-47).

## Engineering and execution verification

- **1,885 AMAT tests passed**, with one expected architecture-test skip.
- **Seven additional audit regression cases passed** after the full suite was
  collected; they prevent an expected terminal-decision omission from masking
  changes in cash, equity, fills or earlier decisions.
- **160 executor tests passed**, including the existing real offline v6-plan
  and fake-broker integration cases. The first attempt encountered a read-only
  uv cache; rerunning with a writable cache and the existing offline environment
  passed without changing executor code.
- Strict type checking passed for all **158 AMAT source files**. Changed-file
  lint and formatting passed.
- **35 recorded simulations passed independent cash/position reconciliation,
  fee, observed execution-price and buy-participation checks**. Maximum
  reconciliation residual was below $0.000001.
- Development records remain identical after loading later data. The runner
  intentionally omits its final allocation diagnostics when their orders would
  fall beyond the run's end; financial records, including the final record,
  remain exact. The initial audit and the corrected audit are both retained.
- New allocation and schedule tests verify visible-prefix equivalence, future
  mutation invariance, immediate exits, cap enforcement and default compatibility.
- Four historical checks verified that offline plan targets match the backtest
  decision function for the new configuration, allowing the plan schema's
  intentional omission of zero weights.
- The saved source/data replay reproduced the validation results. Broker
  submission was not part of this work.

## Reproduce or inspect

From the workspace root, `./research returns --help` explains the study phases.
The [commands cheatsheet](../COMMANDS_CHEATSHEET.md#annual-return-target-study)
shows how to register, screen and validate a fresh output directory. The existing
study also carries its own source, data, configuration and dependency record:

```bash
amat/outputs/return-target-2026-09-14/selection-v3/replay.sh validate
```

Evidence:

- [All 25 new candidates in one CSV](../../amat/outputs/return-target-2026-09-14/all-candidates.csv).
- [Initial allocation results](../../amat/outputs/return-target-2026-09-14/allocation-v1/development.json)
  and [second allocation round](../../amat/outputs/return-target-2026-09-14/allocation-v2/development.json).
- [Final protocol](../../amat/outputs/return-target-2026-09-14/selection-v3/protocol.json),
  [all final-round development results](../../amat/outputs/return-target-2026-09-14/selection-v3/development.json),
  [selection](../../amat/outputs/return-target-2026-09-14/selection-v3/selection.json),
  [later validation](../../amat/outputs/return-target-2026-09-14/selection-v3/validation.json),
  and [target decision](../../amat/outputs/return-target-2026-09-14/selection-v3/decision.json).
- [Sensitivity controls](../../amat/outputs/return-target-2026-09-14/selection-v3/controls.json),
  [exposure matches](../../amat/outputs/return-target-2026-09-14/selection-v3/exposure-matches.json),
  [audit](../../amat/outputs/return-target-2026-09-14/selection-v3/execution-audit.json),
  and [benchmarks/uncertainty](../../amat/outputs/return-target-2026-09-14/selection-v3/diagnostics.json).
- [Fresh replay from frozen source and data](../../amat/outputs/return-target-2026-09-14/selection-v3/fresh-frozen-replay.json):
  every field of all 2,437 records matched for both primary runs.

The practical result is a reproducible portfolio profile meeting the historical
return target, plus evidence showing why the prior conservative profile did not.
The next evidence needed for a deployment decision is independent observation of
this frozen policy and its execution, with a risk budget that
accepts the larger
losses. Further tuning on the same dates cannot create that evidence.
