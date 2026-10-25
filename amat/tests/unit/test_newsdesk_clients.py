"""The vendor clients: redaction, fail-closed pagination, and hostile parsing.

Nothing here touches a network. The transport method is overridden with canned
bytes shaped exactly like the vendors' own documented examples -- including
Marketaux's six-fractional-digit timestamps and unbounded ``match_score``,
because those two details are precisely where a naive client would corrupt the
store.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from newsdesk.clients.adanos import AdanosClient, parse_adanos_mentions
from newsdesk.clients.alpaca import AlpacaNewsClient, parse_alpaca_news_page
from newsdesk.clients.base import (
    MAX_RESPONSE_BYTES,
    ApiCredential,
)
from newsdesk.clients.marketaux import MarketauxClient, parse_marketaux_page
from newsdesk.errors import (
    ConfigurationError,
    IngestionIncompleteError,
    UntrustedContentError,
    VendorResponseError,
)

#: Assembled from parts so credential scanners find nothing shaped like a key.
SECRET = "test-" + "secret-" + "value-123"


def credential() -> ApiCredential:
    return ApiCredential(variable="TEST_API_TOKEN", value=SECRET)


def marketaux_article(
    *,
    uuid: str = "a1b2c3",
    title: str = "Widgets Inc beats expectations",
    url: str = "https://example.test/widgets",
    published_at: str = "2026-08-10T14:22:31.000000Z",
    symbol: str = "SPY",
    sentiment: float = 0.7783,
    match_score: float = 12.133104,
) -> dict:
    """One article row in the vendor's documented shape."""
    return {
        "uuid": uuid,
        "title": title,
        "description": "Revenue rose sharply.",
        "keywords": "",
        "snippet": "Revenue rose...",
        "url": url,
        "image_url": None,
        "language": "en",
        "published_at": published_at,
        "source": "example.test",
        "relevance_score": None,
        "entities": [
            {
                "symbol": symbol,
                "name": "Widgets Inc",
                "exchange": None,
                "exchange_long": None,
                "country": "us",
                "type": "equity",
                "industry": "Technology",
                "match_score": match_score,
                "sentiment_score": sentiment,
                "highlights": [],
            }
        ],
        "similar": [],
    }


def marketaux_page(articles: list[dict], *, found: int, limit: int, page: int) -> dict:
    return {
        "meta": {"found": found, "returned": len(articles), "limit": limit, "page": page},
        "data": articles,
    }


class _TransportStub:
    """Mixin: canned responses in order, requests recorded. No diamond MRO --
    each stub subclasses its vendor client directly and installs this state."""

    responses: list
    urls: list

    def _install(self, responses: list) -> None:
        self.responses = list(responses)
        self.urls = []
        self._retry_sleep_scale = 0.0
        self._requests_per_minute = 1_000_000

    def _transport(self, url: str, headers) -> bytes:
        self.urls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StubMarketaux(_TransportStub, MarketauxClient):
    def __init__(self, responses: list) -> None:
        MarketauxClient.__init__(self, credential())
        self._install(responses)


class StubAdanos(_TransportStub, AdanosClient):
    def __init__(self, responses: list) -> None:
        AdanosClient.__init__(self, credential())
        self._install(responses)


class StubAlpaca(_TransportStub, AlpacaNewsClient):
    def __init__(self, responses: list) -> None:
        AlpacaNewsClient.__init__(
            self,
            ApiCredential(variable="TEST_ALPACA_KEY", value="test-key"),
            ApiCredential(variable="TEST_ALPACA_SECRET", value="test-secret"),
        )
        self._install(responses)


def alpaca_article(*, identifier: int = 101, symbol: str = "XOM") -> dict:
    return {
        "author": "Benzinga Newsdesk",
        "created_at": "2026-09-01T13:15:00Z",
        "headline": "Energy company updates its outlook",
        "id": identifier,
        "source": "benzinga",
        "summary": "Management published an updated outlook.",
        "symbols": [symbol],
        "updated_at": "2026-09-01T13:16:00Z",
        "url": f"https://example.test/news/{identifier}",
    }


def http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"https://api.example.test/v1/news/all?api_token={SECRET}",
        code,
        "error",
        None,  # type: ignore[arg-type]
        io.BytesIO(body),
    )


class TestPathPolicy:
    def test_a_denied_prefix_wins_over_everything(self) -> None:
        """Adanos documents an internal /auth/v1/* family whose contents are
        deliberately unpublished. Denied by prefix, checked before the
        allowlist, so widening the allowlist later cannot expose it."""
        client = StubAdanos([])
        with pytest.raises(ConfigurationError, match="denied route family"):
            client._get_json("/auth/v1/whatever", {})

    def test_a_path_off_the_allowlist_is_refused(self) -> None:
        client = StubMarketaux([])
        with pytest.raises(ConfigurationError, match="read-only surface"):
            client._get_json("/v1/entity/search", {})

    def test_a_hostile_ticker_cannot_traverse_the_path(self) -> None:
        """Tickers are interpolated into the request path, so this is the
        path-traversal guard, not cosmetics."""
        client = StubAdanos([])
        for hostile in ("../auth/v1/keys", "SPY/../../auth", "spy", "A" * 13, ""):
            with pytest.raises(UntrustedContentError, match="implausible ticker"):
                list(client.news_mention_pages(hostile, start=None, end=None))  # type: ignore[arg-type]


class TestCredentialRedaction:
    def test_the_credential_never_renders(self) -> None:
        held = credential()
        for rendered in (repr(held), str(held), f"{held}"):
            assert SECRET not in rendered

    def test_a_non_retryable_error_does_not_leak_the_token(self) -> None:
        """The token travels in the URL, and HTTPError carries the URL. Every
        message path must survive a vendor that reflects the request back."""
        body = json.dumps(
            {"error": {"code": "invalid_api_token", "message": f"bad token {SECRET} rejected"}}
        ).encode()
        client = StubMarketaux([http_error(401, body)])
        with pytest.raises(VendorResponseError) as caught:
            client._get_json("/v1/news/all", {"api_token": SECRET})
        assert SECRET not in str(caught.value)
        assert "invalid_api_token" in str(caught.value)

    def test_exhausted_retries_do_not_leak_the_token(self) -> None:
        client = StubMarketaux([http_error(500)] * 5)
        with pytest.raises(VendorResponseError) as caught:
            client._get_json("/v1/news/all", {"api_token": SECRET})
        assert SECRET not in str(caught.value)
        assert "HTTP 500" in str(caught.value)

    def test_error_messages_name_the_path_never_the_query(self) -> None:
        client = StubMarketaux([http_error(402)])
        with pytest.raises(VendorResponseError) as caught:
            client._get_json("/v1/news/all", {"api_token": SECRET, "symbols": "SPY"})
        message = str(caught.value)
        assert "/v1/news/all" in message
        assert "symbols" not in message
        assert "api_token" not in message


class TestTransportFailsClosed:
    def test_an_oversized_body_is_refused_not_truncated(self) -> None:
        class Oversized(StubMarketaux):
            def _transport(self, url: str, headers) -> bytes:
                return b"x" * (MAX_RESPONSE_BYTES + 1)

        client = Oversized([])
        with pytest.raises(VendorResponseError, match="byte cap"):
            client._get_json("/v1/news/all", {})

    def test_a_non_object_json_body_is_refused(self) -> None:
        client = StubMarketaux([b"[1, 2, 3]"])
        with pytest.raises(VendorResponseError, match="non-object"):
            client._get_json("/v1/news/all", {})

    def test_an_undecodable_body_is_retried_then_fails(self) -> None:
        """A truncated transfer is indistinguishable from short coverage at the
        JSON layer, so the partial body is discarded and retried, never
        parsed."""
        client = StubMarketaux([b"{'not json"] * 5)
        with pytest.raises(VendorResponseError, match="undecodable"):
            client._get_json("/v1/news/all", {})


class TestMarketauxPagination:
    def test_a_short_page_terminates_the_window(self) -> None:
        pages = [
            marketaux_page(
                [marketaux_article(uuid=f"u{i}") for i in range(3)], found=5, limit=3, page=1
            ),
            marketaux_page(
                [marketaux_article(uuid=f"v{i}") for i in range(2)], found=5, limit=3, page=2
            ),
        ]
        client = StubMarketaux([json.dumps(p).encode() for p in pages])
        fetched = list(client.news_pages(["SPY"], published_after="2026-08-01"))
        assert [page.number for page in fetched] == [1, 2]

    def test_an_empty_first_page_is_one_page_not_zero(self) -> None:
        """An empty window is an answer and must be archived as one."""
        client = StubMarketaux([json.dumps(marketaux_page([], found=0, limit=3, page=1)).encode()])
        fetched = list(client.news_pages(["SPY"], published_after="2026-08-01"))
        assert len(fetched) == 1

    def test_a_missing_meta_block_fails_closed(self) -> None:
        """No meta means the schema changed; reading it as an empty archive
        would record a coverage hole as a quiet day."""
        client = StubMarketaux([b'{"data": []}'])
        with pytest.raises(VendorResponseError, match="meta"):
            list(client.news_pages(["SPY"], published_after="2026-08-01"))

    def test_group_similar_is_pinned_off_and_the_token_travels(self) -> None:
        """group_similar=false is what makes meta.found an article count; the
        vendor default (true) counts groups and destroys the completeness
        target."""
        client = StubMarketaux([json.dumps(marketaux_page([], found=0, limit=3, page=1)).encode()])
        list(client.news_pages(["SPY", "GLD"], published_after="2026-08-01"))
        (url,) = client.urls
        assert "group_similar=false" in url
        assert "filter_entities=true" in url
        assert "symbols=SPY%2CGLD" in url

    def test_an_empty_symbol_list_requests_broad_news_without_a_blank_filter(self) -> None:
        payload = marketaux_page([], found=0, limit=3, page=1)
        client = StubMarketaux([json.dumps(payload).encode()])
        list(client.news_pages([], published_after="2026-08-01"))
        assert "symbols=" not in client.urls[0]

    def test_the_page_guard_raises_rather_than_truncating(self) -> None:
        full = json.dumps(
            marketaux_page([marketaux_article()] * 3, found=10_000, limit=3, page=1)
        ).encode()
        client = StubMarketaux([full] * 300)
        with pytest.raises(IngestionIncompleteError, match="narrow"):
            list(client.news_pages(["SPY"], published_after="2020-01-01"))

    def test_a_malformed_symbol_is_refused_before_any_request(self) -> None:
        client = StubMarketaux([])
        with pytest.raises(UntrustedContentError, match="implausible symbol"):
            list(client.news_pages(["SPY", "bad symbol"], published_after="2026-08-01"))
        assert client.urls == []


class TestMarketauxParsing:
    def test_the_six_digit_timestamp_is_normalized_to_canonical(self) -> None:
        """The vendor stamps 2024-11-08T01:24:00.000000Z. Stored verbatim, that
        breaks the store's text ordering; normalization is not optional."""
        records, rejected, found = parse_marketaux_page(
            marketaux_page(
                [marketaux_article(published_at="2024-11-08T01:24:00.000000Z")],
                found=1,
                limit=3,
                page=1,
            )
        )
        assert rejected == 0
        assert found == 1
        assert records[0].published_at == "2024-11-08T01:24:00.000Z"

    def test_unbounded_match_score_becomes_none_not_a_clamp(self) -> None:
        """The vendor's own example carries match_score 12.13 against the
        store's [0,1] relevance constraint. There is no principled
        normalization, so the mention carries None and the truth stays in the
        archived page."""
        records, _, _ = parse_marketaux_page(
            marketaux_page([marketaux_article(match_score=12.133104)], found=1, limit=3, page=1)
        )
        relevance, sentiment = records[0].symbols["SPY"]
        assert relevance is None
        assert sentiment == pytest.approx(0.7783)

    def test_an_out_of_range_sentiment_is_dropped_not_clamped(self) -> None:
        """Clamping would launder a vendor defect into a feature value."""
        records, _, _ = parse_marketaux_page(
            marketaux_page([marketaux_article(sentiment=1.7)], found=1, limit=3, page=1)
        )
        _, sentiment = records[0].symbols["SPY"]
        assert sentiment is None

    def test_rows_without_title_url_or_timestamp_are_counted_not_dropped(self) -> None:
        broken_title = marketaux_article()
        broken_title["title"] = ""
        broken_stamp = marketaux_article(uuid="x9")
        broken_stamp["published_at"] = "not-a-date"
        good = marketaux_article(uuid="ok1", url="https://example.test/ok")
        records, rejected, _ = parse_marketaux_page(
            marketaux_page([broken_title, broken_stamp, good], found=3, limit=3, page=1)
        )
        assert len(records) == 1
        assert rejected == 2

    def test_a_missing_data_array_fails_closed(self) -> None:
        with pytest.raises(VendorResponseError, match="data array"):
            parse_marketaux_page({"meta": {"found": 0}})


class TestAdanos:
    @staticmethod
    def mention(published_at: object = "2026-08-10T14:00:00+00:00") -> dict:
        return {
            "ticker": "SPY",
            "title": "Fund flows accelerate",
            "summary": "Flows rose.",
            "url": "https://example.test/flows",
            "source": "example.test",
            "author": "A. Writer",
            "published_at": published_at,
            "created_utc": 1754830800,
            "sentiment_score": 0.41,
            "relevance_score": 0.9,
        }

    def test_offset_pagination_terminates_on_a_short_page(self) -> None:
        import datetime as dt

        first = {"results": [self.mention() for _ in range(2)]}
        client = StubAdanos([json.dumps(first).encode()])
        pages = list(
            client.news_mention_pages(
                "SPY", start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 10), limit=3
            )
        )
        assert len(pages) == 1
        (url,) = client.urls
        assert "from=2026-08-01" in url
        assert "to=2026-08-10" in url
        assert "offset=0" in url

    def test_the_auth_header_is_sent_not_a_query_parameter(self) -> None:
        import datetime as dt

        class HeaderRecorder(StubAdanos):
            def __init__(self, responses):
                super().__init__(responses)
                self.headers: list[dict] = []

            def _transport(self, url: str, headers) -> bytes:
                self.headers.append(dict(headers))
                return super()._transport(url, headers)

        client = HeaderRecorder([json.dumps({"results": []}).encode()])
        list(client.news_mention_pages("SPY", start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 10)))
        assert client.headers[0]["X-API-Key"] == SECRET
        assert SECRET not in client.urls[0]

    def test_a_missing_results_array_fails_closed(self) -> None:
        import datetime as dt

        client = StubAdanos([b'{"data": []}'])
        with pytest.raises(VendorResponseError, match="results array"):
            list(
                client.news_mention_pages(
                    "SPY", start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 10)
                )
            )

    def test_an_epoch_published_at_is_accepted_and_normalized(self) -> None:
        """The vendor's declared type is unverified and its own examples
        disagree between ISO and epoch; both must parse to canonical."""
        records, rejected, _ = parse_adanos_mentions(
            {"results": [self.mention(published_at=1754834551)]}
        )
        assert rejected == 0
        assert records[0].published_at == "2025-08-10T14:02:31.000Z"

    def test_a_milliseconds_epoch_is_rejected_as_a_unit_error(self) -> None:
        """1754834551000 read as seconds is the year 57573. That is a unit
        error, and it must fail here rather than become a stored article."""
        _, rejected, _ = parse_adanos_mentions(
            {"results": [self.mention(published_at=1754834551000)]}
        )
        assert rejected == 1

    def test_the_vendors_ingestion_clock_is_not_ours(self) -> None:
        """created_utc is when *Adanos* saw the story. Our ingested_at is when
        *we* did, stamped by the store; the vendor's clock stays in the raw
        archive only."""
        records, _, _ = parse_adanos_mentions({"results": [self.mention()]})
        # The record carries no ingestion time at all -- the store stamps it.
        assert not hasattr(records[0], "ingested_at")

    def test_bounded_scores_are_kept_and_unbounded_dropped(self) -> None:
        good = self.mention()
        bad = self.mention()
        bad["relevance_score"] = 3.5
        bad["url"] = "https://example.test/other"
        records, _, _ = parse_adanos_mentions({"results": [good, bad]})
        assert records[0].symbols["SPY"] == (0.9, 0.41)
        assert records[1].symbols["SPY"] == (None, 0.41)

    def test_the_snapshot_is_reachable_but_returns_data_not_records(self) -> None:
        """Aggregates carry no point-in-time guarantee and are never stored;
        the method hands back the payload for a human, not ArticleRecords."""
        client = StubAdanos([json.dumps({"found": True, "buzz_score": 87}).encode()])
        payload = client.stock_snapshot("SPY")
        assert payload["buzz_score"] == 87


class TestScrubCoversEveryEncoding:
    """The token rides in a query string, so a vendor reflecting the request URL
    reflects the percent-ENCODED token -- which a raw-value replace walks
    straight past. Measured 2026-08-11 with a token containing ``+/=``."""

    def test_the_encoded_form_is_scrubbed_too(self) -> None:
        import urllib.parse

        awkward = "sk-" + "live-" + "ab+c/12=34"  # assembled; scanners find nothing
        held = ApiCredential(variable="TEST_TOKEN", value=awkward)
        reflected = "bad request: https://api.example.test/v1/news/all?" + urllib.parse.urlencode(
            {"api_token": awkward}
        )
        scrubbed = held.scrub(reflected)
        assert awkward not in scrubbed
        assert urllib.parse.quote_plus(awkward) not in scrubbed
        assert "ab%2Bc" not in scrubbed

    def test_a_reflected_error_with_an_encoded_token_does_not_leak(self) -> None:
        import urllib.parse

        awkward = "tok+" + "en/with=" + "specials"
        held = ApiCredential(variable="TEST_TOKEN", value=awkward)

        class Reflecting(MarketauxClient):
            def __init__(self) -> None:
                MarketauxClient.__init__(self, held)
                self._retry_sleep_scale = 0.0
                self._requests_per_minute = 1_000_000

            def _transport(self, url: str, headers) -> bytes:
                body = json.dumps(
                    {
                        "error": {
                            "code": "malformed_parameters",
                            "message": "rejected: "
                            + urllib.parse.urlencode({"api_token": awkward}),
                        }
                    }
                ).encode()
                raise http_error(400, body)

        client = Reflecting()
        with pytest.raises(VendorResponseError) as caught:
            client._get_json("/v1/news/all", {"api_token": awkward})
        assert awkward not in str(caught.value)
        assert urllib.parse.quote_plus(awkward) not in str(caught.value)


class TestAuditFindings20260811:
    """Client-side findings from the adversarial audit, pinned."""

    def test_redirects_are_refused_not_followed(self) -> None:
        """THE HIGH FINDING. urllib's default opener follows redirects across
        hosts and carries request headers with it -- one vendor-controlled 302
        would hand the X-API-Key to any server the vendor named. The shared
        opener's redirect handler must return None, which surfaces the 3xx as a
        terminal HTTPError instead of a followed hop."""
        from newsdesk.clients.base import _OPENER, _RefuseRedirects

        handlers = [type(h).__name__ for h in _OPENER.handlers]
        assert "_RefuseRedirects" in handlers
        refused = _RefuseRedirects().redirect_request(
            None, None, 302, "Found", {}, "https://collector.example/steal"
        )
        assert refused is None

    def test_a_redirect_status_fails_closed_with_no_hop(self) -> None:
        client = StubMarketaux([http_error(302)])
        with pytest.raises(VendorResponseError, match="HTTP 302"):
            client._get_json("/v1/news/all", {"api_token": SECRET})
        # Exactly one request was attempted; nothing followed the Location.
        assert len(client.urls) == 1

    def test_the_vendor_error_code_is_scrubbed_like_the_message(self) -> None:
        """_error_detail scrubbed `message` but appended `code` raw -- both are
        vendor-controlled bytes."""
        body = json.dumps({"error": {"code": f"bad-{SECRET}", "message": "x"}}).encode()
        client = StubMarketaux([http_error(401, body)])
        with pytest.raises(VendorResponseError) as caught:
            client._get_json("/v1/news/all", {"api_token": SECRET})
        assert SECRET not in str(caught.value)

    def test_the_raw_page_is_scrubbed_before_anything_downstream(self) -> None:
        """A vendor echoing the request URL into a response body would persist
        the query-string token in the raw-payload archive FOREVER. The scrub
        happens at the transport boundary, so the archive can never see it."""
        echoed = json.dumps(
            {
                "meta": {"found": 0, "returned": 0, "limit": 3, "page": 1},
                "data": [],
                "debug_request": f"https://api.marketaux.com/v1/news/all?api_token={SECRET}",
            }
        ).encode()
        client = StubMarketaux([echoed])
        (page,) = list(client.news_pages(["SPY"], published_after="2026-08-01"))
        assert SECRET.encode() not in page.raw
        assert b"<redacted>" in page.raw

    def test_dot_only_tickers_are_refused(self) -> None:
        """'.' and '..' matched the old [A-Z0-9.]{1,12} pattern. A '..' path
        segment has no business existing in a request this package builds,
        exploitable or not."""
        import datetime as dt

        client = StubAdanos([])
        for hostile in (".", "..", "...", "A.", ".A"):
            with pytest.raises(UntrustedContentError, match="implausible ticker"):
                list(
                    client.news_mention_pages(
                        hostile, start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 10)
                    )
                )
        assert client.urls == []

    def test_real_dotted_tickers_still_pass(self) -> None:
        from newsdesk.clients.adanos import _TICKER_RE

        for real in ("BRK.B", "BF.B", "SPY", "A"):
            assert _TICKER_RE.fullmatch(real), f"{real} should be a valid ticker"

    def test_a_boolean_published_at_is_rejected_not_epoch_converted(self) -> None:
        """JSON true is a Python bool is a Python int: without the gate it
        became an article dated 1970-01-01T00:00:01Z."""
        row = TestAdanos.mention(published_at=True)
        _, rejected, _ = parse_adanos_mentions({"results": [row]})
        assert rejected == 1


class TestAlpacaNews:
    def test_cursor_pagination(self) -> None:
        first = {"news": [alpaca_article()], "next_page_token": "next-token"}
        second = {"news": [alpaca_article(identifier=102)], "next_page_token": None}
        client = StubAlpaca([json.dumps(first).encode(), json.dumps(second).encode()])
        pages = list(client.news_pages(["XOM", "LMT"], start="2026-09-01", end="2026-09-02"))
        assert [page.number for page in pages] == [1, 2]
        assert "symbols=XOM%2CLMT" in client.urls[0]
        assert "page_token=next-token" in client.urls[1]

    def test_repeated_cursor_fails_closed(self) -> None:
        page = {"news": [alpaca_article()], "next_page_token": "same"}
        client = StubAlpaca([json.dumps(page).encode(), json.dumps(page).encode()])
        with pytest.raises(IngestionIncompleteError, match="repeated"):
            list(client.news_pages(["XOM"], start="2026-09-01", end="2026-09-02"))

    def test_parse_normalizes_timestamp_and_symbol_tags(self) -> None:
        records, rejected, reported = parse_alpaca_news_page({"news": [alpaca_article()]})
        assert rejected == 0
        assert reported is None
        assert records[0].published_at == "2026-09-01T13:15:00.000Z"
        assert records[0].symbols == {"XOM": (None, None)}

    def test_both_header_credentials_are_scrubbed_from_archived_bytes(self) -> None:
        reflected = json.dumps(
            {"news": [], "reflection": "test-key and test-secret", "next_page_token": None}
        ).encode()
        client = StubAlpaca([reflected])
        page = next(client.news_pages([], start="2026-09-01", end="2026-09-02"))
        assert b"test-key" not in page.raw
        assert b"test-secret" not in page.raw
