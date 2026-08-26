"""Scheduled market review: morning, midday, and evening observation passes.

What this is, and what it is not
--------------------------------
Three read-only passes that describe the state of the market and of the paper
account, computed from completed data. They **observe**. They form no decision,
size nothing, and submit nothing. The strategy's decision continues to come from
:func:`amat.planning.build_plan` and nowhere else.

This package imports :mod:`amat` deliberately, and that is the opposite of the
rule ``newsdesk`` follows. ``newsdesk`` is a collector with its own network
policy and no business knowing what a strategy is. A review pass, by contrast,
reports on *this* strategy: CLAUDE.md's "one strategy core" requires it to call
the same context, regime, and planning functions the backtest calls, because a
review that recomputed trend or levels its own way would eventually disagree
with the system it claims to describe, and the disagreement would be invisible.
The dependency runs one way only -- ``amat`` imports neither this package nor
``newsdesk`` -- and ``tests/unit/test_deskreview_boundary.py`` holds that line.

The vocabulary problem, stated honestly
---------------------------------------
The passes were requested in the vocabulary of discretionary swing trading:
*entry zone*, *trigger candle*, *target*, *stop*. Grepped 2026-08-10, none of
those concepts exists in this repository, and v1 is a long-or-cash,
volatility-targeted **portfolio** strategy that has no per-trade risk points at
all. Three of the four map onto something real; one does not, and inventing it
would be the worst thing this package could do -- a review that reports a "stop
at 412.50" implies a rule the strategy will never execute.

============== ==========================================================
Requested      What it honestly maps to here
============== ==========================================================
entry zone     **Real.** Two bands exist. The signal deadband
               (``trend.deadband_abs = 0.20``) decides whether an instrument
               is held at all; the rebalance band
               (``rebalance.min_weight_delta = 0.02``) decides whether an
               existing weight is adjusted. "In the entry zone" means the
               target-versus-current weight gap is approaching the band.
trigger        **Real, but not a candle.** v1 has no candlestick patterns.
               The genuine trigger is whether the decision formed from the
               last close crosses the deadband or the rebalance band, which
               is computable exactly. Bar geometry is reported alongside it
               as description, never as a cause.
target         **Does not exist.** v1 has no profit target. A position is
               reduced when the volatility-scaled signal decays or a
               constraint binds, not at a price. Reported as ``None``.
stop           **Does not exist.** v1 has no stop-loss. Reported as ``None``.
               What *is* reported is each holding's drawdown from its own
               peak and its distance to trailing support -- observations,
               explicitly not action levels.
============== ==========================================================

The model boundary
------------------
``.claude/rules/30-quant-methodology.md``: "Never allow an LLM to generate live
signal values, weights, fills, or accounting entries." Every number in a review
is produced by code in :mod:`deskreview.metrics`. A narration layer may read a
finished, frozen report and return prose. It cannot write a number back, because
:class:`~deskreview.passes.ReviewReport` is frozen and nothing consumes prose.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
