# Daily group selection: long history, recovery and ML exits

This fixed autoresearch experiment completed 27 native-engine trials: nine approaches at 2, 6 and 10 basis points per side. All runs have zero engine warnings. No strategy, executor mode or active account configuration was promoted.

## Main finding

Daily groups improves the 2020-onward return relative to v13, but its full-history advantage is small and disappears under higher costs. Five-session group selection is competitive with less drawdown and lower cost sensitivity. The tested recovery rule buys protection at a substantial return cost. The first separate ML exit indicator does not justify adoption.

## Data and experiment contract

- Source: immutable `alpaca-sip-core-2026-10-13-verified`, 2,709 sessions from January 4, 2016 through October 12, 2026. The eleven core ETFs have complete observed opening windows; no synthetic window prices were used.
- All accounts begin with $100,000 cash on January 3, 2017. The 2016 history supplies signal warmup. Corporate actions, observed 09:30–09:35 VWAP, next-session execution and a 1% opening-window volume participation limit use the native engine.
- Costs of 2/6/10 bps per side are combined spread, commission and impact assumptions booked as transaction costs. They are sensitivity cases, not calibrated historical bid/ask estimates. Gaps from decision close to actual opening-window VWAP are included separately through the fill price. Unfilled instructions remain visible.
- The common walk-forward comparison is January 2020–October 12, 2026, with existing holdings carried from 2017. Curves normalize to the December 31, 2019 close. There are no annual account resets or spliced recent snapshots.
- Daily selection refreshes eligible asset membership each session; group-strength rankings still use prior completed weeks, following the saved strategy.
- Fixed core strategies are evaluated year by year. The ML classifier alone is refit before each test year, using expanding earlier data with ten-session label purging. All dates have previous research exposure: chronological model testing does not make this an untouched strategy holdout.
- Cash earns zero, sizing uses $100,000 initial capital, current ETF selection is survivor-conditioned, and no historical broker acknowledgment/limit-order parity is claimed. Results do not cover 2008 or delisted assets.
- Protocol SHA-256: `1d577aca25260cf96406d2669b3e65a6683003aeac601c25da6afe8bbc0646cb`. Frozen source, all three actual partition hashes, protocol identity and all 27 canonical run seals were rechecked after execution. Dependency versions are recorded in `runtime-audit.json`.

## Return and drawdown at 2 bps per side

| Approach | 2017–2026 net | Full max decline | 2020–2026 net | 2020–2026 max decline |
|---|---:|---:|---:|---:|
| Daily selection | 247.72% | 13.40% | 171.95% | 13.40% |
| v13 reference | 255.95% | 15.01% | 158.58% | 15.01% |
| Daily groups | 262.75% | 15.18% | 197.87% | 15.18% |
| Groups every 5 days | 254.96% | 13.06% | 192.02% | 13.06% |
| Daily groups + ML exit | 218.96% | 15.21% | 161.91% | 15.21% |
| ML exit + staged recovery | 206.49% | 15.19% | 151.67% | 15.19% |
| Daily groups + trend quality | 235.28% | 14.95% | 175.64% | 14.95% |
| Simple exit + staged recovery | 178.96% | 13.59% | 141.54% | 13.59% |
| Daily groups + simple exit | 204.57% | 13.69% | 161.34% | 13.69% |

## Cost sensitivity: full 2017–2026 return

| Approach | 2 bps/side | 6 bps/side | 10 bps/side |
|---|---:|---:|---:|
| Daily selection | 247.72% | 216.18% | 182.84% |
| v13 reference | 255.95% | 233.56% | 213.29% |
| Daily groups | 262.75% | 220.32% | 187.59% |
| Groups every 5 days | 254.96% | 220.17% | 200.70% |
| Daily groups + ML exit | 218.96% | 187.16% | 150.37% |
| ML exit + staged recovery | 206.49% | 177.62% | 146.59% |
| Daily groups + trend quality | 235.28% | 197.65% | 166.93% |
| Simple exit + staged recovery | 178.96% | 152.36% | 132.22% |
| Daily groups + simple exit | 204.57% | 164.29% | 130.90% |

The daily-group return falls from +262.75% to +187.59% between the low and high cost cases. v13 falls from +255.95% to +213.29%. This makes turnover reduction a higher-priority next experiment than adding another generic market gate. The dollar cost sums are account-path dependent; the return gap also includes compounding and subsequent sizing changes.

## Named stressed markets at 2 bps

Each cell is net return / maximum drawdown measured from the high inside that window. ML is inactive before 2020, so the 2018 ML result is the ungated daily-group result.

| Approach | 2018 Q4 | Feb 19–Apr 30, 2020 | Calendar 2022 |
|---|---:|---:|---:|
| v13 reference | -7.09% / 9.22% | -8.95% / 12.25% | 4.76% / 7.93% |
| Groups every 5 days | -4.71% / 5.79% | -3.92% / 8.57% | 12.38% / 7.54% |
| Daily groups | -4.84% / 5.95% | -3.40% / 8.06% | 9.24% / 9.82% |
| Daily groups + simple exit | -4.81% / 5.30% | 0.77% / 3.42% | 7.12% / 3.95% |
| Simple exit + staged recovery | -3.50% / 4.00% | -0.98% / 2.86% | 4.05% / 3.21% |
| ML exit + staged recovery | -4.84% / 5.95% | -3.32% / 8.04% | 9.29% / 9.80% |

## Annual and monthly behavior

| Year | Daily groups | Simple exit + staged recovery | ML exit + staged recovery |
|---|---:|---:|---:|
| 2017 | 7.07% | 7.07% | 7.07% |
| 2018 | 1.64% | 0.33% | 1.64% |
| 2019 | 11.90% | 7.51% | 11.90% |
| 2020 | 21.81% | 24.97% | 12.69% |
| 2021 | 20.05% | 18.29% | 9.65% |
| 2022 | 9.24% | 4.05% | 9.29% |
| 2023 | -5.50% | -3.55% | -5.53% |
| 2024 | 16.12% | 15.81% | 16.13% |
| 2025 | 34.96% | 27.23% | 34.79% |
| 2026 (partial) | 25.90% | 10.50% | 26.02% |

| Weakest daily-group month | Daily groups | Simple exit + staged recovery | ML exit + staged recovery |
|---|---:|---:|---:|
| 2020-09 | -6.57% | -2.94% | -1.03% |
| 2023-02 | -5.21% | -4.14% | -5.21% |
| 2018-10 | -5.18% | -3.59% | -5.18% |
| 2020-02 | -4.53% | -0.17% | -4.02% |
| 2019-05 | -4.22% | -3.66% | -4.22% |
| 2021-11 | -4.12% | -4.04% | -4.21% |
| 2023-05 | -3.41% | -3.41% | -3.41% |
| 2026-03 | -3.13% | -4.15% | -3.01% |
| 2024-07 | -3.11% | -0.91% | -3.11% |
| 2019-09 | -2.51% | -2.71% | -2.51% |

September 2020 was the worst month (-6.57%). In that month QQQ fell 5.64%, GLD 4.17% and SLV 17.50%; IEF rose only 0.33%. These are market-context returns, not measured holding attribution. Simple staged recovery reduced that month to -2.94%; ML staged recovery reduced it to -1.03%, but both lost substantial return across the whole evaluation. A favorable single month is insufficient evidence for adopting either rule.

The largest daily-group drawdown was 15.18%, from the June 8, 2022 high to the January 4, 2024 low. It recovered on May 17, 2024 after 487 sessions below the high. Calendar 2023 returned -5.50%. Prolonged weak rotation and whipsaw deserve separate evaluation from fast crash protection.

## Tested recovery quota

A risk signal cuts the normal target exposure to 25%; it does not cap actual holdings instantaneously. The simple signal is QQQ losing more than 4% over five sessions, or being below its 200-close average while its 20-session return is negative. The policy waits five healthy sessions after the last risk signal, then requires three additional healthy closes per step from 25% to 50%, 75% and 100%. A new signal resets it. All changes execute through the native lag, turnover threshold, constraints and fill model. No borrowing, doubling or profit-to-recover target is introduced.

This fixed recovery policy improved stress-window declines, but full-history return fell from +262.75% to +178.96%, while full drawdown only fell from 15.18% to 13.59%. It is a research candidate, not the recommended default.

## Separate ML indicator

The indicator is a standardized, regularized logistic regression (C=0.1, deterministic seed 0), predicting whether the ungated daily-group 2 bps account will lose at least 3% from the current close at any of the next ten closes. It is a portfolio risk indicator, not an individual-asset exit model. Nine close-time features cover QQQ short returns/trend/volatility, 63-session universe breadth, cross-sectional return dispersion, and the strategy’s own trailing returns/drawdown. Its shadow ungated NAV is causal and continues independently of the overlay.

The scaler and classifier fit only training rows. Every training label matures before the first test close; the ten most recent training starts are purged. Predictions before 2020 are absent and no ML gate is applied. A probability of at least 35% reduces the next executable target to 25%; the staged variant uses the same recovery policy. Parameters were fixed before this run, with no threshold search on these outcomes. Plain JSON artifacts retain each yearly model’s scaler, coefficients and intercept.

| Test year | Last mature training outcome | Test start | AUC | Brier error | Constant-risk Brier | False positives / false negatives |
|---|---|---|---:|---:|---:|---:|
| 2020 | 2019-12-31 | 2020-01-02 | 0.634 | 0.198 | 0.170 | 52 / 30 |
| 2021 | 2020-12-31 | 2021-01-04 | 0.628 | 0.100 | 0.090 | 17 / 23 |
| 2022 | 2021-12-31 | 2022-01-03 | 0.490 | 0.067 | 0.064 | 0 / 17 |
| 2023 | 2022-12-30 | 2023-01-03 | 0.477 | 0.095 | 0.090 | 0 / 25 |
| 2024 | 2023-12-29 | 2024-01-02 | 0.564 | 0.120 | 0.119 | 0 / 34 |
| 2025 | 2024-12-31 | 2025-01-02 | 0.517 | 0.064 | 0.057 | 0 / 15 |
| 2026 | 2025-12-31 | 2026-01-02 | 0.395 | 0.119 | 0.110 | 0 / 23 |

Brier error is worse than the training-base-rate estimate in all seven years. The threshold produces no exits during 2022–2026; it fails to catch any labeled adverse event in those years. AUC is below 0.5 in 2022, 2023 and 2026. The staged ML overlay returns +151.67% over 2020 onward versus +197.87% ungated, with drawdown 15.19% versus 15.18%. Overlapping ten-session labels are not independent observations; these scores should not be interpreted as seven statistically independent proofs.

The potential remains worth evaluating through different labels and more relevant features, but the present indicator is not reliable enough to drive execution. The methodology follows chronological validation and training-only preprocessing as described in the [scikit-learn cross-validation guide](https://scikit-learn.org/stable/modules/cross_validation.html#time-series-split); model details follow [LogisticRegression](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html).

## Prioritized next improvements: hypotheses, not established gains

1. **Daily ranking with cost-aware trading.** Preserve daily group scans but trade only when estimated improvement exceeds spread/impact plus a turnover buffer. Compare against fixed five-session groups; measure switches, net return and missed opportunities at every cost level.
2. **Protect the selected group, not every group through QQQ.** Equity weakness need not imply commodity, dollar or bond weakness. Test group-specific trailing weakness and within-group breadth before reducing the entire portfolio. Keep caps and no-leverage constraints.
3. **Handle prolonged weak leadership separately from crashes.** Test a predeclared deterioration score combining shrinking eligible breadth, falling leading-group momentum and repeated leadership changes. Evaluate 2022–2024 recovery duration as well as named crashes.
4. **Evidence-based recovery speed.** Test a small preregistered comparison of the fixed quota with a faster return when selected-group breadth and momentum recover. Evaluate the cost of staying underinvested and repeated re-entry, rather than forcing the account to earn back a monetary loss quota.
5. **Make an ML exit label match the actual decision.** Compare the forward benefit of reducing a specific group or holding versus staying invested, net of exit and re-entry costs, rather than predicting any short loss. Add group features; calibrate probabilities and choose thresholds only inside nested chronological training splits. Require incremental return/risk benefit against simple rules and a constant estimate on a newly reserved period.
6. **Improve execution evidence.** Calibrate opening spreads and impact using observed quotes/order events, stress delayed or missing fills, and evaluate additional account sizes. Include an explicit cash-yield convention before drawing final conclusions about cash-heavy policies. Keep simulated and broker-event parity claims separate.
7. **Reserve genuinely new evidence.** Freeze the next small set of policies, record all rejected trials, and use future shadow/paper observation before promotion. More tuning on the same 2017–2026 history would weaken, not strengthen, the evidence.

## Dashboard and reproduction

The default analysis is now “Daily groups · long history & exit tests” and the 2020-onward common walk-forward period. It includes growth/drawdown curves, nine approaches, three cost cases, fixed stress windows, annual folds, every observed month, exact month details, ETF market context, exposure quotas, classifier diagnostics and recovery episodes. ML diagnostic and recovery charts are explicitly labeled as full 2020-onward views, independent of the main chart period.

```sh
PYTHONPATH=amat/src amat/.venv/bin/python amat/scripts/run_validation_study.py \
  --output amat/outputs/analysis/NEW-IMMUTABLE-STUDY
```

The runner freezes source/configuration before registration and refuses an existing output directory. Raw rows and all cost cases are retained under the study directory. New unit tests cover future-mutation and prefix invariance, label maturity, yearly purging, recovery boundaries, monthly accounting and open recovery episodes. API tests reject mismatched diagnostics and preserve the earlier comparisons. Browser checks use the actual saved evidence without broker actions.

## Candidate removal after the user's performance requirement

The active incumbent is **Groups + daily · core ETFs**. Six lower-return arms
were rejected because each returned less in both the full-history and common
2020-onward comparisons at the registered primary cost of 2 bps per side:
five-session groups, daily groups plus quality, simple exit, simple staged
recovery, ML exit and ML staged recovery. v13 and plain daily selection remain
explicit reference controls. Five-session groups' better results in some higher
cost cases remain an archived sensitivity finding.

The current dashboard strategy and cost tables show only the incumbent and
those two controls. Recovery/ML evidence is collapsed under “Rejected
experiments · recovery and ML audit”; earlier studies are labeled archived.
The selectable `amat_group_selection_daily_quality.toml` and
`amat_v14_adaptive_rotation.toml` profiles were deleted. Their original versions
remain in the immutable frozen studies for reproducibility. No native strategy
engine primitives or live-account settings were deleted or promoted.

Future ordinary daily autoresearch sessions now start from the daily-group core
profile, and the native research envelope supports this v14 profile without
importing v6 learned overlays. A candidate must match or exceed the incumbent's
matched development net return at both primary (2 bps) and stress (6 bps) costs,
in addition to the existing eligibility, improvement and uncertainty gates.
Missing or nonfinite return measurements fail this gate. Validation data is
excluded from the keep/discard decision. Rejected measurements stay in the audit
ledger but cannot become the retained incumbent through this gate.

The decisions and evidence references are recorded in
`configs/experiments/candidate-decisions-2026-10-19.json`.


The bounded end-to-end check in
`~/.local/share/amat/web-research/bc9d49962820/daily-group-return-floor-smoke-20261019`
completed one built-in candidate plus the daily-group baseline, with no model API
calls and execution authority `none`. On that job's matched development window,
the candidate returned 138.57% versus 168.04% at 2 bps and 115.64% versus 142.72%
at 6 bps. Both return-floor cases failed and the recorded decision was `discard`.
These smoke-test development returns cover that program's own measurement
window; they must not be substituted for the 2017–2026 or 2020-onward study
comparisons above. Explicit research promotion also rejects a recorded failed
return floor.
