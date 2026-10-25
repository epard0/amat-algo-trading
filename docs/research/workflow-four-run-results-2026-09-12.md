# Four strategy-research runs: results and workflow analysis

Completed 2026-09-12. Four sequential sessions, one measured candidate each, against the same operator-selected **E73 — Trend agreement × channel position** baseline. Three candidates came from live models; one used the normal built-in fallback. All four completed and were discarded. No strategy was promoted.

## Study design

- Measured sample: **2018-01-02 through 2022-12-30**, 1,259 recorded sessions. Same frozen data, evaluator, baseline, warm-up and historical robustness settings across all runs.
- Primary costs: **2 basis points one way**; stress costs: **6 basis points one way**. The score is exposure-matched Sharpe difference less incremental complexity. It must exceed the baseline by 0.05 and satisfy the other retention checks.
- One requested candidate per session, a maximum of three attempted experiments per session, and the existing 300,000-token accounting budget per session. No existing budget was reset.
- Gemini → OpenRouter free routing → NVIDIA, with one local corrective call available across the chain. Normal fallback enabled. Baseline and candidate summary reviews were attempted.
- This is adaptive development on the same historical period. Four runs do not supply four independent holdout tests. No checkpoint or broker operation was run.
- Total elapsed time: **16.31 minutes**. Baseline identity and its complete primary metric record were identical in all four sessions.

## Measured results

All returns below are annualized, net of the primary cost assumption. Drawdown and exposure are percentages. The score uses a retrospectively exposure-matched baseline, so it is not simply the difference between the unscaled Sharpe values in this table. Absolute ending account values are not compared because the strategies can enter the measured window with different warm-up balances.

| Run | Actual executed recipe | Source | Annual return | Sharpe | Max drawdown | Gross exposure | Incremental score |
|---|---|---|---:|---:|---:|---:|---:|
| Baseline | Selected E73 | Fixed | 3.81% | 0.687 | -7.33% | 60.14% | 0.0000 |
| 1 | E73 gate interaction + two volatility overlays | llm | 0.19% | 0.525 | -0.53% | 3.62% | -0.0293 |
| 2 | Original strategy (custom signal omitted) | llm | 4.01% | 0.765 | -7.89% | 50.84% | +0.0854 |
| 3 | E73 signed-minimum interaction | llm | 3.71% | 0.681 | -7.22% | 59.21% | +0.0072 |
| 4 | Original strategy + 21-session momentum overlay | built-in | 3.72% | 0.746 | -7.52% | 48.80% | +0.0699 |

![Score and exposure comparison](/home/supasuge/Documents/amat-research/workflow-four-results-20260912-143700/comparison.png)

| Run | Why it was discarded | Stressed Sharpe |
|---|---|---:|
| 1 | Exposure, no incremental exposure-matched information, and nearby parameter instability checks failed. | 0.495 |
| 2 | Score cleared 0.05, but the 1,000-resample bootstrap guard failed; lower Sharpe-difference bound **−0.2741**. | 0.660 |
| 3 | Score **+0.0072** did not clear the 0.05 improvement margin; uncertainty testing was skipped at that gate. | 0.628 |
| 4 | Score cleared 0.05, but the bootstrap guard failed; lower Sharpe-difference bound **−0.3021**. | 0.630 |

The uncertainty bounds come from the existing trial-budget-adjusted research guard, not a calibrated p-value or a newly claimed confidence level. E73’s stressed Sharpe was **0.640**. The original strategy in Run 2 had the strongest point estimate here, but neither that result nor the Run 4 filter supplied sufficient uncertainty evidence for automatic retention.

## What worked

1. **Live model proposal execution now has evidence.** NVIDIA supplied the accepted candidates in Runs 1 and 3; Gemini supplied Run 2. This supersedes the earlier verification’s unresolved live-proposal acceptance check at the structural/execution level. Economic-mechanism quality remains a separate issue.
2. **Cross-session memory was actually supplied.** Eligible and supplied record counts were 0, 1, 2 and 3. Run 3 explicitly referred to an earlier failed attempt.
3. **Exact-repeat protection prevented a redundant backtest.** In Run 4 NVIDIA reproduced Run 3’s exact candidate, despite changing the hypothesis wording and stage from refine to combine. Candidate identity `6c467a9b86ba0d1afde17c238da22f24519e933006b4cebac1ee8bda9100f50d` matched prior memory, so local validation rejected it as `proposal_duplicate`. A different built-in recipe was measured instead.
4. **Deterministic gates remained authoritative.** Low drawdown alone did not rescue Run 1’s near-cash exposure. Positive scores in Runs 2 and 4 did not override failed uncertainty checks.
5. **Evidence and visual outputs reconciled.** All recorded measurement artifact hashes were rechecked during aggregation. Each run has verified equity, drawdown and exposure charts with adjacent numeric data.

## Main weakness: prose and executable recipes disagree

| Run | Model claim | Actual behavior and implication |
|---|---|---|
| 1 | Volatility-of-volatility gating of E73. | No volatility-of-volatility feature was encoded. The recipe changed product to gate and added ordinary 21/63-session volatility overlays, each with a below-zero threshold and 75% attenuation. At positive volatility, each applies a 0.25 multiplier; together they apply 0.0625. Observed exposure fell to 3.62%. This does not falsify the claimed volatility-of-volatility mechanism. |
| 2 | Replace the product interaction with minimum agreement. | The raw model response contained only hypothesis, parent ID and stage. The omitted signal defaulted to null, clearing E73’s custom signal and reverting to the original strategy. Novelty validation accepted the changed candidate. This is an explicit completeness/intent gap in the workflow, not evidence that a minimum interaction produced the result. |
| 3 | A positive-only AND rule, `min(max(a,0),max(b,0))`. | The registered operator is signed `minimum(a,b)` on transformed inputs. Negative values can contribute; the positive-only clipping is absent. Its model-written complexity penalty also differed from the recorded penalty. The measured result applies to the compiled operator. |
| 4 | Repeat the minimum-agreement idea under another stage label. | The recipe was identical to Run 3 and was rejected. The measured built-in replacement uses the original strategy plus momentum attenuation; it does not retain E73’s custom signal. |

Implementation references: [proposal application](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/contracts.py:107), [volatility overlay behavior](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/features.py:55), and [registered interaction semantics](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/signals.py:108).

These distinctions matter for learning: storing “this economic hypothesis failed” when the encoded strategy tested something else can teach the next model the wrong lesson. Current memory stores recipes and measured outcomes, but it does not automatically label this semantic mismatch.

## Provider and review results

- **31 provider calls:** 11 proposal attempts (including the one repair) and 20 review attempts. Three distinct proposal responses were accepted; **0/8 baseline/candidate summaries completed**.
- Failures: **7 quota responses, 14 deadlines, 5 incomplete outputs, 1 local schema rejection and 1 duplicate rejection**. Quota behavior was intermittent: Gemini failed several calls but completed Run 2’s proposal.
- Run 4 exercised the single repair allowance after an OpenRouter schema rejection. The corrective call then returned an incomplete response. The later NVIDIA duplicate did not receive another correction because the one-repair allowance was already used.
- One inspected OpenRouter proposal consumed all 4,096 output tokens in reasoning and returned no content (`finish_reason=length`, routed model `nex-agi/nex-n2.5-mini:free`). A low-reasoning request therefore did not guarantee room for executable output on that route.
- Proposal and review phases accounted for **77.2%** of elapsed time. This includes orchestration work within those phases, not just measured network latency.
- Reported input/output usage totaled **87,293 tokens**, with **21 calls of unknown usage**. The ledger conservatively charged **574,281 budget units**. That is not confirmed provider token consumption or a monetary charge.

The four runs exercised the research loop’s model summary stage. They did not run an independent portfolio-team committee or an approved matched-follow-up task. This report’s analysis was produced from the measurements and recipe/source inspection, not from unavailable review summaries.

## Recommended changes before increasing run volume

1. **Require explicit executable intent.** Reject hypothesis-only model responses; require mechanism/falsification and a declared recipe change. Clearing a custom signal should require an explicit reset action. Acceptance: the exact Run 2 response is rejected before any worker is launched.
2. **Add a cheap semantic preflight with ready-to-use operator examples.** Return the actual recipe diff, operator equations, feature units/ranges, condition frequencies and attenuation multipliers. Include signed-versus-positive-only examples. Acceptance: Run 1’s below-zero volatility filters and Run 3’s unsupported positive clipping are clearly identified before the expensive experiment.
3. **Separate implementation mismatch from economic falsification in memory.** Link each lesson to what was actually executed and verified. Keep unsupported mechanism explanations labeled as hypotheses. Use one controlled change or an explicit ablation when attribution matters.
4. **Improve provider routing and review execution.** Prefer a measured structured-output route; test review deadlines against observed latency and protect proposal budget from repeated baseline-review failures. Reuse compatible baseline evidence and carry provider cooldowns across related sessions where appropriate. These are proposed changes, not modifications made during this study.
5. **Preserve cumulative research accounting.** These were four adaptive sessions. The existing bootstrap guard uses each frozen program’s maximum-trial budget; memory alone does not turn it into an aggregate correction across all related sessions. No candidate passed here, but a larger study needs a predeclared cumulative budget and independent evaluation plan.

Preconfigured resources are useful here when they make the model execute the right experiment: operator examples, recipe inspection, distribution checks and trustworthy failure labels. These four runs do not establish a benefit from more frameworks, agents, or market datasets. An equal-budget comparison is still needed to measure whether the improved workflow produces better research decisions.

## Saved evidence

- [Machine-readable results](/home/supasuge/Documents/amat-research/workflow-four-results-20260912-143700/results.json)
- [Run manifest](/home/supasuge/Documents/amat-research/workflow-four-results-20260912-143700/manifest.json)
- [Comparison graphic](/home/supasuge/Documents/amat-research/workflow-four-results-20260912-143700/comparison.png)
- Run 1: [strategy report](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run1/report.html), [diagnostic charts](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run1/analysis/charts-144f7f450aa83f682c8c89c2df7a513a0aef2a3c259da20035d356a4ec972944.html), [chart data](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run1/analysis/charts-144f7f450aa83f682c8c89c2df7a513a0aef2a3c259da20035d356a4ec972944.json).
- Run 2: [strategy report](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run2/report.html), [diagnostic charts](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run2/analysis/charts-9163074da25c43079b43a911092e613e67dc8c55cc1bc1f922f8b478176223c1.html), [chart data](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run2/analysis/charts-9163074da25c43079b43a911092e613e67dc8c55cc1bc1f922f8b478176223c1.json).
- Run 3: [strategy report](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run3/report.html), [diagnostic charts](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run3/analysis/charts-6c467a9b86ba0d1afde17c238da22f24519e933006b4cebac1ee8bda9100f50d.html), [chart data](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run3/analysis/charts-6c467a9b86ba0d1afde17c238da22f24519e933006b4cebac1ee8bda9100f50d.json).
- Run 4: [strategy report](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run4/report.html), [diagnostic charts](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run4/analysis/charts-a7273f02c96bc7a08ae47ffd4f9c60f98b1abf26cafed960899648a3cf8d4332.html), [chart data](/home/supasuge/Documents/amat-research/workflow-four-20260912-143700-run4/analysis/charts-a7273f02c96bc7a08ae47ffd4f9c60f98b1abf26cafed960899648a3cf8d4332.json).
