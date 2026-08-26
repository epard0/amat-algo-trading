"""Marketaux news client: one endpoint, paginated to proven completion.

Contract provenance
-------------------
The vendor's documentation site refuses automated fetches (HTTP 403 on every
path tried, 2026-08-10), so this client is built from the documentation
*content* recovered through a mirrored copy with per-snippet source attribution,
cross-checked against the operator's sister API. Facts relied on:

* endpoint ``GET /v1/news/all``; auth is the required ``api_token`` **query
  parameter** -- no header mechanism is documented, hence the redaction
  machinery in :mod:`newsdesk.clients.base`;
* ``published_at`` is ISO-8601 UTC with **six** fractional digits and a ``Z``
  (``2024-11-08T01:24:00.000000Z``) -- normalized to the store's three-digit
  canonical shape at parse time, never stored verbatim;
* the pagination envelope is ``meta{found, returned, limit, page}`` -- no
  cursor, no ``has_more``;
* errors arrive as ``{"error": {"code", "message"}}`` with 402 for exhausted
  quota and 429 inside a 60-second window;
* the free tier serves 100 requests/day and **3 articles per request**.

Two documented behaviours shape the defaults below. ``group_similar`` defaults
to *true* on the vendor side, which collapses syndicated copies into groups --
and makes ``meta.found`` count groups, destroying it as a completeness target.
It is pinned ``false``: deduplication is the store's job, done accountably with
recorded keys. ``filter_entities`` is pinned ``true`` so an article's entity
list names the symbols it was matched for rather than every entity mentioned.

What is *not* verified, and how it is survived
----------------------------------------------
The boundary semantics of ``published_after``/``published_before`` (inclusive
or exclusive) are undocumented. Consecutive ingestion windows must therefore
**overlap by design**: the store is idempotent, so an article captured twice
collapses to one row, while a boundary gap would be a silent hole no query
could detect. The maximum ``limit`` and any deep-pagination ceiling are also
undocumented; the page guard below fails loudly rather than letting a long
window truncate silently.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import Any

from newsdesk.clients.base import ApiCredential, FetchedPage, ReadOnlyJsonClient
from newsdesk.errors import IngestionIncompleteError, UntrustedContentError, VendorResponseError
from newsdesk.store import ArticleRecord, canonical_timestamp

__all__ = ["ENV_TOKEN", "MarketauxClient", "parse_marketaux_page"]

#: Environment variable holding the API token. Named for the vendor's own
#: ``api_token`` parameter.
ENV_TOKEN = "MARKETAUX_API_TOKEN"  # noqa: S105 - a variable NAME, never a value

HOST = "api.marketaux.com"

_NEWS_PATH = "/v1/news/all"

#: Symbols travel into a query string; anything outside this shape is refused
#: before a request exists. Uppercase tickers with optional dots, as the
#: universe uses.
_SYMBOL_RE = re.compile(r"[A-Z0-9](?:[A-Z0-9.]{0,10}[A-Z0-9])?")

#: Hard stop for pagination. On the free tier (3 articles/request) a wide
#: window can imply tens of thousands of pages; stopping silently at some count
#: would report partial coverage as complete, so the guard raises and tells the
#: operator to narrow the window instead.
_MAX_PAGES = 200


class MarketauxClient(ReadOnlyJsonClient):
    """Paginated, read-only access to the Marketaux news archive."""

    def __init__(
        self, credential: ApiCredential | None = None, *, timeout_seconds: float = 30.0
    ) -> None:
        super().__init__(
            host=HOST,
            credential=credential or ApiCredential.from_environment(ENV_TOKEN),
            permitted_prefixes=(_NEWS_PATH,),
            timeout_seconds=timeout_seconds,
        )

    def news_pages(
        self,
        symbols: Sequence[str],
        *,
        published_after: str,
        published_before: str | None = None,
        language: str = "en",
        limit: int | None = None,
    ) -> Iterator[FetchedPage]:
        """Yield every page of the window, or raise before claiming completion.

        ``published_after``/``published_before`` take the vendor's documented
        forms (``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM``). Termination: a page
        returning fewer rows than its limit, or zero rows, is the last page. A
        missing ``meta`` block is a schema change and fails closed rather than
        being read as an empty archive.

        Raises:
            UntrustedContentError: for a malformed symbol.
            IngestionIncompleteError: if the page guard trips -- the window is
                too wide to pull completely and must be narrowed, not truncated.
            VendorResponseError: propagated from transport, including quota
                exhaustion (402) and a missing/invalid envelope.
        """
        for symbol in symbols:
            if not _SYMBOL_RE.fullmatch(symbol):
                message = f"refusing an implausible symbol in a request: {symbol!r}"
                raise UntrustedContentError(message)

        page = 1
        while True:
            params: dict[str, str | int] = {
                "api_token": self._credential.value,
                "published_after": published_after,
                "language": language,
                "group_similar": "false",
                "filter_entities": "true",
                "page": page,
            }
            if symbols:
                params["symbols"] = ",".join(symbols)
            if published_before is not None:
                params["published_before"] = published_before
            if limit is not None:
                params["limit"] = limit

            payload, raw = self._get_json(_NEWS_PATH, params)
            meta = payload.get("meta")
            if not isinstance(meta, dict):
                message = f"page {page} carries no meta block; the envelope schema has changed"
                raise VendorResponseError(message)
            yield FetchedPage(number=page, payload=payload, raw=raw)

            returned = int(meta.get("returned", 0))
            page_limit = int(meta.get("limit", 0))
            if returned == 0 or returned < page_limit:
                return
            if page >= _MAX_PAGES:
                message = (
                    f"window still returning full pages after {_MAX_PAGES} requests; "
                    "narrow the published_after/published_before window rather than "
                    "accepting a silent truncation"
                )
                raise IngestionIncompleteError(message)
            page += 1


def parse_marketaux_page(
    payload: dict[str, Any],
) -> tuple[list[ArticleRecord], int, int | None]:
    """Turn one page into records, counting what could not be trusted.

    Returns ``(records, rejected, reported_total)`` where ``reported_total`` is
    the vendor's ``meta.found`` -- meaningful as an article count only because
    requests pin ``group_similar=false``.

    Rejection is per row and counted, never silent: a row without a usable
    title, URL, or publication time cannot become a point-in-time fact, but its
    absence must show up in the ingestion run's status (the run settles
    ``incomplete`` when this counter is non-zero) and its bytes stay in the raw
    payload archive for a later, better parser.

    Two fields are deliberately dropped rather than coerced:

    * ``entities[].match_score`` is an unbounded relevance score (12.13 appears
      in the vendor's own example) and the store's ``relevance`` column is
      constrained to ``[0, 1]``. There is no principled normalization, so the
      mention carries ``None`` and the true score stays in the archived page.
    * ``sentiment_score`` outside ``[-1, 1]`` becomes ``None`` -- an
      out-of-range sentiment is a vendor defect, and clamping it would launder
      the defect into a feature value.
    """
    data = payload.get("data")
    if not isinstance(data, list):
        message = "page carries no data array; the envelope schema has changed"
        raise VendorResponseError(message)

    meta = payload.get("meta")
    reported: int | None = None
    if isinstance(meta, dict) and isinstance(meta.get("found"), int):
        reported = int(meta["found"])

    records: list[ArticleRecord] = []
    rejected = 0
    for row in data:
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
            published = canonical_timestamp(stamp) if isinstance(stamp, str) else None
        except UntrustedContentError:
            published = None
        if published is None:
            rejected += 1
            continue

        symbols: dict[str, tuple[float | None, float | None]] = {}
        entities = row.get("entities")
        if isinstance(entities, list):
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                symbol = entity.get("symbol")
                if not isinstance(symbol, str) or not symbol.strip():
                    continue
                sentiment = entity.get("sentiment_score")
                bounded = (
                    float(sentiment)
                    if isinstance(sentiment, int | float) and -1.0 <= float(sentiment) <= 1.0
                    else None
                )
                symbols[symbol.strip().upper()] = (None, bounded)

        vendor_uuid = row.get("uuid")
        description = row.get("description")
        snippet = row.get("snippet")
        language = row.get("language")
        source = row.get("source")
        records.append(
            ArticleRecord(
                vendor_uuid=vendor_uuid if isinstance(vendor_uuid, str) else None,
                published_at=published,
                title=title,
                url=url,
                source_domain=source if isinstance(source, str) else None,
                language=language if isinstance(language, str) else None,
                snippet=(
                    description
                    if isinstance(description, str) and description.strip()
                    else snippet
                    if isinstance(snippet, str)
                    else None
                ),
                symbols=symbols or None,
            )
        )
    return records, rejected, reported
