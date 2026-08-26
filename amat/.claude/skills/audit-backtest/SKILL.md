---
name: audit-backtest
description: Perform an independent adversarial audit of an AMAT implementation, run, or report before promotion.
argument-hint: "<run id, report path, diff, or audit scope>"
disable-model-invocation: true
context: fork
agent: adversarial-reviewer
background: false
effort: high
---

Audit: `$ARGUMENTS`

Read the strategy contract, validation protocol, promotion gates, run manifest, configuration, data-quality record, ledgers, tests, and relevant source. Reproduce critical claims where artifacts permit.

Challenge:

- point-in-time availability and same-bar leakage;
- adjusted/raw price and corporate-action treatment;
- order timing, fill feasibility, fees, spread/slippage, and missed fills;
- cash, positions, distributions, and equity reconciliation;
- research/event-runner parity;
- experiment count, holdout access, parameter selection, and benchmark consistency;
- concentration by symbol, year, and trade;
- report traceability and omitted failures;
- any path toward live/paper order submission.

Use severity-calibrated findings with reproducible evidence. Conclude `PROMOTE`, `REJECT`, or `INCONCLUSIVE`; promotion means shadow/paper operational testing only.
