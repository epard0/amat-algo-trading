# Autonomous feature and signal development

New programs use `strategy-development/5`. The engine separates feature formulas,
forecast construction, trading policy and portfolio sizing. It runs in a frozen
workspace outside the executor-mounted checkout and exports matched backtest
artifacts to `portfolio_team`. It has no trading or deployment authority.

The [v2 implementation contract](strategy-development-v2-design.md) records the
scope and evidence boundaries. The [earlier verification](strategy-development-verification-2026-09-11.md)
records the v1 attenuation-only loop; its results are not v2 results. Existing v1
workspaces continue through their own frozen `research.sh` launcher.
The [v2 verification report](strategy-development-v2-verification-2026-09-11.md)
records the staged search, fitted signals, controls and final provider-boundary checks.
The [agent pattern research guide](agent-pattern-research.md) documents v3's
experimental-parent selection, temporal conditions and custom price-path templates.
The [regime router guide](regime-router-implementation.md) documents v4's trend/range
routing, breadth and volatility features, and static-blend falsification controls.
V5 adds [market-condition testing](backtest-market-conditions.md) and
[automatic LLM experiment reviews](research-run-summaries.md).
The [v5 verification report](backtest-and-summary-verification-2026-09-11.md)
records the executed scenario battery, actual LLM reviews, and checks.

## Start a new program

For the usual workflow, run `./research 5`. This creates the workspace, tests five
distinct candidates plus the baseline, and opens a comparison report. Use
`./research view` to reopen it. See [simple research](simple-research.md).
The commands below expose the advanced controls.

From `amat`:

```bash
uv run --locked autoresearch init /home/supasuge/Documents/amat-research/my-signal-program \
  --name my-signal-program --train-end 2022-12-30 --validation-end 2026-08-10 \
  --max-experiments 50 --stall-limit 12 --timeout-seconds 240 \
  --max-model-tokens-per-day 100000

/home/supasuge/Documents/amat-research/my-signal-program/research.sh features
/home/supasuge/Documents/amat-research/my-signal-program/research.sh loop --iterations 12
/home/supasuge/Documents/amat-research/my-signal-program/research.sh status
```

Initialization verifies the snapshot manifest and freezes source, configuration,
data, dependency versions, split policy and budgets. Resume with the frozen
launcher after editing the checkout. Changing installed dependencies requires
restoring the recorded environment or creating a new program. The launcher uses
the original interpreter; it is not an independent virtualenv.

All candidates retain the baseline universe, risk limits, 2 bps normal costs,
6 bps stress costs, next-session execution and accounting. A signal enters
through the existing runner's `[0, 1]` long-score override **before** portfolio
construction. Portfolio constraints still apply. This adapter supports the plain
v6 allocator; configurations enabling extra cross-sectional, structure, learned
regime or learned-risk selectors require a separate adapter.

## Recipe layers

A `Proposal` contains a hypothesis, up to three allowed strategy parameter
patches, optional legacy `features`, and an optional `signal`. Both feature lists
and `signal` are complete replacements. Set `signal: null` to restore the core
trend signal; use `features: []` to remove attenuation gates.

Each signal has up to four `terms`. A term contains an independent `FeatureSpec`
plus its combination `weight`, `center` and `scale`. Up to two interactions can
combine distinct term indices with a product or a difference. Every feature's
history must fit within the fixed warm-up budget. Arbitrary Python expressions,
file access, new data sources and broker calls are not proposal capabilities.

| Layer | Controls |
|---|---|
| Feature | Registered family; lookback 5–126; secondary lookback 5–126; trailing smoothing 1–21; transformation; normalization window 21–126; benchmark from frozen universe; clipping |
| Pattern | Lagged features, named temporal conditions, ordered event windows and custom path templates; see the pattern research guide |
| Continuous rule | Signed term weights −2 to 2; center; scale; optional limited pair interactions; optional mix with core trend |
| Fitted forecast | `learner: ridge`; horizon 5, 20 or 60; purged chronological inner calibration of feature count and regularization |
| Trading policy | Separate entry and exit thresholds; 1–5 confirmation sessions; minimum hold of 0–21 sessions |
| Portfolio | Existing fixed covariance, volatility targeting, symbol/cluster caps and execution rules |

The weighted forecast maps each term through `tanh((value-center)/scale)`, takes
an absolute-weight-normalized combination including interactions, and maps it to
`[0, 1]`. Ridge predicts volatility-normalized forward return and applies a bounded
`tanh` score mapping. These scores are **not calibrated probabilities**.
Term and interaction weights apply to the weighted learner. Ridge learns its own
coefficients; its feature transformations, centers and scales define the design
columns, and fitted coefficients are recorded in `fits.json`.

Hysteresis operates per asset and carries state across refits. Entry needs the
specified number of consecutive qualifying sessions. Exit uses the lower
threshold; a premature exit during minimum hold preserves the prior score.
Unavailable data forces abstention/exit even during minimum hold. The portfolio
can still reduce a position to satisfy risk limits.

## Registered feature families

`research.sh features` prints the versioned metadata catalog. Every formula uses
completed-session data and declares its inputs, units, availability and missing
policy. Full-session volume is separate from execution-window volume.

| Family | Meaning |
|---|---|
| `momentum` | Fractional close-to-close change over a trailing window |
| `normalized_momentum` | Trailing log return divided by trailing daily volatility × square root of horizon |
| `trend_agreement` | Signed agreement between two trailing return horizons |
| `trend_acceleration` | Change in normalized momentum over a secondary window |
| `volatility` | Annualized trailing log-return standard deviation |
| `drawdown` | Distance below the trailing closing-price peak |
| `efficiency` | Absolute displacement divided by absolute path movement |
| `donchian_position` | Position within the trailing closing-price range |
| `breakout_distance` | Log distance above the preceding range high, in daily volatility units |
| `breakout_persistence` | Fraction of recent closes above a level fixed before the confirmation window |
| `relative_strength` | Trailing log return less the declared benchmark's return |
| `volume_ratio` | Current full-session volume divided by the preceding window's mean |
| `volume_price` | Log volume surprise × bounded, volatility-normalized daily price movement |
| `variance_ratio` | Overlapping multi-session variance relative to one-session variance scaling |
| `downside_variance_share` | Fraction of squared returns contributed by negative returns |
| `rsi` | Existing Wilder RSI implementation rescaled to `[0, 1]` |
| `path_similarity` | Correlation of a trailing normalized log-price path with agent-authored template control points |

Existing AMAT structure implementations are reused for RSI, variance ratio,
Donchian position and downside variance share. Legacy attenuation recipes retain
their original formulas, including simple-return volatility; their diagnostic
metadata includes the exact legacy recipe.

Transformations are `identity`, `time_series_rank`, `rolling_zscore`,
`cross_sectional_rank`, `group_rank`, `volatility_scaled` and `train_zscore`.
Smoothing precedes transformation. Rank ties receive average ranks. Cross-sectional
ranking needs at least three finite assets. Group ranking uses frozen strategy
clusters; groups with fewer than three usable members fall back to the asset's
own trailing percentile. `train_zscore` is fit per asset only on the training
portion of each chronological fold. Other transformations are causal rolling
operations; they never use full-period fitted statistics.

## Examples

```bash
/home/supasuge/Documents/amat-research/my-signal-program/research.sh run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/trend-breakout.example.json

/home/supasuge/Documents/amat-research/my-signal-program/research.sh run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/fitted-trend-structure.example.json

/home/supasuge/Documents/amat-research/my-signal-program/research.sh run-one \
  /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/relative-participation.example.json
```

The first combines trend and breakout scores with an interaction and hysteresis.
The second fits a linear forecast from trend, downside structure and participation.
The third combines benchmark-relative strength with participation. These are
experiments, not recommended trading configurations.

## Chronological fitting and labels

The common evaluation anchor follows the original warm-up plus 252 initial
training sessions. With the default snapshot, the measured return period starts
in 2018. This differs from v1; v1 and v2 scores are not directly comparable.
Every candidate/control uses the same new anchor and end date.

Refits occur every 126 sessions. Forward labels enter at the next execution-window
VWAP and exit 5, 20 or 60 sessions later, using adjusted/raw close factors to put
the windows on the same return basis. Two-sided 2 bps costs are included. Missing
entry or exit windows produce missing labels. These labels are diagnostic and
training targets; portfolio results still use the complete corporate-action and
cash-accounting runner.

At a prediction boundary, only labels whose exit has matured before the embargo
are eligible for fitting. All assets use the same date boundaries. The inner
validation segment is the last 63 eligible training decision sessions, with its
own horizon-aware purge. Feature association, standardization and coefficients
are fit on the earlier segment. Ridge calibrates alpha over `{0.1, 1, 10}` and
one selected design column versus all columns. It then refits the chosen recipe
on all eligible training observations. Every cutoff, scaler, selected column,
coefficient and inner loss is persisted.

Recipe search sees these chronological development outcomes. They are therefore
**adaptive development evidence**, not independent validation of the search
process. The later checkpoint remains excluded from proposal context and
selection. Its historical period has already been inspected elsewhere in this
project; no pristine holdout or future-profitability claim is made.

## Feature diagnostics and controls

Every completed training experiment writes:

- Availability and feature persistence.
- Forward-return diagnostics at 5, 20 and 60 sessions, with missing-window counts
  represented by reduced usable observations.
- Return buckets whose edges come from initial training data, with bucket counts.
- Daily cross-sectional rank correlations and separate asset/year slices.
- Redundancy with the core trend and between candidate features.
- A date-block bootstrap interval for the high-minus-low bucket spread, where
  both tails have enough usable observations. All assets share date resamples.
- Full backtests with one and two extra decision-session delays.
- Leave-one-feature-out and interaction ablations, plus joint nearby lookback
  perturbations (or deadband perturbations for configuration-only recipes).
- A retrospective exposure-matched comparison with the frozen baseline.
- One deterministic block-shuffled timing control, explicitly noncausal and not
  interpreted as a p-value or a deployable strategy.

Exposure matching reduces the more exposed strategy with a fixed multiplier,
calibrated retrospectively on development data. It attempts up to four adjustments
and reports the residual error. A mismatch above two percentage points blocks
eligibility rather than being called a successful matched control. Both sides'
full accounting and the exact intervention are saved.

Inspect a candidate using its digest from `report.md` or `report.json`:

```bash
/home/supasuge/Documents/amat-research/my-signal-program/research.sh diagnostics \
  --candidate CANDIDATE_DIGEST
```

The frozen launcher verifies artifact hashes before returning cached diagnostics.

## Search and selection

The deterministic search first screens ten registered hypotheses, including a
fitted forecast. It then cycles through pattern hypotheses, local refinement, combinations, ablations
and policy experiments. Refinement exercises smoothing, normalization, ranking,
attenuation and nearby windows; policy experiments vary hysteresis or test the
fitted learner. Interactions and combinations draw from a small exploratory
frontier, so a component need not individually beat the incumbent to be tested
in combination.

The frontier retains up to four distinct feature-family/learner signatures with
finite scores and basic accounting/risk eligibility. Highly positively correlated
forecast series are deduplicated on the measured development window. Frontier
membership never means a keep or deployment approval. The frontier is rebuilt
from durable outcomes and verified prediction artifacts after restart.

Built-in screening trials consume the experiment budget but not the post-screen
stall budget. A model or manual proposal cannot claim this exemption by labeling
itself `screen`. Later non-improvements and failures consume both. Each proposal records
its stage and actual experimental parent. A keep must beat the global incumbent,
not merely a weaker exploratory parent.

Existing yearly-score, cost-stress, positive-year, drawdown, exposure, fill and
accounting gates remain. Eligibility additionally requires positive incremental
Sharpe after exposure matching, positive delayed-execution Sharpe, and neighboring
Sharpe above both zero and 75% of the primary Sharpe. The original `0.05` score
margin remains as a minimum effect threshold.

Before a keep, paired daily returns against the incumbent undergo a contiguous
60-session block bootstrap. The lower Sharpe-difference bound must be positive;
the tail is adjusted using the program's declared maximum trial count. This is a
conservative research guard, **not** a calibrated adaptive-search p-value or a
proof that overfitting has been eliminated. All thresholds, seeds and resample
counts are frozen with the program.

## Optional model proposer

```bash
/home/supasuge/Documents/amat-research/my-signal-program/research.sh loop --iterations 1 \
  --chain /home/supasuge/Documents/amat-workspace/amat/configs/autoresearch/llm-chain.example.toml
```

The model receives the registry, current recipe and compact training diagnostics
for up to the latest eight experiments within a 24 KB context cap, including information by horizon, baseline
redundancy, exposure-matched differences and uncertainty outcomes. It does not
receive validation, raw market files, source files or broker tools. Every response
must satisfy the typed recipe and protected-configuration checks.
Unchanged recipes and repeats of measured candidates are rejected at the provider
boundary, allowing the chain to try the next provider. Hypothesis prose alone
cannot count as a strategy change. A program with no distinct completed candidate
exports a valid empty review catalog.

Provider failover is serial and budgeted. Missing usage retains its conservative
reservation. Limits are per-workspace UTC token limits, not account-wide dollar
caps. Unavailable providers or exhausted budgets pause with a nonzero CLI status.
The proposer chain is separate from the portfolio-team review provider.

## Artifacts and checkpoint

Each training run has `diagnostics.json`, `controls.json`, `fits.json` and
`signals.npz`, alongside primary/stress manifests and full daily equity, returns,
exposure, attribution and fill records. Control subdirectories include exact
candidate recipes and interventions. NPZ files contain numeric arrays and string
axes and are loaded with pickle disabled. `research.sqlite3` records proposal,
parent, selection control, outcome and artifact hashes; `report.json` exposes the
exploratory frontier.

```bash
/home/supasuge/Documents/amat-research/my-signal-program/research.sh checkpoint

uv run --locked pmteam --root /home/supasuge/Documents/amat-research/my-signal-program cycle \
  /home/supasuge/Documents/amat-research/my-signal-program/catalog.toml \
  --provider fake --max-reviews 20
```

The checkpoint runs the frozen baseline and incumbent strategy on the later
period. Fitted strategies continue their declared chronological refitting as
labels mature; the recipe and research selection remain frozen. Checkpoint
results are cached and cannot change the incumbent. Fake reviews exercise
orchestration only. Neither a keep nor a review deploys a strategy or changes
executor configuration.
