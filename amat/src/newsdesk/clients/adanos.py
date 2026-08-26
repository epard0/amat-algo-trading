"""Adanos client: news mentions in, sentiment aggregates read but never stored.

What Adanos is, and what of it fits this store
----------------------------------------------
Adanos is a ticker-tagged sentiment and attention aggregator over Reddit, X,
financial news, and Polymarket -- not a news archive. Exactly one part of its
surface is article-shaped and therefore storable here: the news raw-mention
rows (``ticker, title, summary, url, source, author, published_at, created_utc,
sentiment_score, relevance_score``), which the vendor gates to the Professional
tier. Aggregate endpoints (``buzz_score``, trend, per-ticker snapshots) carry
**no documented point-in-time guarantee** -- the vendor does not say whether
they are computed at query time or snapshotted, nor whether history is revised.
An append-only article store must not launder a maybe-recomputed aggregate into
a point-in-time fact, so aggregates are exposed as a live *query* only and
never written to the database.

``created_utc`` is the vendor's own ingestion clock ("when the mention was
fetched into the system"). It is *their* observation time, not ours: our
``ingested_at`` is stamped by our store when we first see the row, because a
point-in-time claim is only as good as the clock of the party making it.
``created_utc`` survives in the archived raw page.

Contract provenance and the deny list
-------------------------------------
Built from the vendor's machine-readable references (``/llms.txt`` and
``/llms-full.txt``, fetched 2026-08-10) and its OpenAPI document (title "Adanos
Market Sentiment API" 1.49.0). Auth is the ``X-API-Key`` header; the vendor's
own CLI reads ``ADANOS_API_KEY``. The reference documents exactly one non-GET
endpoint (``POST /sentiment/v1/analyze``) and an *internal, deliberately
undocumented* ``/auth/v1/*`` route family. This client cannot express a POST at
all, and ``/auth/`` is denied by prefix -- a prefix rather than an endpoint
list, because a family whose contents are unpublished cannot be denied
endpoint-by-endpoint.

One first-fetch artifact is worth recording: an initial read of
``openapi.json`` confidently reported "no POST endpoints and no non-Reddit
routes", and both claims were false -- the document had been truncated
mid-specification by the fetching tool. The allowlist below is built from the
``llms.txt`` references instead, and a live re-validation against
``/openapi.json`` before first production use is part of the operator
checklist in ``newsdesk.cli``.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from typing import Any

from newsdesk.clients.base import ApiCredential, FetchedPage, ReadOnlyJsonClient
from newsdesk.errors import UntrustedContentError, VendorResponseError
from newsdesk.store import ArticleRecord, canonical_timestamp

__all__ = ["ENV_KEY", "AdanosClient", "parse_adanos_mentions"]

#: Environment variable holding the API key -- the vendor CLI's own convention.
ENV_KEY = "ADANOS_API_KEY"

HOST = "api.adanos.org"

#: The one namespace this client may reach: US-stock news. Widening this to the
#: Reddit/X/Polymarket namespaces is a deliberate, reviewable act.
_NEWS_PREFIX = "/news/stocks/v1/"

#: Internal route family, contents unpublished by design. Denied by prefix.
_DENIED = ("/auth/",)

#: Tickers are interpolated into the request *path*, so this is a path-traversal
#: guard as much as a sanity check: no slash, no dot-dot, no percent, nothing
#: outside a plain uppercase ticker shape.
#: Anchored on alphanumerics: "BRK.B" passes, "." / ".." / "A." do not. A
#: dot-only "ticker" is not exploitable today (urllib normalizes nothing here
#: and the allowlist prefix survives), but a path segment of ".." has no
#: business existing in a request this package builds.
_TICKER_RE = re.compile(r"[A-Z0-9](?:[A-Z0-9.]{0,10}[A-Z0-9])?")

#: Documented ceiling on raw-mention pages (limit 1-100).
_MAX_LIMIT = 100

#: Offset-pagination stop. Fails loudly, never truncates silently.
_MAX_PAGES = 200


class AdanosClient(ReadOnlyJsonClient):
    """Read-only access to the Adanos news-sentiment surface."""

    def __init__(
        self, credential: ApiCredential | None = None, *, timeout_seconds: float = 30.0
    ) -> None:
        super().__init__(
            host=HOST,
            credential=credential or ApiCredential.from_environment(ENV_KEY),
            permitted_prefixes=(_NEWS_PREFIX,),
            denied_prefixes=_DENIED,
            timeout_seconds=timeout_seconds,
        )

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._credential.value}

    @staticmethod
    def _validated(ticker: str) -> str:
        if not _TICKER_RE.fullmatch(ticker):
            message = f"refusing an implausible ticker in a request path: {ticker!r}"
            raise UntrustedContentError(message)
        return ticker

    def news_mention_pages(
        self,
        ticker: str,
        *,
        start: dt.date,
        end: dt.date,
        limit: int = _MAX_LIMIT,
    ) -> Iterator[FetchedPage]:
        """Yield raw news-mention pages for one ticker over ``[start, end]``.

        Dates use the vendor's documented ``from``/``to`` parameters,
        ``YYYY-MM-DD``, inclusive, UTC-bounded. Pagination is limit/offset; a
        page shorter than its limit is the last one.

        This endpoint is Professional-gated. On a lower tier the vendor returns
        403, which surfaces as :class:`VendorResponseError` naming the status --
        a plan limitation, reported as such rather than as an empty archive.
        Exceeding the plan's historical lookback *also* returns 403 by contract.

        Raises:
            UntrustedContentError: for a malformed ticker.
            VendorResponseError: from transport, tier gating, or a missing
                ``results`` array (the envelope is thinly documented; a shape
                change fails closed rather than reading as no news).
        """
        symbol = self._validated(ticker)
        if not 1 <= limit <= _MAX_LIMIT:
            message = f"limit must be in [1, {_MAX_LIMIT}], received {limit}"
            raise UntrustedContentError(message)

        path = f"{_NEWS_PREFIX}stock/{symbol}/mentions"
        offset = 0
        page = 1
        while True:
            payload, raw = self._get_json(
                path,
                {
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "limit": limit,
                    "offset": offset,
                },
                headers=self._headers(),
            )
            results = payload.get("results")
            if not isinstance(results, list):
                message = (
                    f"page {page} for {symbol} carries no results array; the envelope "
                    "schema is thinly documented and appears to have changed -- verify "
                    "against a live /openapi.json before trusting this pull"
                )
                raise VendorResponseError(message)
            yield FetchedPage(number=page, payload=payload, raw=raw)

            if len(results) < limit:
                return
            if page >= _MAX_PAGES:
                message = (
                    f"{symbol}: still returning full pages after {_MAX_PAGES} requests; "
                    "narrow the date window rather than accepting a silent truncation"
                )
                raise VendorResponseError(message)
            offset += limit
            page += 1

    def stock_snapshot(self, ticker: str) -> dict[str, Any]:
        """Live sentiment aggregate for one ticker. **Never stored.**

        The vendor documents no point-in-time guarantee for these numbers --
        they may be recomputed over the query range at request time, and history
        may be revised. They are for a human reading a review pass *now*; writing
        them to the article store would fabricate an as-of fact the vendor never
        promised. If snapshot history is ever wanted, it needs its own table
        whose rows say "observed at T", which is a different claim.
        """
        symbol = self._validated(ticker)
        payload, _ = self._get_json(f"{_NEWS_PREFIX}stock/{symbol}", {}, headers=self._headers())
        return payload


def parse_adanos_mentions(
    payload: dict[str, Any],
) -> tuple[list[ArticleRecord], int, int | None]:
    """Turn one mentions page into records, counting what could not be trusted.

    Returns ``(records, rejected, reported_total)``; the envelope documents no
    total, so ``reported_total`` is ``None`` and completeness rests on the
    short-page termination plus the run's page count.

    ``published_at``'s declared type is unverified (the reference hedges "ISO
    8601 or equivalent", and the vendor's own examples disagree between ISO
    strings and Unix epochs across namespaces), so both are accepted and
    normalized through :func:`newsdesk.store.canonical_timestamp` -- which also
    catches the milliseconds-for-seconds unit error an epoch mix-up produces. A
    row whose stamp resists both readings is rejected and counted, and the run
    settles ``incomplete``.
    """
    results = payload.get("results")
    if not isinstance(results, list):
        message = "mentions payload carries no results array"
        raise VendorResponseError(message)

    records: list[ArticleRecord] = []
    rejected = 0
    for row in results:
        if not isinstance(row, dict):
            rejected += 1
            continue
        title = row.get("title")
        url = row.get("url")
        stamp = row.get("published_at")
        if not (isinstance(title, str) and title.strip()) or not (
            isinstance(url, str) and url.strip()
        ):
            rejected += 1
            continue
        try:
            published = (
                canonical_timestamp(stamp)
                if isinstance(stamp, str | int | float) and not isinstance(stamp, bool)
                else None
            )
        except UntrustedContentError:
            published = None
        if published is None:
            rejected += 1
            continue

        ticker = row.get("ticker")
        sentiment = row.get("sentiment_score")
        relevance = row.get("relevance_score")
        bounded_sentiment = (
            float(sentiment)
            if isinstance(sentiment, int | float) and -1.0 <= float(sentiment) <= 1.0
            else None
        )
        # relevance_score's range is unverified; only a value already inside the
        # store's [0, 1] constraint is kept, anything else stays in the archive.
        bounded_relevance = (
            float(relevance)
            if isinstance(relevance, int | float) and 0.0 <= float(relevance) <= 1.0
            else None
        )
        symbols = (
            {ticker.strip().upper(): (bounded_relevance, bounded_sentiment)}
            if isinstance(ticker, str) and ticker.strip()
            else None
        )

        summary = row.get("summary")
        source = row.get("source")
        records.append(
            ArticleRecord(
                vendor_uuid=None,
                published_at=published,
                title=title,
                url=url,
                source_domain=source if isinstance(source, str) else None,
                language=None,
                snippet=summary if isinstance(summary, str) else None,
                symbols=symbols,
            )
        )
    return records, rejected, None
