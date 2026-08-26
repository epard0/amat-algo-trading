---
paths:
  - "src/**/features/**"
  - "src/**/portfolio/**"
  - "src/**/backtest/**"
  - "src/**/validation/**"
  - "configs/strategy/**"
  - "configs/experiments/**"
  - "notebooks/**"
---

# Quantitative methodology rules

- Begin from the predeclared canonical strategy. Exploration is not permission to optimize the holdout.
- Compute signals only from completed observations available at the decision timestamp.
- Shift decisions into the next permitted execution window explicitly; tests must make the shift visible.
- Use nearby-parameter stability, walk-forward tests, stress tests, and benchmark decomposition rather than a single best backtest.
- Record every tested configuration in the experiment registry, including failures.
- Compare against SPY, 60/40, equal-weight, cash, and a signal-free portfolio using the same allocator and execution assumptions.
- Report gross and net results separately and attribute the difference to fees, spread/slippage, missed fills, and cash assumptions.
- Do not rank configurations by in-sample Sharpe alone.
- Avoid false precision: confidence intervals, fold dispersion, concentration, and drawdown duration matter.
- Never allow an LLM to generate live signal values, weights, fills, or accounting entries.
