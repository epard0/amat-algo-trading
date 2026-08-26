# Validation protocol

## Sequence

1. Validate source coverage and immutable snapshot manifests.
2. Validate normalized schemas, sessions, adjustments, and corporate actions.
3. Validate signal mathematics with hand-calculated fixtures.
4. Validate allocation constraints with deterministic and property-based tests.
5. Validate execution timing and missed-fill behavior.
6. Reconcile cash, positions, fees, distributions, and equity every session.
7. Establish research/event-runner parity under simplified assumptions.
8. Freeze the canonical configuration and experiment registry.
9. Run expanding walk-forward evaluation.
10. Open the locked holdout only once the preceding gates pass.
11. Run parameter-neighborhood, cost, delay, missing-data, concentration, and regime stresses.
12. Generate an immutable validation report and promotion decision.

## Required anti-leakage tests

- Future mutation: replacing all prices after `t` cannot alter outputs through `t`.
- Explicit lag: target formation and fill timestamps must be inspectable and strictly ordered.
- Covariance cutoff: rows after decision time cannot enter the estimate.
- Corporate-action availability: announcements are applied by effective/payable semantics, not future knowledge.
- Universe availability: a symbol cannot appear before valid history and metadata exist.
- Holdout access: code records when and why holdout artifacts are read.

## Required accounting tests

```text
equity = cash + sum(quantity * mark)
change in equity = market P&L + distributions + cash yield - fees - slippage
```

Every unexplained difference fails the run. Tolerances must derive from declared numeric precision, not from the size of the observed error.

## Required robustness tests

- baseline, 2x, 3x, and 5x costs;
- one- and two-session execution delays;
- deterministic 5% and 10% missed fills;
- next-open and next-close alternatives;
- top-five and top-ten trade removal;
- best symbol and best year removal;
- signal-free allocation benchmark;
- nearby horizon, deadband, covariance, target-volatility, and rebalance values;
- block bootstrap confidence intervals;
- results by major historical market regime.

## Experiment hygiene

Register every attempted configuration before inspecting its result. Never delete failed experiments. If many variants are tested, report multiple-testing risk and probability-of-backtest-overfitting analysis where feasible.
