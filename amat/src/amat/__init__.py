"""AMAT: Adaptive Multi-Asset Trend backtesting platform.

This package is a research and backtesting system. It contains no broker
integration, no credential handling, and no order-submission path, and it must
not acquire one. See ``.claude/CLAUDE.md`` for the non-negotiable guardrails and
``.claude/references/strategy-contract.md`` for the normative v1 strategy.

Layer order (imports may only point upward in this list):

1. :mod:`amat.errors`, :mod:`amat.paths`, :mod:`amat.provenance`
2. :mod:`amat.domain` -- immutable records
3. :mod:`amat.config` -- typed, validated configuration
4. :mod:`amat.boundaries` -- protocols joining the stages
   and :mod:`amat.news_assets` -- validated inert universe overlays
5. stage packages: ``data``, ``features``, ``portfolio``, ``execution``,
   ``backtest``, ``validation``, ``metrics``, ``reporting``
6. :mod:`amat.cli`
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
