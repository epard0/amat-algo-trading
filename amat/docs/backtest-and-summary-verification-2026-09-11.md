# Market-condition testing and automatic LLM review verification

The completed v5 research session is at
`/home/supasuge/Documents/amat-research/v5-final-market-scenarios-20260911`.
Its `report.md` contains the experiment table and actual provider-generated reviews.

Frozen program SHA-256:
`dd820909b0be3547fb6d1bba09dab67b66c5dc0c2cf8f5a5997d519e3746df34`.

## Executed experiment

The session used extended robustness, seed 20260911, two bootstrap paths, and a
900-second worker timeout. The submitted recipe was
`configs/autoresearch/breadth-router.example.json`. The controller established the
baseline, evaluated the recipe, and finished with `batch_completed` and no crashes.

| Measurement | Baseline | Breadth router |
|---|---:|---:|
| Historical development Sharpe, 2 bps | 0.7655 | 0.3303 |
| Historical development Sharpe, 6 bps | 0.6597 | 0.2632 |
| Worst hypothetical maximum drawdown | -55.10% | -39.87% |
| Completed paired scenario cases | 11 | 11 |
| Scenario integrity passed | Yes | Yes |
| Recorded decision | Baseline | Discard |

These historical measurements use the 2018–2022 development period. The router
failed `no_incremental_exposure_matched_information` and
`router_has_no_incremental_static_blend_value`. The baseline remained incumbent.
No validation checkpoint was used for selection, and execution authority was none.

Each battery contains six synthetic worlds, two joint bootstrap worlds, and
three historical execution stresses. The files contain 44 paired scenario result
records across the two experiments. Hypothetical drawdowns are outcomes under
declared generating assumptions, not estimates of future loss probabilities or
independent evidence of profitability.

## Actual LLM reviews

Both final reviews completed through `gemini / gemini-3.6-flash`. OpenAI returned
a rate-limit response, and the configured chain fell back to Gemini. The ledger
contains three provider attempts in total for this session.

The baseline review discussed positive historical performance alongside the
-55.10% worst hypothetical drawdown. The candidate review classified the result
as bad and explained the failed comparisons and uncertainty guards. These are
model-generated explanations of the recorded decisions, not decision authority.

An earlier live review overstated a failed uncertainty guard as proof of negative
value. That prompted additional instructions and a wording check. A subsequent
live response exposed an overly broad version of the check: it rejected a
disclaimer about guaranteed profits. The final check permits that disclaimer
while rejecting selected affirmative proof claims. The recorded cautious response
was replayed successfully through the final validator, and the final frozen
session then generated both reviews successfully with real provider calls.
These checks do not establish that every narrative sentence is correct.

## Verification

- Full suite: **1,698 passed, 1 skipped** in 321.27 seconds. The skip was the
  existing dependency-layer check for an undeclared `__init__.py` layer.
- After the final disclaimer adjustment: **52 focused tests passed**, with one
  slow test deselected. This covers summary availability, caching, evidence
  tampering, decision contradictions, overclaim rejection, and the research loop.
- Ruff lint and formatting checks passed; 195 files were already formatted.
- Mypy passed for 140 source files.
- SHA-256 checks passed for all 115 frozen source/data/configuration inputs,
  all 260 measurement artifacts, and both ledger-tracked review artifacts.

## Continue this session

```bash
export RESEARCH_DIR="/home/supasuge/Documents/amat-research/v5-final-market-scenarios-20260911"

# Model-proposed strategy experiments; reviews use the frozen summary chain.
"$RESEARCH_DIR/research.sh" loop --iterations 3 \
  --chain /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/llm-chain.example.toml

# Explicitly regenerate the review without repeating the backtest.
"$RESEARCH_DIR/research.sh" summarize --experiment 2

"$RESEARCH_DIR/research.sh" status
```

Omit `--chain` to use built-in proposal search instead. Model budgets and provider
availability still apply. New sessions created through the v5 CLI configure
automatic reviews by default; old frozen sessions retain their original runtime.

See [market-condition testing](backtest-market-conditions.md) and
[LLM review behavior and retry commands](research-run-summaries.md).
