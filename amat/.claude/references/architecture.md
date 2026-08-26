# AMAT target architecture

## Dependency direction

```text
configuration/domain records
          ^
          |
data -> features -> portfolio -> targets
                         |          |
                         v          v
                  risk constraints  execution models
                         \          /
                          accounting
                              |
                   runners and framework adapters
                              |
                  validation, metrics, reporting
```

Domain modules must not import a broker SDK, web framework, notebook, or reporting library. Framework adapters translate events into shared domain records and call the same pure functions used by the research runner.

## Proposed source tree

```text
src/amat/
  config/          typed configuration and resolution
  domain/          bars, instruments, orders, fills, positions, ledgers
  data/            ingestion, normalization, manifests, calendars, actions
  features/        returns, volatility, trend
  portfolio/       covariance, allocation, constraints, risk contribution
  execution/       timing, VWAP, slippage, fees, missed-fill policy
  backtest/        session lifecycle, research runner, event adapter
  validation/      parity, walk-forward, bootstrap, sensitivity, leakage
  metrics/         performance, drawdowns, turnover, attribution
  reporting/       tables, charts, self-contained reports
```

## Required boundaries

- **Data boundary:** immutable source snapshots enter; normalized typed tables leave.
- **Decision boundary:** only information available by the decision timestamp enters; target weights leave.
- **Execution boundary:** target deltas and future market observations enter; fills or missed-fill records leave.
- **Accounting boundary:** prior ledger, fills, actions, and marks enter; a reconciled ledger leaves.
- **Reporting boundary:** immutable run artifacts enter; presentation artifacts leave without changing results.

## Dual-runner rule

The fast runner may vectorize feature computation, but it must advance portfolio state in explicit session order. The authoritative event-driven runner may model richer order state, but it cannot redefine signals or allocation. Under a simplified parity configuration, both runners must produce identical signals, targets, quantities, positions, and gross equity within documented tolerances.

## Reproducibility record

Every run manifest includes:

- run and strategy version;
- resolved configuration;
- code commit and dirty-tree status;
- dependency lock hash and Python version;
- dataset and partition hashes;
- source/feed/as-of coverage;
- execution and cost model versions;
- random seeds;
- start/end timestamps;
- validation status and failed gates.
