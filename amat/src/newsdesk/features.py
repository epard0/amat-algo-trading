"""Point-in-time news features exported as files: the only bridge to a strategy.

``amat`` and ``newsdesk`` never import each other; a strategy version that
conditions on news reads a *file* this module wrote before the run. The file
carries its own parameters as comments, so a result can always be traced back
to exactly the rule that produced it.

The v3 rule: attention-spike de-risk
------------------------------------
For each calendar date ``d`` the exporter asks the store one point-in-time
question: *how many articles were knowable by the end of that UTC day?* The
day's new-article count is the difference from the previous day (clamped at
zero -- withdrawals may shrink the knowable set). A day whose count spikes to
``spike_multiple`` times the trailing median gets the de-risk scalar; every
other day gets 1.0.

Why the end of the UTC day: the strategy forms its decision after the session
close and executes no earlier than the next session's 09:30 New York window,
which is 13:30/14:30 UTC. Everything ingested by 23:59:59.999Z on the decision
date was therefore genuinely knowable hours before any execution -- causal with
margin, and deterministic to re-derive.

Why counts filter on ``ingested_at``: it is our clock. The store began
collecting forward on 2026-08-11, so exports over earlier dates are all-zero
and the scalar is 1.0 everywhere -- v3 deliberately degenerates to v1 until
real history accrues. That is the honest behaviour, not a limitation to paper
over.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from newsdesk.errors import NewsdeskError
from newsdesk.store import NewsStore

__all__ = ["AttentionParameters", "attention_scalars", "render_scalar_csv"]

#: A trailing median over fewer days than this is noise, not a baseline.
_MIN_BASELINE_SESSIONS = 5


@dataclass(frozen=True, slots=True)
class AttentionParameters:
    """The declared constants of the v3 attention rule.

    Every value is recorded in the exported file's header. Changing any of them
    produces a different conditioning series and therefore a different
    experiment; there is no in-code tuning path.
    """

    baseline_sessions: int = 63
    spike_multiple: float = 3.0
    min_baseline_articles: int = 10
    derisk_scalar: float = 0.5

    def validate(self) -> None:
        if self.baseline_sessions < _MIN_BASELINE_SESSIONS:
            message = f"baseline_sessions must be at least {_MIN_BASELINE_SESSIONS}"
            raise NewsdeskError(message)
        if self.spike_multiple <= 1.0:
            message = "spike_multiple must exceed 1.0; spiking below the baseline is not a spike"
            raise NewsdeskError(message)
        if self.min_baseline_articles < 1:
            message = "min_baseline_articles must be at least 1"
            raise NewsdeskError(message)
        if not 0.0 < self.derisk_scalar <= 1.0:
            message = "derisk_scalar must lie in (0, 1]; conditioning may reduce risk, never add"
            raise NewsdeskError(message)


def _dates(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    current = start
    while current <= end:
        yield current
        current += dt.timedelta(days=1)


def _end_of_day(date: dt.date) -> str:
    return f"{date.isoformat()}T23:59:59.999Z"


def attention_scalars(
    store: NewsStore,
    *,
    start: dt.date,
    end: dt.date,
    symbols: Sequence[str] = (),
    parameters: AttentionParameters | None = None,
) -> list[tuple[dt.date, float, int]]:
    """Per-date scalars from the attention rule.

    Args:
        store: The news store to interrogate.
        start: First calendar date, inclusive. The baseline warm-up consumes
            the earliest ``baseline_sessions`` dates, which therefore always
            export 1.0.
        end: Last calendar date, inclusive.
        symbols: When given, the day's count is the sum of per-symbol knowable
            counts (vendor entity tags -- a lower bound). Empty means every
            article in the store.

    Returns:
        ``(date, scalar, new_articles)`` per calendar date, in date order.

    Raises:
        NewsdeskError: on an inverted window or invalid parameters.
    """
    parameters = parameters or AttentionParameters()
    parameters.validate()
    if end < start:
        message = f"window is inverted: {start.isoformat()} .. {end.isoformat()}"
        raise NewsdeskError(message)

    def cumulative(date: dt.date) -> int:
        as_of = _end_of_day(date)
        if not symbols:
            return store.count_knowable_at(as_of)
        return sum(store.count_knowable_at(as_of, symbol=symbol) for symbol in symbols)

    rows: list[tuple[dt.date, float, int]] = []
    history: list[int] = []
    previous = cumulative(start - dt.timedelta(days=1))
    for date in _dates(start, end):
        current = cumulative(date)
        new_today = max(0, current - previous)
        previous = current

        scalar = 1.0
        if len(history) >= parameters.baseline_sessions:
            baseline = statistics.median(history[-parameters.baseline_sessions :])
            floor = max(baseline, float(parameters.min_baseline_articles))
            if new_today >= parameters.spike_multiple * floor:
                scalar = parameters.derisk_scalar
        history.append(new_today)
        rows.append((date, scalar, new_today))
    return rows


def render_scalar_csv(
    rows: Sequence[tuple[dt.date, float, int]],
    *,
    symbols: Sequence[str],
    parameters: AttentionParameters,
    generated_at: str,
) -> str:
    """The exported file: self-describing header, then ``session,scalar`` rows.

    The consumer (``amat study --scalars``) requires exactly this header and
    ignores ``#`` lines, so the parameters ride along without a side channel.
    """
    spikes = sum(1 for _date, scalar, _count in rows if scalar < 1.0)
    lines = [
        "# newsdesk export-scalars -- v3 attention-spike de-risk series",
        f"# generated_at: {generated_at}",
        f"# symbols: {','.join(symbols) if symbols else '<all articles>'}",
        f"# baseline_sessions: {parameters.baseline_sessions}",
        f"# spike_multiple: {parameters.spike_multiple}",
        f"# min_baseline_articles: {parameters.min_baseline_articles}",
        f"# derisk_scalar: {parameters.derisk_scalar}",
        f"# rows: {len(rows)}  de-risked: {spikes}",
        "# scalar semantics: multiplies the combined portfolio's target weights;",
        "# 1.0 = unconditioned v1. Counts filter on ingested_at (our clock), so",
        "# dates before the store's first ingestion are structurally 1.0.",
        "session,scalar",
    ]
    lines.extend(f"{date.isoformat()},{scalar}" for date, scalar, _count in rows)
    return "\n".join(lines) + "\n"
