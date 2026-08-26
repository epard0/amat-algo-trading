"""Ingestion runs: the coverage record must never flatter the pull.

Every test here is about one property: whatever happens mid-ingestion, the run
table ends up telling the truth about it. "No articles that day", "the vendor
broke on page three", "two rows would not parse", and "the plan does not cover
this endpoint" are four different facts, and a feature series built later can
only be trusted if the store can still distinguish them.
"""

from __future__ import annotations

import json

import pytest

# Sibling test module: pytest's rootdir insertion makes it importable by name
# (there is no tests/__init__.py, deliberately).
from test_newsdesk_clients import StubMarketaux, marketaux_article, marketaux_page

from newsdesk.clients.base import FetchedPage
from newsdesk.errors import VendorResponseError
from newsdesk.ingest import _run_ingestion, ingest_marketaux
from newsdesk.store import NewsStore


@pytest.fixture
def store(tmp_path):
    with NewsStore(tmp_path / "news.sqlite3") as opened:
        yield opened


def _client_with(pages: list[dict]) -> StubMarketaux:
    return StubMarketaux([json.dumps(page).encode() for page in pages])


class TestACompleteRun:
    def test_pages_are_archived_parsed_stored_and_settled(self, store) -> None:
        pages = [
            marketaux_page(
                [
                    marketaux_article(uuid=f"u{i}", url=f"https://example.test/{i}")
                    for i in range(3)
                ],
                found=5,
                limit=3,
                page=1,
            ),
            marketaux_page(
                [
                    marketaux_article(uuid=f"v{i}", url=f"https://example.test/x{i}")
                    for i in range(2)
                ],
                found=5,
                limit=3,
                page=2,
            ),
        ]
        outcome = ingest_marketaux(
            store, _client_with(pages), symbols=["SPY"], published_after="2026-08-01"
        )
        assert outcome.status == "complete"
        assert outcome.pages == 2
        assert outcome.articles_parsed == 5
        assert outcome.versions_written == 5
        assert outcome.rows_reported == 5

        (run,) = store.coverage()
        assert run["status"] == "complete"
        assert run["pages_fetched"] == 2
        assert run["rows_stored"] == 5

        raw = store._connection.execute("SELECT COUNT(*) AS n FROM raw_payload").fetchone()
        assert raw["n"] == 2

    def test_an_empty_window_settles_complete_with_zero_rows(self, store) -> None:
        """ "No articles" is an answer, and it must be distinguishable from
        "never asked" forever."""
        outcome = ingest_marketaux(
            store,
            _client_with([marketaux_page([], found=0, limit=3, page=1)]),
            symbols=["SPY"],
            published_after="2026-08-01",
        )
        assert outcome.status == "complete"
        assert outcome.versions_written == 0
        (run,) = store.coverage()
        assert run["status"] == "complete"

    def test_re_running_the_same_window_writes_nothing_new(self, store) -> None:
        """Overlapping windows are the policy (boundary semantics are
        undocumented), so idempotent re-pulls are load-bearing, not a
        nicety."""
        pages = [marketaux_page([marketaux_article()], found=1, limit=3, page=1)]
        first = ingest_marketaux(
            store, _client_with(pages), symbols=["SPY"], published_after="2026-08-01"
        )
        second = ingest_marketaux(
            store, _client_with(pages), symbols=["SPY"], published_after="2026-08-01"
        )
        assert first.versions_written == 1
        assert second.versions_written == 0
        assert second.status == "complete"


class TestAFailedRun:
    def test_an_error_before_any_page_settles_failed(self, store) -> None:
        client = StubMarketaux([b'{"data": []}'])  # no meta -> schema failure
        with pytest.raises(VendorResponseError):
            ingest_marketaux(store, client, symbols=["SPY"], published_after="2026-08-01")
        (run,) = store.coverage()
        assert run["status"] == "failed"
        assert "VendorResponseError" in run["failure_reason"]

    def test_an_error_after_a_stored_page_settles_incomplete(self, store) -> None:
        """The dangerous half-state: real rows are in the store but the window
        is not whole. 'incomplete' warns that the window needs re-pulling;
        'failed' would invite treating the stored half as absent."""
        good = marketaux_page([marketaux_article()] * 3, found=6, limit=3, page=1)
        client = StubMarketaux([json.dumps(good).encode(), b'{"data": []}'])
        with pytest.raises(VendorResponseError):
            ingest_marketaux(store, client, symbols=["SPY"], published_after="2026-08-01")
        (run,) = store.coverage()
        assert run["status"] == "incomplete"
        assert run["pages_fetched"] == 1
        assert run["rows_stored"] >= 1

    def test_the_failure_reason_is_recorded_and_bounded(self, store) -> None:
        client = StubMarketaux([b'{"data": []}'])
        with pytest.raises(VendorResponseError):
            ingest_marketaux(store, client, symbols=["SPY"], published_after="2026-08-01")
        (run,) = store.coverage()
        assert len(run["failure_reason"]) <= 450


class TestRejectedRowsAreNeverSilent:
    def test_a_partially_unparseable_page_settles_incomplete(self, store) -> None:
        """Two of three rows parse. The two are kept -- discarding good rows for
        bad neighbours is its own loss -- but the run must not claim the window
        is whole, and the reason must say where the rest went."""
        bad = marketaux_article(uuid="bad")
        bad["published_at"] = "not-a-date"
        page = marketaux_page([marketaux_article(), bad], found=2, limit=3, page=1)
        outcome = ingest_marketaux(
            store, _client_with([page]), symbols=["SPY"], published_after="2026-08-01"
        )
        assert outcome.status == "incomplete"
        assert outcome.rejected == 1
        assert outcome.versions_written == 1
        (run,) = store.coverage()
        assert run["status"] == "incomplete"
        assert "raw pages retain them" in run["failure_reason"]

    def test_the_rejected_rows_survive_in_the_archive(self, store) -> None:
        """Archive before parse: a parser bug must never cost the evidence
        needed to fix it."""
        bad = marketaux_article(uuid="bad")
        bad["published_at"] = "not-a-date"
        page = marketaux_page([bad], found=1, limit=3, page=1)
        ingest_marketaux(store, _client_with([page]), symbols=["SPY"], published_after="2026-08-01")
        (row,) = store._connection.execute("SELECT body FROM raw_payload").fetchall()
        assert b"not-a-date" in row["body"]


class TestTheIngestionClockIsOurs:
    def test_ingest_never_supplies_ingested_at(self) -> None:
        """The store's override parameter exists for tests that construct
        historical timelines. Production ingestion must always take the store's
        own clock -- a supplied value would let an ingest backdate knowledge,
        which is the one lie an append-only store cannot survive. Asserted
        against the source so a future edit cannot quietly add it."""
        import ast
        import inspect

        import newsdesk.ingest as module

        tree = ast.parse(inspect.getsource(module))
        offenders = [
            keyword.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "ingested_at"
        ]
        assert not offenders, "ingest.py passes ingested_at; production must use the store clock"

    def test_stored_rows_carry_the_store_clock_not_the_vendors(self, store) -> None:
        page = marketaux_page(
            [marketaux_article(published_at="2020-01-02T09:00:00.000000Z")],
            found=1,
            limit=3,
            page=1,
        )
        ingest_marketaux(store, _client_with([page]), symbols=["SPY"], published_after="2020-01-01")
        (row,) = store._connection.execute(
            "SELECT published_at, ingested_at FROM article_version"
        ).fetchall()
        assert row["published_at"] == "2020-01-02T09:00:00.000Z"
        # Ingested today, published 2020: a backfilled article is knowable only
        # from now on, which is precisely the point-in-time property.
        assert row["ingested_at"] > "2026-01-01T00:00:00.000Z"


class TestTheRunHelperIsProviderAgnostic:
    def test_a_parser_exception_still_settles_the_run(self, store) -> None:
        def exploding_parser(_payload):
            message = "parser defect"
            raise VendorResponseError(message)

        pages = iter([FetchedPage(number=1, payload={"data": []}, raw=b"{}")])
        with pytest.raises(VendorResponseError, match="parser defect"):
            _run_ingestion(
                store,
                provider="testwire",
                host="example.test",
                pages=pages,
                parser=exploding_parser,
                window_start=None,
                window_end=None,
                symbols=("SPY",),
            )
        (run,) = store.coverage()
        assert run["status"] == "incomplete"


class TestAuditFindings20260811:
    def test_a_first_page_archive_failure_settles_failed_not_incomplete(self, store) -> None:
        """The status pivot was on pages FETCHED; a page fetched but never
        persisted contributed nothing recoverable, and 'incomplete' would
        contradict the module's own definition of 'failed'."""
        page = marketaux_page([marketaux_article()], found=1, limit=3, page=1)
        client = _client_with([page])

        def exploding_archive(_run_id, _page_number, _body):
            message = "disk full"
            raise OSError(message)

        store.archive_payload = exploding_archive  # type: ignore[method-assign]
        with pytest.raises(OSError, match="disk full"):
            ingest_marketaux(store, client, symbols=["SPY"], published_after="2026-08-01")
        (run,) = store.coverage()
        assert run["status"] == "failed"


class TestTheCircuitBreaker:
    """Three consecutive failed runs suspend a provider.

    Both vendors meter by quota -- Marketaux by the day, Adanos by the month --
    and a run failing for a standing reason (revoked key, wrong tier, exhausted
    plan) fails identically on every retry. The breaker converts "keeps
    failing" into "stops asking" before any request is built.
    """

    @staticmethod
    def _fail_once(store) -> None:
        client = StubMarketaux([b'{"data": []}'])  # no meta -> schema failure
        with pytest.raises(VendorResponseError):
            ingest_marketaux(store, client, symbols=["SPY"], published_after="2026-08-01")

    def test_three_failures_suspend_the_fourth_attempt(self, store) -> None:
        from newsdesk.errors import ProviderSuspendedError

        for _ in range(3):
            self._fail_once(store)
        client = StubMarketaux([b'{"data": []}'])
        with pytest.raises(ProviderSuspendedError, match="suspended"):
            ingest_marketaux(store, client, symbols=["SPY"], published_after="2026-08-01")
        # Refused before any request was built: zero quota spent, no new run row.
        assert client.urls == []
        assert len(store.coverage()) == 3

    def test_two_failures_do_not_suspend(self, store) -> None:
        for _ in range(2):
            self._fail_once(store)
        page = marketaux_page([marketaux_article()], found=1, limit=3, page=1)
        outcome = ingest_marketaux(
            store, _client_with([page]), symbols=["SPY"], published_after="2026-08-01"
        )
        assert outcome.status == "complete"

    def test_force_bypasses_and_a_success_resets_the_streak(self, store) -> None:
        for _ in range(3):
            self._fail_once(store)
        page = marketaux_page([marketaux_article()], found=1, limit=3, page=1)
        outcome = ingest_marketaux(
            store,
            _client_with([page]),
            symbols=["SPY"],
            published_after="2026-08-01",
            force=True,
        )
        assert outcome.status == "complete"
        # The success reset the streak; the next attempt needs no force.
        second = marketaux_page(
            [marketaux_article(url="https://example.test/fresh")], found=1, limit=3, page=1
        )
        assert (
            ingest_marketaux(
                store, _client_with([second]), symbols=["SPY"], published_after="2026-08-02"
            ).status
            == "complete"
        )
        # And a genuinely different provider was never affected.
        adanos_id = store.register_provider("adanos", "api.adanos.org")
        assert store.consecutive_failures(adanos_id) == 0

    def test_an_incomplete_run_resets_the_streak_because_data_flowed(self, store) -> None:
        for _ in range(2):
            self._fail_once(store)
        bad = marketaux_article(uuid="bad")
        bad["published_at"] = "not-a-date"
        page = marketaux_page([marketaux_article(), bad], found=2, limit=3, page=1)
        outcome = ingest_marketaux(
            store, _client_with([page]), symbols=["SPY"], published_after="2026-08-01"
        )
        assert outcome.status == "incomplete"
        provider_id = store.register_provider("marketaux", "api.marketaux.com")
        assert store.consecutive_failures(provider_id) == 0
