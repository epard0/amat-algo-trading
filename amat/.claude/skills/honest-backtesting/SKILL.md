---
name: honest-backtesting
description: Enforce scientifically honest backtest design and interpretation, including realistic costs, causal timing, benchmarks, uncertainty, concentration, and limits on profitability claims.
user-invocable: false
---

Treat every attractive historical result as a hypothesis under challenge.

- Make signal availability and execution lag explicit.
- Separate gross from net performance and include realistic costs/missed fills.
- Compare against passive and signal-free allocator benchmarks under identical assumptions.
- Preserve failed experiments and disclose the number of variants tried.
- Freeze the canonical configuration before the locked holdout is opened.
- Test nearby parameters, multiple periods, concentration, delayed execution, and higher costs.
- Report confidence intervals/fold dispersion, drawdown duration, and assumptions—not only Sharpe/CAGR.
- Distinguish risk scaling from alpha and backtest fit from future profitability.
- Reject results with leakage, unreconciled ledgers, partial evidence, or benchmark/sample switching.

Use `${CLAUDE_PROJECT_DIR}/.claude/references/validation-protocol.md` and `promotion-gates.md` as the decision contract.
