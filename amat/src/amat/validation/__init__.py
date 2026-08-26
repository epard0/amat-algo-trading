"""Validation stage: leakage, parity, walk-forward, bootstrap, sensitivity.

Contract for this package:

* leakage checks include future mutation, explicit lag inspection, covariance
  cutoff, corporate-action availability, universe availability, and recorded
  holdout access;
* the locked holdout is opened only after the canonical configuration and the
  preceding gates are frozen and passing;
* every attempted configuration is registered before its result is inspected,
  and failed experiments are never deleted.

The sequence and required tests are in
``.claude/references/validation-protocol.md``. Submodules, imported explicitly
by their consumers rather than re-exported here:

* :mod:`~amat.validation.falsification` -- the control batteries a worthless
  conditioning signal, stance blend, or universe expansion cannot pass;
* :mod:`~amat.validation.walkforward` -- expanding walk-forward folds and the
  locked-holdout declaration;
* :mod:`~amat.validation.montecarlo` -- stationary-bootstrap resampling of the
  market panel and the resulting metric distributions;
* :mod:`~amat.validation.regimes` -- causal market-regime labels, per-regime
  performance slices, and the volatility-state conditioning candidate;
* :mod:`~amat.validation.sweep` -- the volatility-target search under split
  discipline;
* :mod:`~amat.validation.optimize` -- the shared split/fold/grid machinery;
* :mod:`~amat.validation.conditioning` -- the rejected v3 KMeans regime
  scalars, retained as evidence.
"""

from __future__ import annotations

__all__: list[str] = []
