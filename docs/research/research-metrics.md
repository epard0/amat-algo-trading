# Research metrics, monitoring, and recorded data

```bash
./research 5       # Automatically records metrics and exports analysis tables
./research status  # Inspect the latest run without interrupting its writer
./research view    # Open the comparison report and its analysis downloads
./research analyze # Rebuild analysis exports for an existing completed session
```

The last three commands also accept an explicit session directory. `analyze`
verifies recorded evidence and uses a shared lock to avoid racing a live writer.
It does not rerun backtests or change selection decisions. Exports update
automatically at phase and experiment boundaries during new runs.

## Recorded lifecycle and resource use

The SQLite ledger now has an append-only `events` table with UTC timestamps.
It records batch boundaries, baseline/proposal/backtest/comparison/review phases,
escalation decisions, failures, proposal fallback, review saves, and model-call
starts and finishes. Phase events include experiment identifiers where available.
Model attempt durations use a monotonic clock. Phase durations are elapsed wall
time, not CPU time; interrupted phases are explicitly marked and do not acquire
invented completion durations when resumed.

`status` reads the ledger without constructing the writer or changing experiment
states. It reports the current phase and its age, writer activity, completed
strategy count, provider failures, and token accounting. If the recorded status
is running but no writer holds the session lock, it reports
`interrupted_or_inactive`. A baseline measurement failure now saves a failed
report and `baseline_failed` status before returning an error.

Provider tracking distinguishes known input/output tokens from **budget-charged
tokens**. The latter can include conservative reservations for unknown usage.
An unknown token count is not zero, and token counts are not dollar costs.
Monitoring totals cover the session; the provider table includes UTC day for
daily accounting. Unresolved calls can include interrupted attempts.

## Analysis files

All files live in the session's `analysis/` directory and are linked from the
browser report.

| File | Contents |
|---|---|
| `strategies.csv` | Decisions, recipe identifiers, hypotheses, proposal source, comparison score/margin, failed checks, normal/stressed performance, and derived risk metrics |
| `daily.csv` | Primary and stressed daily equity, returns, cash, gross exposure, cash attribution, rebased equity, drawdown, available risk forecasts and binding constraints |
| `fills.csv` | Executed fills with strategy, experiment, variant, date, symbol, side, quantity, price and cash costs |
| `missed.csv` | Missed execution records and their recorded reasons |
| `folds.csv` | Yearly development-period performance |
| `scenarios.csv` | Paired candidate/baseline scenario metrics with scenario kind, role, seed and cost assumptions |
| `providers.csv` | Provider/model, purpose, outcome, known tokens, budget charges and recorded attempt durations |
| `timings.csv` | Completed or interrupted phase records with timestamps and available durations |
| `events.jsonl` | Export of the durable lifecycle event log |
| `feature_diagnostics.json` | Proposal recipes, feature/pattern diagnostics, controls and fit counts |
| `metrics.json` | Structured aggregate metrics, monitoring snapshot and export row counts |
| `manifest.json` | Metric definitions, program digest, exporter-source digest, verified input artifact hashes and output hashes |

Strategy/account tables include program, experiment and candidate identifiers.
The `variant` field distinguishes primary from stressed execution. In the
scenario table, `candidate_id` identifies the subject of the experiment, while
`role` identifies whether a row measures that candidate or the frozen baseline.
Repeated baseline controls across comparisons are not independent samples.
Synthetic, resampled and historical execution cases retain separate `kind` labels.

CSV exports preserve numerical precision and neutralize common spreadsheet
formula prefixes in model-authored text. Scalar missing values are blank in CSV
and null in JSON. Structured columns, such as constraint lists, use JSON; their
`null` value means not recorded. Empty tables retain column headers.

## Derived metrics

These are diagnostics, not new selection gates or forecasts. They are also
provided to the LLM reviewer for new experiments.

- **Sortino, MAR 0:** annualized arithmetic mean daily return divided by annualized
  downside RMS, with zero contribution from positive-return days. Undefined when
  downside deviation is zero.
- **Calmar:** CAGR divided by the absolute maximum drawdown. Undefined when there
  is no drawdown.
- **Daily 95% VaR loss:** nonnegative loss corresponding to the linearly
  interpolated fifth percentile of observed daily returns.
- **Daily 95% expected shortfall loss:** nonnegative average loss in exactly the
  worst 5% of empirical return probability mass, with fractional boundary weight.
  Ties at a quantile do not dilute the tail by including every zero-return day.
- **Longest underwater period:** the longest consecutive observed session count
  below the previous peak, including an unrecovered interval.
- **Annualized one-way turnover:** traded absolute notional divided by preceding
  equity, summed and annualized at 252 sessions. Buys and sells both contribute;
  this is not a half-turnover convention.
- **Best/worst day:** observed daily return extrema in the measured period.

Daily rows include the measured-period anchor, identified by `is_anchor`.
Return-based statistics and turnover exclude that first row. `equity_index_100`
rebases each strategy to 100 at its own measured-period anchor, allowing comparison
despite different warm-up account values. Existing `hit_rate` is the fraction of
positive-return sessions, not a closed-trade win rate. Fills are not closed trades.

The exporter checks chronological unique sessions, positive finite equity,
agreement between recorded returns and equity changes, and artifact hashes.
New daily records preserve pre/post allocation volatility forecasts and binding
constraints. Undefined/nonfinite forecasts are serialized as null, never zero.

## Older sessions

The existing five-strategy session has been backfilled with metrics and analysis
tables from its verified measurements. Timings, risk forecasts or constraints
that were not originally recorded remain unavailable; they are not reconstructed
from file modification times or guessed. New sessions record those fields.

The recorded data remains adaptive development evidence. More detailed recording
helps diagnose a result; it does not turn reused history into independent validation.

See [recording verification](research-metrics-verification-2026-09-11.md) for the
completed live session, export integrity checks, and automated test results.

See [strategy flexibility and the failure library](strategy-flexibility-and-failure-library.md) for broader exploration,
research briefs, and analysis of every unsuccessful attempt.

New version 6 sessions export the incremental objective as `score`, with
`score_version`, `absolute_performance_score` (diagnostic only), and individual
`score_*` component columns. These include the original baseline reference,
matched candidate/baseline Sharpe, matched gain, added-complexity deduction,
exposures, exposure error, scaling and tolerance. See
[the scoring contract](exposure-matched-search-score.md). Historical rows retain
their original score; do not compare scores across objective versions.
