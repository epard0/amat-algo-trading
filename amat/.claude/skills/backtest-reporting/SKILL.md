---
name: backtest-reporting
description: Build traceable AMAT metrics, attribution, charts, and self-contained validation reports from immutable run artifacts without recomputation drift or misleading presentation.
user-invocable: false
---

Reports consume immutable artifacts; they do not rerun strategy logic silently.

Include data/config/code identity, coverage, failed/skipped gates, gross/net/benchmark results, drawdowns, exposures, turnover/costs, attribution, concentration, forecast/realized risk, walk-forward/holdout, robustness, reconciliation, and limitations.

Define every metric's units, annualization, cash-flow treatment, sample, and missing-value policy. Persist source tables for figures. Use log equity where appropriate, show full drawdowns, avoid truncated/misleading axes, and keep exploratory results visibly separate from canonical evidence.

The verdict must map mechanically to documented promotion gates and must never imply future profitability.
