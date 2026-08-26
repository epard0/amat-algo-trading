"""Backtest stage: session lifecycle, accounting, research runner, event adapter.

Implements :class:`amat.boundaries.AccountingBoundary` and hosts both runners.

Contract for this package:

* the research runner may vectorize feature computation but must advance
  portfolio state in explicit session order;
* the event-driven adapter may model richer order state but may not redefine
  signals or allocation; both runners call the same functions in
  :mod:`amat.features` and :mod:`amat.portfolio`;
* under a simplified parity configuration both runners must produce identical
  signals, targets, quantities, positions, and gross equity within documented
  tolerances;
* every position change traces to a fill or a corporate action, and the ledger
  reconciles every session.

Not implemented yet.
"""

from __future__ import annotations

__all__: list[str] = []
