"""Execute the AMAT strategy against an Alpaca **paper** account.

This package is the executor half of a deliberate split. The ``amat``
repository owns the *decision* and its provenance and is forbidden, by its own
guardrails and by a test that scans its source, from containing anything that
can reach a trading account. This package owns the *calendar*, the *account*,
and the *submission*, and it consumes the strategy only through the inert plan
artifact ``amat plan`` emits.

The dependency arrow points one way and is enforced:
``tests/test_boundary.py`` fails if this package ever imports ``amat``, and
``executor.broker.alpaca_paper`` asserts its own hostname on every request so
that "paper only" is a property of the code rather than a claim in a README.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
