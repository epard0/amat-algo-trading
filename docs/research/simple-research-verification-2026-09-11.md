# Five-strategy workflow verification

Executed from the workspace root:

```bash
./research 5 --no-open \
  --workspace /home/supasuge/Documents/amat-research/simple-five-strategies-20260911
```

The command returned zero with `batch_completed`, five completed candidate
strategies, zero failed measurement attempts, and zero escalations. The baseline
was an additional experiment. All five candidates have distinct recipe digests.

| Strategy | Annual return | Sharpe | Max drawdown | 6 bps Sharpe | Decision |
|---|---:|---:|---:|---:|---|
| Baseline | 4.01% | 0.765 | -7.89% | 0.660 | Retained reference |
| Momentum attenuation | 3.72% | 0.746 | -7.52% | 0.630 | Not escalated |
| Normalized momentum | 3.54% | 0.630 | -9.36% | 0.569 | Not escalated |
| Trend agreement | 3.80% | 0.709 | -7.40% | 0.611 | Not escalated |
| Trend acceleration | 2.36% | 0.512 | -7.97% | 0.325 | Not escalated |
| Donchian position | 3.05% | 0.580 | -8.08% | 0.476 | Not escalated |

These measurements cover 2018-01-02 through 2022-12-30 under the existing frozen
development contract. They are historical development results, not independent
validation or evidence of future returns.

## Proposal and review behavior

The configured proposal providers returned rate-limit, malformed-output, or
timeout outcomes. The launcher announced fallback and continued with built-in
search. Each result records `proposal_source: built-in`; these five recipes are
not presented as LLM-generated. The batch still completed all five requested
backtests without operator intervention.

All six outcome reviews completed: the baseline through
`openrouter / deepseek/deepseek-v4-flash`, and all five candidates through
`gemini / gemini-3.6-flash`. The LLMs explained the failed performance comparisons
and checks. The baseline remained incumbent through deterministic selection.

## Artifacts and presentation

The session contains `report.html`, `report.md`, `report.json`, and the full
per-strategy backtest artifacts. Each of the six experiments completed all
11 paired scenario cases with its scenario integrity check passing.

SHA-256 verification passed for 116 frozen inputs, 772 measurement artifacts,
and all six saved review artifacts. Frozen program digest:

`14f168b49bf9fe2fced32c11ec05c9b253e93ad566339af085e042442fb480bb`.

The browser comparison was inspected through a temporary localhost preview
during the run. Its count advanced as candidates finished, and expandable LLM
reviews and raw-result links were visible. Direct `file://` navigation was blocked
by the browser testing tool, so that route was not verified there. The preview
server and temporary tab were closed after inspection. Browser-opening dispatch
and the `--no-open` option are covered by unit tests.

The final HTML was regenerated from the same recorded results to include the
measured date range and score margin labels. The table was checked to contain
five candidate rows plus the baseline. The final code also preserves explicit
cached `run-one` replays; the automatic batch continues to reject duplicate recipes.

Both workspace-root and `amat` launchers were exercised. `./research view --no-open`
resolved the saved latest-session pointer to the correct report.

The final workflow plus metrics extension passed 1,714 tests with one skip;
lint and type checks passed. The original measurements now also have verified
analysis exports. See [recording verification](research-metrics-verification-2026-09-11.md)
for export counts and the fresh telemetry run.
