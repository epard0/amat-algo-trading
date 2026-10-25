# Regime router research: implemented surface

New sessions use `strategy-development/4`, `causal-features/4`, and research engine
`v1-frozen+research-signals/4`. Existing frozen sessions keep their original runtime;
initialize a new session to use this version.

This implements the daily rule-router and reusable-feature portions of the
[strategy plan](regime-router-strategy-plan-2026-09-11.md) and
[mathematics plan](regime-math-feature-plan-2026-09-11.md). It is an executable
research strategy with controls and artifacts, not a deployable strategy release.
The [verification report](regime-router-verification-2026-09-11.md) records passing
engineering checks and the two real seed experiments, both correctly discarded.

## Start and inspect

From the AMAT package directory, choose an unused workspace outside the checkout:

```bash
cd /home/supasuge/Documents/amat-workspace/amat
export RESEARCH_DIR="/home/supasuge/Documents/amat-research/router-$(date +%Y%m%d-%H%M%S)"
uv run --locked autoresearch init "$RESEARCH_DIR" \
  --name regime-router --max-experiments 50 --stall-limit 12 --timeout-seconds 600

"$RESEARCH_DIR/research.sh" run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/regime-router.example.json
"$RESEARCH_DIR/research.sh" run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/breadth-router.example.json
"$RESEARCH_DIR/research.sh" status
"$RESEARCH_DIR/research.sh" diagnostics
```

`diagnostics` defaults to the incumbent. To inspect a discarded router, pass its
digest from `status` with `diagnostics --candidate DIGEST`. Reuse `research.sh`
to resume; do not rerun `init` on an existing directory.

For model proposals, with provider credentials available in the environment:

```bash
"$RESEARCH_DIR/research.sh" loop --iterations 5 \
  --chain /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/llm-chain.example.toml
```

Without `--chain`, built-in search rotates through routing, patterns, refinement,
combination, ablation, and policy stages after initial screening. Routing can be
explored without a previous keep. The supplied recipes are hypotheses, not claims
of profitable strategies.

## Three reusable features

| Registry name | Formula and availability |
|---|---|
| `breadth` | Fraction of the frozen universe with positive trailing endpoint return. Requires at least three assets and 80% valid universe coverage. Missing assets remain unavailable. The shared market reading is repeated on the asset axis. |
| `volatility_shock` | Log(short-window daily return standard deviation / long-window standard deviation). `secondary_lookback` must be less than `lookback`. |
| `volatility_of_volatility` | Standard deviation over `secondary_lookback` observations of log volatility estimated over `lookback`. Its required history includes both windows. |

Volatility calculations use log adjusted-close returns, sample standard deviation,
and a 1e-12 floor before logarithms. A flat valid path has zero shock/instability;
missing observations remain unavailable. Existing lag, smoothing, clipping, and
causal transformations remain available to ordinary weighted/ridge signals and
pattern conditions. Breadth is market context; because it is shared across assets,
cross-sectional IC alone cannot measure its usefulness.

## Router recipe

`signal.router` references the signal's existing feature terms:

- `trend_terms` and `range_terms` choose the feature indices for two weighted
  sleeves. Each sleeve uses its selected signed term weights, centers, and scales
  through the existing tanh score mapping, normalized by total absolute weight.
- `state_term` selects the transformed measurement, before term center/scale,
  mapped linearly from `state_floor` to `state_ceiling` into a trend weight in
  [0,1]. `smoothing_sessions` applies a complete trailing mean.
- The range score is zero when the trend score is below `range_trend_floor`.
  This guards against buying every oversold decline.
- Optional `stress_term` uses the same pre-center/scale units. The multiplier is
  `1 - max_attenuation * clip((stress - stress_threshold)/stress_scale, 0, 1)`.
- `mode=static` uses `static_trend_weight`. `mode=training_mean` fixes per-asset
  mean adaptive weights using only the pre-measurement training segment, with
  at least 40 valid observations per asset. It does not refit on later results.

The final preference is the stress multiplier times the trend/range weighted
blend. Optional baseline mixing is clipped to [0,1]. One shared entry/exit policy
then applies, followed by the existing allocator and risk limits. The sleeves
are signal components, not independently funded portfolios or holding policies.

Every term must be referenced, and both sleeves need nonzero weights. Routers
currently require the weighted learner and causal rolling transformations;
interactions, patterns, and fitted `train_zscore` within a router are rejected
rather than ignored. Ordinary non-router signals retain those capabilities.
Router term/state histories plus smoothing must fit the frozen warm-up.
Missing required inputs force abstention/exit through the existing policy.

The efficiency recipe uses normalized momentum, contrarian RSI, directional
efficiency, and volatility shock. The breadth recipe substitutes market breadth
for efficiency. Agents may change these choices and thresholds within the schema.
The measurements and weights are rule scores, not calibrated regime probabilities.

## Evidence and controls

In addition to the existing baseline, stressed costs, delays, exposure matching,
nearby parameters, and block-shuffled score tests, routers receive:

- Trend-only and guarded range-only controls.
- Equal blend and pre-anchor training-mean blend controls.
- Equal blend without stress attenuation.
- Removal of stress attenuation and removal of the range trend guard.

Each control preserves the input coverage requirement so it does not silently
trade a larger sample. Controls are fully simulated and persisted with their
recipes. A router must pass the trial-budget-adjusted paired bootstrap comparison
against its available static-blend controls, in addition to the existing frozen
promotion gates. Failure adds `router_has_no_incremental_static_blend_value`.
Bootstrap guards remain adaptive research evidence, not calibrated p-values.

`signals.npz` includes `routing_scores` with columns `trend_sleeve`, `range_sleeve`,
`trend_weight`, and `stress_multiplier`. `fits.json` records router settings,
training boundaries, initial diagnostic quantiles, and frozen mean weights when
applicable. Feature diagnostics include all four inputs and all four routing
components. `controls.json` includes static-control uncertainty comparisons.
The model proposer receives the router grammar and these control results.

## Remaining plan items

HMM/BOCPD/GARCH models, longer forecast horizons and independent sleeve holding
policies, a frozen news-feature bridge, intraday OFI/VPIN, and a production plan
adapter remain future stages. The current 5/20/60-session horizons are unchanged.
Those additions require their respective fitting, data, and execution contracts;
this release does not enable an existing learned-overlay flag to bypass them.

Research results must establish useful behavior before extending the live plan
surface. `executor` needs no change for these research experiments.
