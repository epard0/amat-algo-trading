"""Alpaca historical news: Benzinga articles from a read-only archive.

The endpoint and pagination contract come from Alpaca's official Historical
News Data reference, validated against a live response on 2026-09-07.  Alpaca
documents archive coverage back to 2015.  The collector still records our own
``ingested_at`` clock: archive availability is not proof that a backfilled item
was knowable to this system at publication time.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import Any

from newsdesk.clients.base import ApiCredential, FetchedPage, ReadOnlyJsonClient
from newsdesk.errors import IngestionIncompleteError, UntrustedContentError, VendorResponseError
from newsdesk.store import ArticleRecord, canonical_timestamp

__all__ = [
    "ENV_KEY",
    "ENV_SECRET",
    "AlpacaNewsClient",
    "parse_alpaca_news_page",
]

ENV_KEY = "ALPACA_MARKET_API_KEY"
ENV_SECRET = "ALPACA_MARKET_API_SECRET"  # noqa: S105 - environment variable name
HOST = "data.alpaca.markets"
_NEWS_PATH = "/v1beta1/news"
_MAX_PAGES = 2_000
_MAX_PAGE_SIZE = 50
_SYMBOL_RE = re.compile(r"[A-Z0-9](?:[A-Z0-9.]{0,10}[A-Z0-9])?")


class AlpacaNewsClient(ReadOnlyJsonClient):
    """Cursor-paginated, GET-only access to Alpaca historical news."""

    def __init__(
        self,
        key: ApiCredential | None = None,
        secret: ApiCredential | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        held_key = key or ApiCredential.from_environment(ENV_KEY)
        held_secret = secret or ApiCredential.from_environment(ENV_SECRET)
        super().__init__(
            host=HOST,
            credential=held_secret,
            additional_credentials=(held_key,),
            permitted_prefixes=(_NEWS_PATH,),
            timeout_seconds=timeout_seconds,
            requests_per_minute=190,
        )
        self._key = held_key
        self._secret = held_secret

    def news_pages(
        self,
        symbols: Sequence[str],
        *,
        start: str,
        end: str,
        limit: int = 50,
    ) -> Iterator[FetchedPage]:
        """Yield all pages in ascending publication order or fail closed."""
        for symbol in symbols:
            if not _SYMBOL_RE.fullmatch(symbol):
                raise UntrustedContentError(
                    f"refusing an implausible symbol in a request: {symbol!r}"
                )
        if not 1 <= limit <= _MAX_PAGE_SIZE:
            raise UntrustedContentError("Alpaca news limit must be between 1 and 50")

        token: str | None = None
        seen_tokens: set[str] = set()
        for number in range(1, _MAX_PAGES + 1):
            params: dict[str, str | int] = {
                "start": start,
                "end": end,
                "sort": "asc",
                "limit": limit,
                "include_content": "false",
            }
            if symbols:
                params["symbols"] = ",".join(symbols)
            if token is not None:
                params["page_token"] = token
            payload, raw = self._get_json(
                _NEWS_PATH,
                params,
                headers={
                    "APCA-API-KEY-ID": self._key.value,
                    "APCA-API-SECRET-KEY": self._secret.value,
                },
            )
            rows = payload.get("news")
            if not isinstance(rows, list):
                raise VendorResponseError(
                    f"page {number} carries no news list; the envelope schema has changed"
                )
            yield FetchedPage(number=number, payload=payload, raw=raw)
            next_token = payload.get("next_page_token")
            if next_token is None or next_token == "":
                return
            if not isinstance(next_token, str):
                raise VendorResponseError("Alpaca next_page_token is not a string")
            if next_token in seen_tokens:
                raise IngestionIncompleteError("Alpaca repeated a page token; refusing a cycle")
            seen_tokens.add(next_token)
            token = next_token
        raise IngestionIncompleteError(
            f"window still paginating after {_MAX_PAGES} pages; narrow the start/end window"
        )


def parse_alpaca_news_page(
    payload: dict[str, Any],
) -> tuple[list[ArticleRecord], int, int | None]:
    """Normalize one Alpaca page; malformed rows are counted and archived."""
    rows = payload.get("news")
    if not isinstance(rows, list):
        raise VendorResponseError("Alpaca response has no news list")
    records: list[ArticleRecord] = []
    rejected = 0
    for row in rows:
        if not isinstance(row, dict):
            rejected += 1
            continue
        title = row.get("headline")
        url = row.get("url")
        stamp = row.get("created_at")
        if not (isinstance(title, str) and title.strip()):
            rejected += 1
            continue
        if not (isinstance(url, str) and url.strip()):
            rejected += 1
            continue
        try:
            published = canonical_timestamp(stamp) if isinstance(stamp, str) else None
        except UntrustedContentError:
            published = None
        if published is None:
            rejected += 1
            continue
        tagged = row.get("symbols")
        symbols: dict[str, tuple[float | None, float | None]] = {
            symbol.strip().upper(): (None, None)
            for symbol in tagged
            if isinstance(symbol, str) and _SYMBOL_RE.fullmatch(symbol.strip().upper())
        } if isinstance(tagged, list) else {}
        identifier = row.get("id")
        summary = row.get("summary")
        source = row.get("source")
        records.append(
            ArticleRecord(
                vendor_uuid=str(identifier) if isinstance(identifier, str | int) else None,
                published_at=published,
                title=title,
                url=url,
                source_domain=source if isinstance(source, str) else None,
                language="en",
                snippet=summary if isinstance(summary, str) else None,
                symbols=symbols or None,
            )
        )
    return records, rejected, None
