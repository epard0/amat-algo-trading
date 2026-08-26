"""Point-in-time financial news intake, stored separately from the strategy.

Why this is its own package
---------------------------
``amat`` is a backtesting core with a deliberately small surface: one read-only
market-data host, no third-party HTTP stack, and a layer test that fails the
build if an import points the wrong way. News intake is the opposite kind of
code -- many hosts, untrusted text, vendor schemas that drift, and a scheduler.
Putting it inside ``amat`` would widen that surface permanently.

So the two never import each other. ``newsdesk`` collects and stores; a future
strategy version reads point-in-time features out of the database. There is no
call path from a signal computation to a news API, and
``tests/unit/test_newsdesk_isolation.py`` asserts it in both directions.

What "point-in-time" means here, precisely
------------------------------------------
Two timestamps, never conflated:

``published_at``
    The vendor's claim about when the article appeared. It is *evidence*, not
    fact: vendors revise it, and a backfilled archive reflects the story as it
    was later understood.
``ingested_at``
    When this system first saw the row. This one we control and can prove.

A feature computed for a decision at time ``T`` filters on ``ingested_at <= T``,
not ``published_at <= T``. Filtering on the vendor's timestamp would silently
admit anything backfilled after the fact -- which is most of what an archive
endpoint returns -- and produce a backtest that traded on news it could not have
had. ``published_at`` is kept because it is the economically meaningful time, and
the gap between the two is itself a measurable data-quality signal.

The honest limitation, stated once
----------------------------------
**Forward-collected news is genuinely point-in-time. Historical backfill is
not, and no amount of schema design fixes that.** An archive query run today
returns the surviving, revised, still-indexed version of the past: stories
retracted, publishers that folded, and headlines edited after the fact are all
invisible. Any backtest over backfilled news is a rough sensitivity, never
evidence. The store records ``ingested_at`` so that the distinction is
queryable rather than a matter of trust, and so that a forward-collected sample
accumulating from today is unambiguous.

Article text is untrusted input
-------------------------------
Bodies and headlines are attacker-controllable strings from the open internet.
They are stored as data, parameterized into every query, size-capped, and never
concatenated into SQL. Where a downstream review pass summarizes them, the text
is quoted as content and never followed as instruction, and no such pass may
emit a signal value, a weight, or an order.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
