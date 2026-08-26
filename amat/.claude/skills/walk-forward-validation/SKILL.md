---
name: walk-forward-validation
description: Design and implement predeclared expanding walk-forward evaluation and locked-holdout controls for AMAT without annual parameter overfitting.
user-invocable: false
---

Use chronological, expanding-context folds with test periods defined before results are inspected. AMAT does not require annual model retraining; the context region supports covariance/history and methodology checks.

- freeze sample boundaries and minimum warm-up;
- fit every data-dependent transform on information available before each fold;
- persist fold-level targets, fills, ledgers, metrics, and failures;
- aggregate only after reporting dispersion and worst folds;
- reserve the final configured period as a locked holdout;
- record every holdout access and prohibit parameter changes based on it;
- compare all benchmarks with identical fold, execution, and cost rules.

A positive aggregate with unstable or concentrated folds is not a pass.

## Implemented 2026-08-13

`src/amat/validation/walkforward.py` + `amat walkforward` (calendar folds of
the single expanding run, the three measurable research gates, per-regime
slices via `amat.validation.regimes`); `amat declare-holdout` freezes the
boundary into `outputs/holdouts/` and `assert_respects_holdout` fails closed
on unauthorized crossings, recording authorized ones in the run manifest.
The 2026-08-13 declaration records `history_burned: true`: every session at
or before 2026-08-10 was inspected by full-sample studies first, so fold
results are consistency evidence only — say "walk-forward consistency",
never "out-of-sample performance", for anything measured on burned data.
