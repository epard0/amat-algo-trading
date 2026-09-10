"""Ingestion runs: fetch, archive, parse, store, and settle -- in that order.

The invariant this module owns is that **the run record never flatters the
pull**. Every path out of an ingestion -- success, partial parse, vendor error,
crash -- settles the run with a status that matches what actually happened:

``complete``
    Every page was fetched to proven termination and every row parsed.
``incomplete``
    Either pagination broke after at least one page was stored, or some rows
    were rejected by the parser. Data that parsed is kept -- discarding good
    rows because of bad neighbours would be its own loss -- but the coverage
    record says the window is not whole, so no feature built later can quietly
    treat it as whole.
``failed``
    Nothing usable was stored.

Order of operations per page: **archive first, then parse, then store.** The
verbatim bytes go into the raw-payload table before any interpretation is
attempted, so a parser bug never costs the evidence needed to fix it -- a
rejected row can be re-parsed from the archive by better code later, whereas a
row dropped before archiving is gone.

The one thing this module never does is pass ``ingested_at`` to the store.
That parameter exists for tests that need to construct historical timelines;
production ingestion always takes the store's own clock, because our
point-in-time guarantee is a claim about when *we* observed a row, and a
supplied value would let an ingest backdate knowledge. A test pins this.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from newsdesk.clients.adanos import AdanosClient, parse_adanos_mentions
from newsdesk.clients.alpaca import AlpacaNewsClient, parse_alpaca_news_page
from newsdesk.clients.base import FetchedPage
from newsdesk.clients.marketaux import MarketauxClient, parse_marketaux_page
from newsdesk.errors import ProviderSuspendedError
from newsdesk.store import NewsStore

__all__ = [
    "SUSPENSION_THRESHOLD",
    "IngestOutcome",
    "ingest_adanos_mentions",
    "ingest_alpaca_news",
    "ingest_marketaux",
]

#: Consecutive ``failed`` runs after which a provider is suspended. Three is a
#: diagnosis, not noise: one failure is weather, two might be, three in a row
#: means something standing -- a revoked key, a wrong tier, an exhausted plan --
#: and every further attempt spends metered quota (Marketaux: 100 requests/day;
#: Adanos: 250 requests/MONTH) on a request whose answer will not change until
#: a human changes something. ``force=True`` (the CLI's ``--force``) is that
#: human's statement that something changed; any successful run resets the
#: streak on its own.
SUSPENSION_THRESHOLD = 3

#: (records, rejected_rows, vendor_reported_total_or_None)
PageParser = Callable[[dict[str, Any]], tuple[list[Any], int, int | None]]


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """What one ingestion run did, exactly as the run table records it."""

    run_id: int
    provider: str
    status: str
    pages: int
    articles_parsed: int
    versions_written: int
    rejected: int
    rows_reported: int | None

    @property
    def summary(self) -> str:
        reported = "?" if self.rows_reported is None else str(self.rows_reported)
        return (
            f"{self.provider} run {self.run_id}: {self.status} -- {self.pages} page(s), "
            f"{self.articles_parsed} parsed, {self.versions_written} new version(s), "
            f"{self.rejected} rejected, vendor reported {reported}"
        )


def _run_ingestion(
    store: NewsStore,
    *,
    provider: str,
    host: str,
    pages: Iterator[FetchedPage],
    parser: PageParser,
    window_start: str | None,
    window_end: str | None,
    symbols: Sequence[str],
    force: bool = False,
) -> IngestOutcome:
    """Drive one run through the store's bookkeeping, settling it on every path.

    Raises:
        ProviderSuspendedError: when the provider's last
            :data:`SUSPENSION_THRESHOLD` settled runs all failed and ``force``
            is not set. Raised before any request is built, so a suspended
            provider costs zero quota.
    """
    provider_id = store.register_provider(provider, host)
    streak = store.consecutive_failures(provider_id)
    if streak >= SUSPENSION_THRESHOLD and not force:
        message = (
            f"{provider} is suspended: its last {streak} runs all failed, which points at "
            "a standing cause (revoked key, wrong tier, exhausted quota) that retrying "
            "cannot fix. Read `newsdesk coverage` for the recorded reasons, fix the cause, "
            "then re-run with --force; a successful run resets the breaker."
        )
        raise ProviderSuspendedError(message)
    run_id = store.open_run(
        provider_id, window_start=window_start, window_end=window_end, symbols=symbols
    )

    fetched = 0
    archived = 0
    parsed = 0
    written = 0
    rejected = 0
    reported: int | None = None
    try:
        for page in pages:
            fetched += 1
            store.archive_payload(run_id, page.number, page.raw)
            archived += 1
            records, bad, total = parser(page.payload)
            rejected += bad
            parsed += len(records)
            if reported is None:
                reported = total
            written += store.store(records, provider_id=provider_id, run_id=run_id)
    except Exception as exc:
        # A pull that broke mid-window persisted something real -- archived
        # bytes or stored rows; one that broke before persisting anything left
        # no trace but the run row. The two must never share a status, because
        # "failed" invites a clean retry while "incomplete" warns that the
        # window is partially represented until re-pulled. The pivot is on what
        # was PERSISTED, not on pages fetched: a page fetched but never archived
        # contributed nothing recoverable, and calling that "incomplete" would
        # contradict the module's own definition of "failed".
        status = "incomplete" if (archived or written) else "failed"
        store.settle_run(
            run_id,
            status=status,
            pages_fetched=fetched,
            rows_reported=reported,
            rows_stored=written,
            failure_reason=f"{type(exc).__name__}: {str(exc)[:400]}",
        )
        raise

    status = "incomplete" if rejected else "complete"
    store.settle_run(
        run_id,
        status=status,
        pages_fetched=fetched,
        rows_reported=reported,
        rows_stored=written,
        failure_reason=(
            f"{rejected} row(s) rejected by the parser; raw pages retain them" if rejected else None
        ),
    )
    return IngestOutcome(
        run_id=run_id,
        provider=provider,
        status=status,
        pages=fetched,
        articles_parsed=parsed,
        versions_written=written,
        rejected=rejected,
        rows_reported=reported,
    )


def ingest_marketaux(
    store: NewsStore,
    client: MarketauxClient,
    *,
    symbols: Sequence[str],
    published_after: str,
    published_before: str | None = None,
    language: str = "en",
    force: bool = False,
) -> IngestOutcome:
    """Pull one Marketaux window into the store.

    Windows should **overlap** their predecessors: the vendor does not document
    whether ``published_after`` is inclusive, so abutting windows risk dropping
    an article stamped exactly on the boundary. Overlap costs nothing -- the
    store is idempotent and a repeated article collapses into its existing
    version -- while a boundary gap is a hole no later query can detect.
    """
    return _run_ingestion(
        store,
        provider="marketaux",
        host="api.marketaux.com",
        pages=client.news_pages(
            symbols,
            published_after=published_after,
            published_before=published_before,
            language=language,
        ),
        parser=parse_marketaux_page,
        window_start=published_after,
        window_end=published_before,
        symbols=symbols,
        force=force,
    )


def ingest_alpaca_news(
    store: NewsStore,
    client: AlpacaNewsClient,
    *,
    symbols: Sequence[str],
    start: str,
    end: str,
    force: bool = False,
) -> IngestOutcome:
    """Pull one fully cursor-paginated Alpaca/Benzinga archive window."""
    return _run_ingestion(
        store,
        provider="alpaca_news",
        host="data.alpaca.markets",
        pages=client.news_pages(symbols, start=start, end=end),
        parser=parse_alpaca_news_page,
        window_start=start,
        window_end=end,
        symbols=symbols,
        force=force,
    )


def ingest_adanos_mentions(
    store: NewsStore,
    client: AdanosClient,
    *,
    ticker: str,
    start: dt.date,
    end: dt.date,
    force: bool = False,
) -> IngestOutcome:
    """Pull one ticker's Adanos news mentions into the store.

    Professional-tier gated; on a lower tier the vendor's 403 propagates and
    the run settles ``failed`` with the status in its reason -- a plan
    limitation recorded as one, never an empty-but-"complete" window.
    """
    return _run_ingestion(
        store,
        provider="adanos",
        host="api.adanos.org",
        pages=client.news_mention_pages(ticker, start=start, end=end),
        parser=parse_adanos_mentions,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        symbols=(ticker,),
        force=force,
    )
