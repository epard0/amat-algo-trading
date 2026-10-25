**Baseline refinement: the performance objective was not met**

The prior adaptive ML work improved research controls but underperformed the baseline. This follow-up tested eight fixed refinements against the original strategy. None earned promotion. The best candidate under the development selection rule lost to the baseline in later historical validation. The trading settings remain unchanged.

| Period, after 2 bps one-way costs | Baseline annualized return | Selected filter annualized return | Baseline Sharpe | Selected filter Sharpe |
| --- | ---: | ---: | ---: | ---: |
| Development, 2018–2022 | 3.91% | 4.04% | 0.745 | 0.800 |
| Later historical validation, 2023–September 11, 2026 | 7.51% | 5.99% | 1.308 | 1.119 |

The selected filter allowed additions only when 21-session trend efficiency was at least 0.10 and the five-session move was no greater than one trailing volatility unit. It preserved the baseline's trend scores, allocation limits, and exit decisions. Its development gain was only 0.13 percentage points of annualized return. In validation it lost 1.52 percentage points, and maximum drawdown worsened from 4.93% to 6.04%. Average exposure fell from 58.25% to 52.33%; matching exposure still left a Sharpe disadvantage of 0.186.

The calendar folds explain the apparent development improvement. The filter lagged the baseline in 2018, 2019, 2020 and 2021, then gained in 2022. It subsequently lagged in 2023, 2024 and 2026 to date, with an improvement in 2025. This does not establish a reliable regime rule.

**What was tested**

The recipes and selection criteria were saved before running the screen. Only the selected recipe was opened on later history, and its thresholds were not revised afterward. The results below include all eight attempts.

| Development recipe | Annualized return | Sharpe | Exposure-matched Sharpe change |
| --- | ---: | ---: | ---: |
| Original baseline | 3.91% | 0.745 | — |
| Rebalance threshold 3% | 3.73% | 0.709 | −0.042 |
| Rebalance threshold 4% | 3.43% | 0.653 | −0.103 |
| Trend deadband 0.16 | 4.11% | 0.784 | +0.053 |
| Deadband 0.16 and rebalance 3% | 3.85% | 0.722 | −0.017 |
| Entry efficiency filter | 3.62% | 0.712 | −0.010 |
| Entry extension filter | 3.97% | 0.766 | +0.049 |
| Both entry filters, selected | 4.04% | 0.800 | +0.090 |
| Learned entry quality | 3.93% | 0.751 | +0.001 |

Selection maximized exposure-matched Sharpe gain among candidates with higher development return, a matched Sharpe gain of at least 0.05, exposure matching within two percentage points, and no more than one percentage point of drawdown deterioration. None passed the existing trial-adjusted uncertainty guard. Selecting one candidate for historical validation was an investigation, not a promotion.

The ML experiment was a ridge model trained only on past baseline buy opportunities with matured 20-session net-return labels. It used normalized momentum, trend efficiency, relative volume and close location. Fits used trailing data, training-only standardization, a one-session embargo, fixed regularization and six-month refits. The output could block an addition; it did not replace the baseline's portfolio signal. Its 0.001 matched Sharpe gain was negligible. Ridge regularization and keeping preprocessing separate from validation follow the documented methods in [scikit-learn's ridge reference](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html) and [data-leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

The initial diagnostic found 231 short losing trade episodes with approximately $21,936 in realized price losses during development. This is descriptive trade attribution, excludes distributions and open positions, and is not an estimate of avoidable loss. Overlapping forward-return diagnostics suggested weak short-term outcomes after extended moves and in choppy trends. The subsequent portfolio test failed to turn that observation into a persistent improvement.

**Validation and execution result**

| Later-period test | Baseline annualized return | Selected filter annualized return | Matched Sharpe change |
| --- | ---: | ---: | ---: |
| Standard execution, 2 bps | 7.51% | 5.99% | −0.186 |
| Cost stress, 6 bps | 6.85% | 5.54% | −0.163 |
| One additional session of delay, corrected engine | 7.60% | 6.21% | −0.105 |
| Every fifth fill withheld, 2 bps | 7.46% | 6.50% | −0.099 |

The original delayed filter run failed an entry-permission audit: 258 buys could be traced to decisions that blocked additions. Entry limits referenced settled positions while instruction sizing also counted pending orders. A pending sale could therefore produce a replacement buy. Partial fills exposed a second case: an oversized pending sale could create a fictitious negative position during sizing, although settlement would never permit a short.

The correction uses positions plus pending quantities consistently when applying entry permissions, and floors projected long holdings at zero when sizing instructions. Four regression cases cover two- and three-session delays with full and partial fills. The original delayed records and the intermediate correction remain saved as failed evidence; the final corrected records supersede their delayed-performance numbers.

All four final audit runs passed: baseline and candidate, each at standard execution and one extra session of delay. The audit checked entry authorization at the actual decision lag, observed positive-volume execution windows, exact recorded VWAP prices, costs, the 1% buy participation ceiling, nonnegative cash and the accounting identity. Ordinary one-session validation equity, cash and fills matched the original records exactly for both strategies. The corrected candidate still lost to the corrected baseline. No broker orders were submitted.

The full AMAT suite passed: **1,838 tests passed and one expected architecture test was skipped**. The 50 focused checks, changed-file lint, formatting and type checks also passed. These checks establish implementation and accounting behavior; they do not establish a trading advantage.

**Evidence and limits**

All tests retained the same eleven ETFs, 6% volatility target, position and cluster caps, 2 bps base costs, 6 bps cost stress, zero cash yield and observed execution windows. The study uses the verified September 14 snapshot through September 11, 2026. Earlier research has already used both historical periods; this is not a pristine holdout or evidence of future profitability. The adjusted 50-trial block-bootstrap guard is a conservative research screen, not a calibrated significance claim that erases previous search. Historical fill simulation also does not validate broker fills.

The complete records are in [the study protocol](/home/supasuge/Documents/amat-workspace/amat/outputs/baseline-refinement-2026-09-14/protocol.json), [all development results](/home/supasuge/Documents/amat-workspace/amat/outputs/baseline-refinement-2026-09-14/development.json), [later validation results](/home/supasuge/Documents/amat-workspace/amat/outputs/baseline-refinement-2026-09-14/validation.json), [annual folds](/home/supasuge/Documents/amat-workspace/amat/outputs/baseline-refinement-2026-09-14/annual-folds.json), [the corrected comparison](/home/supasuge/Documents/amat-workspace/amat/outputs/baseline-refinement-2026-09-14/delay-correction-final/comparison.json), [the final execution audit](/home/supasuge/Documents/amat-workspace/amat/outputs/baseline-refinement-2026-09-14/delay-correction-final/execution-audit.json) and [the full test log](/home/supasuge/Documents/amat-workspace/amat/outputs/baseline-refinement-2026-09-14/full-tests-final.log). Each recorded run includes its daily fills, missed fills, cash, equity and cost attribution.

The code changes are in [pending-position handling](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:468), [instruction sizing](/home/supasuge/Documents/amat-workspace/amat/src/amat/execution/__init__.py:108) and [the regression test](/home/supasuge/Documents/amat-workspace/amat/tests/unit/test_delayed_entry_permissions.py:17). The research engine label advances to `v1-frozen+research-signals/7`. Frozen prior runs retain their original code. The strategy configuration was not promoted or changed.
