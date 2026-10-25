# Agent-controlled minute strategy development

Start from the workspace root:

```bash
./research 50 --intraday --max-model-tokens-per-day 2000000
./research status
./research view
```

The first command downloads/caches actual Alpaca SIP one-minute bars using
`ALPACA_MARKET_API_KEY` and `ALPACA_MARKET_API_SECRET`. The initial supported
universe is SPY, QQQ, IWM and GLD, four liquid assets already in the daily universe.
The minute dataset covers 2022–2025: development uses 2022–2024; the full list of
frozen recipes is tested on 2025 only after proposal generation ends. This later
period is a post-search check, not a pristine project-wide holdout: those calendar
years and assets were already inspected in earlier daily research.

Use `--minute-data /absolute/path/to/dataset` for an existing, verified dataset,
`--workspace /absolute/path/to/new/session` for a chosen output directory, and
`--no-open` on a server. New sessions freeze source files, dependencies, data
hashes, provider order and evaluation assumptions. To resume after interruption,
run the session's `research.sh --no-open`; it uses the recorded runtime. A source
or data mismatch fails closed. The requested count is the total number of distinct
completed recipes, excluding the baseline. A renamed identical recipe does not
count as a new strategy.

## What agents control

Each proposal supplies a complete executable recipe, a mechanism, a falsification
criterion, and an explanation of its changes from the retained best:

- Decision cadence and maximum holding duration independently select **1, 5, 10,
  15, 30, 45 or 60 minutes**. These are minute counts, not daily sessions. Features
  use raw minute bars; cadence controls when the signal is evaluated for trading.
- Select any subset of the supported assets and long-only, short-only or both
  directions. Short results are simulations with an explicit fee proxy, without
  historical borrow-availability confirmation.
- Compose up to eight feature terms with independent lookback, smoothing, lag,
  transform, center, scale and weight. Features include momentum, mean reversion,
  channel position, breakout, efficiency, volatility, volume surprise, rolling
  VWAP distance, candle body, range expansion, trend agreement and acceleration.
- Combine terms with weighted sums, products, minima, maxima and eight optional
  interactions, including conditional gates. Lagged terms can express temporal
  conditions. Pure interaction rules can give the underlying terms zero weight.
- Choose entry/exit thresholds, confirmation, minimum/maximum holding limits,
  cooldown, stop loss, take profit, trailing stop, entry hours, per-asset sizing,
  maximum daily trade count and a per-sleeve daily loss limit.

Every proposal descends from the **current retained best**, while replacing the
entire strategy style is allowed. The daily research search follows the same
best-parent rule. Discarded strategies remain evidence for learning, rather than
becoming the next parent. Intraday starts from a separately measured,
E73-inspired 76-minute trend/channel interaction; the promoted daily E73 itself
remains unchanged and its daily performance is not presented as minute performance.

## What stays fixed for a fair comparison

Agents cannot alter historical observations, development/validation boundaries,
performance definitions, data-integrity checks, execution costs or selection gates.
There is no arbitrary generated-code execution and no broker/executor authority.

Completed-minute signals fill at a subsequent minute's open. Stops trigger from
completed closes and execute at the next open, so gaps can exceed stop thresholds.
Timed exits use the open after the actual holding duration. There are no overnight
positions, and all positions close by the final available regular-session open.
Minimum holding time constrains signal exits, not emergency stop exits.

The engine uses fractional fixed notional sleeves relative to each session's
starting equity, with at most 25% allocated per asset and four assets. A capacity
check assumes a $100,000 reference portfolio and limits orders to 1% of trailing
20-minute dollar volume. This is an approximate capacity filter, not observed
quote liquidity or a fill guarantee. Costs are 2 bps per side normally and 6 bps
per side in the stress replay. Simulated shorts pay an additional fixed 1 bp entry
fee; that is a borrow/locate proxy, not observed pricing. Spread, order-book depth,
queue position, live borrowing and point-in-time news are not modeled.

No forward-filled minute prices are used. Incomplete asset-days, including early
closes, abstain and remain in the return calendar. The exclusion is a retrospective
quality filter, held fixed across strategies; it is not a claim that real-time
systems know future outages. Features reset each session and wait for sufficient
history. Raw prices are safe from overnight corporate-action jumps because no
position or feature history crosses the session boundary.

## Selection and metrics

Intraday uses its own explicit score version. Scale candidate and the original
minute benchmark down to their common average exposure, compute the daily excess
return series, then score its annualized information ratio minus 0.01 for each
additional term/interaction. The baseline scores zero. Comparison against the
retained incumbent requires a score improvement greater than 0.05, positive
incremental return, profitable normal/stressed/delayed results, at least 50 trades,
acceptable drawdown, positive results in at least 60% of yearly folds, and a
positive lower bound in a paired five-session block bootstrap against the incumbent.
The 500-resample guard is development evidence, not a correction for all adaptive
search choices or independent proof of alpha. Matching is retrospective.

The daily engine's existing score definition remains separate. Intraday Sharpe
uses arithmetic mean daily returns divided by daily standard deviation, annualized
with 252 sessions and zero cash yield. Do not directly compare it with the daily
engine's prior CAGR-based Sharpe convention.

Reports include annual/total return, Sharpe, Sortino, Calmar, volatility, daily and
intraday drawdown at minute-open valuations, drawdown duration, daily VaR/CVaR, best/worst day, win rate,
profit factor, gross/net expectancy, average win/loss, holding time, MAE/MFE,
exposure, turnover, annualized cost drag, liquidity rejections, yearly folds,
asset/hour/direction attribution, exit reasons, and market-condition slices.
MAE/MFE use the high/low bars from entry through the bar before exit; exit-open
gaps are included in realized returns and minute-open drawdown, rather than these
pre-exit excursion statistics.
Condition slices are retrospective diagnostics, not forecast inputs.

`report.html` shows every candidate and its LLM explanation, with expandable full
recipes and evidence. `metrics.csv`, `report.json`, `failed-strategies.json`,
`data-quality.json` and `final-validation.json` support further analysis. Each
strategy has normal, cost-stress and delayed trade logs and daily returns in
`runs/`, including losslessly compressed minute signal arrays for later feature
analysis. Proposal records identify both requested and served models. Model
requests, responses, token usage and phase events are in
`research.sqlite3`. Rejections include objective, signal, execution, risk,
condition, uncertainty and data standpoints; hypotheses are distinct from
measured reasons. Unavailable LLM reviews are explicitly labeled. Intraday batches require model
proposals: failed requests retry with bounded reasoning/output budgets, honor
provider cooldowns, and wait up to an hour for service before pausing. They never
fill the requested 50 slots with built-in substitutes. Resume preserves completed
results and the retained best. Individual failed infrastructure attempts are
recorded under `errors/`.

The model order is Gemini → OpenRouter free models → NVIDIA NIM. Provider quotas
and the session's shared daily token budget still apply. No background live
trading or automatic promotion into the executor is performed.

Minute timestamp reference: [Alpaca stock minute bars](https://alpaca.markets/learn/stock-minute-bars).
