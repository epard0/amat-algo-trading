# Workflow map

## New repository

```text
/bootstrap-project
  -> /plan-phase data-foundation
  -> /implement-phase approved data plan
  -> /run-quality-gates full
  -> adversarial review
```

## Existing feature or defect

```text
repository-cartographer (when structure is unclear)
  -> /plan-phase <bounded scope>
  -> /implement-phase <approved plan>
  -> /run-quality-gates full
  -> /audit-backtest when scientific behavior changed
```

## Research question

```text
/dependency-research <question>
  -> record primary sources and version constraints
  -> implementation plan
  -> test against a minimal fixture
```

## Backtest promotion

```text
data-quality report
  -> leakage/accounting/parity gates
  -> canonical walk-forward
  -> locked holdout
  -> robustness matrix
  -> /audit-backtest <run id>
  -> promotion or rejection report
```

## Agent responsibilities

- `architecture-planner`: implementation-ready plans and dependency boundaries
- `implementation-lead`: integration and bounded delegation
- `repository-cartographer`: read-only codebase map
- `quant-researcher`: evidence and methodology
- `data-engineer`: data/manifests/actions/calendars
- `signal-engineer`: returns/volatility/trend
- `portfolio-risk-engineer`: covariance/allocation/constraints
- `backtest-engineer`: lifecycle/execution/accounting/adapters
- `validation-engineer`: walk-forward/stress/parity/overfitting controls
- `test-engineer`: fixtures/property/regression tests and gates
- `adversarial-reviewer`: independent challenge and severity-calibrated findings
- `performance-engineer`: measurement-led optimization
- `security-reviewer`: supply chain, secrets, command, path, and deserialization review
- `reporting-engineer`: traceable reports without recomputation drift
