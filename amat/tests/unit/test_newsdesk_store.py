"""The news store's two load-bearing properties: append-only, and point-in-time.

Neither is testable by inspection. "Append-only" is a claim about what the
database refuses, and "point-in-time" is a claim about what a query does *not*
return -- both of which look identical to a working system right up until they
are wrong, at which point every news-derived feature is quietly contaminated.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from newsdesk.errors import ConfigurationError, UntrustedContentError
from newsdesk.store import ArticleRecord, NewsStore, normalize_text, to_utc_z


@pytest.fixture
def store(tmp_path):
    with NewsStore(tmp_path / "news.sqlite3") as opened:
        yield opened


@pytest.fixture
def provider(store):
    return store.register_provider("testwire", "example.test", tier="test")


def article(**overrides) -> ArticleRecord:
    payload = {
        "vendor_uuid": "v-1",
        "published_at": "2026-08-07T13:30:00.000Z",
        "title": "Widgets Inc beats expectations",
        "url": "https://example.test/widgets-beat",
        "source_domain": "example.test",
        "language": "en",
        "snippet": "Revenue rose.",
        "symbols": {"SPY": (0.8, 0.4)},
    }
    payload.update(overrides)
    return ArticleRecord(**payload)


class TestAppendOnlyIsEnforcedByTheDatabase:
    """Application discipline is not a control. These assert the triggers."""

    def test_an_update_to_a_stored_version_is_refused(self, store, provider) -> None:
        store.store([article()], provider_id=provider, run_id=store.open_run(provider))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute("UPDATE article_version SET title = 'rewritten'")

    def test_a_delete_of_a_stored_version_is_refused(self, store, provider) -> None:
        store.store([article()], provider_id=provider, run_id=store.open_run(provider))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute("DELETE FROM article_version")

    def test_the_raw_payload_archive_cannot_be_edited(self, store, provider) -> None:
        run = store.open_run(provider)
        store.archive_payload(run, 1, b'{"data": []}')
        with pytest.raises(sqlite3.IntegrityError, match="never edited"):
            store._connection.execute("UPDATE raw_payload SET body = X'00'")

    def test_a_settled_run_cannot_be_settled_again(self, store, provider) -> None:
        run = store.open_run(provider)
        store.settle_run(run, status="complete", rows_stored=1)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.settle_run(run, status="failed")


class TestIdempotence:
    def test_re_ingesting_the_same_article_writes_nothing_new(self, store, provider) -> None:
        """A failed pull must be safe to retry; the alternative to retrying is a
        coverage hole."""
        run = store.open_run(provider)
        assert store.store([article()], provider_id=provider, run_id=run) == 1
        assert store.store([article()], provider_id=provider, run_id=run) == 0

    def test_a_revised_headline_appends_a_second_version(self, store, provider) -> None:
        run = store.open_run(provider)
        store.store([article()], provider_id=provider, run_id=run)
        store.store(
            [article(title="Widgets Inc beats expectations, raises guidance")],
            provider_id=provider,
            run_id=run,
        )
        rows = store._connection.execute("SELECT COUNT(*) AS n FROM article_version").fetchone()
        assert rows["n"] == 2

    def test_the_same_story_from_two_urls_query_strings_is_one_article(
        self, store, provider
    ) -> None:
        """Tracking parameters must not split one story into two, because an
        article count is a feature and double-counting inflates it."""
        run = store.open_run(provider)
        store.store([article()], provider_id=provider, run_id=run)
        store.store(
            [article(url="https://example.test/widgets-beat?utm_source=newsletter")],
            provider_id=provider,
            run_id=run,
        )
        rows = store._connection.execute("SELECT COUNT(*) AS n FROM article").fetchone()
        assert rows["n"] == 1


class TestThePointInTimeQuery:
    def test_an_article_ingested_later_is_invisible_earlier(self, store, provider) -> None:
        """The whole point. A backtest at T must not see what arrived at T+1."""
        run = store.open_run(provider)
        store.store(
            [article()],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-08T12:00:00.000Z",
        )
        assert store.knowable_at("2026-08-08T11:59:59.000Z") == []
        assert len(store.knowable_at("2026-08-08T12:00:01.000Z")) == 1

    def test_a_backfilled_article_is_filtered_on_ingestion_not_publication(
        self, store, provider
    ) -> None:
        """The failure this store exists to prevent.

        An archive pull returns a story *published* in 2019 but *observed* today.
        Filtering on ``published_at`` would make it visible to a 2019 decision,
        which is a backtest trading on news nobody had. The article is stamped
        with a 2019 publication and a 2026 ingestion; a 2019 query must not
        return it.
        """
        run = store.open_run(provider)
        store.store(
            [article(published_at="2019-03-04T14:00:00.000Z")],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-10T09:00:00.000Z",
        )
        assert store.knowable_at("2019-03-05T00:00:00.000Z") == []
        assert len(store.knowable_at("2026-08-11T00:00:00.000Z")) == 1

    def test_only_the_version_current_at_the_queried_instant_is_returned(
        self, store, provider
    ) -> None:
        run = store.open_run(provider)
        store.store(
            [article()], provider_id=provider, run_id=run, ingested_at="2026-08-08T12:00:00.000Z"
        )
        store.store(
            [article(title="Revised headline")],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-09T12:00:00.000Z",
        )
        before = store.knowable_at("2026-08-08T18:00:00.000Z")
        after = store.knowable_at("2026-08-10T00:00:00.000Z")
        assert before[0].title == "Widgets Inc beats expectations"
        assert after[0].title == "Revised headline"

    def test_a_withdrawal_hides_the_story_without_resurrecting_its_old_version(
        self, store, provider
    ) -> None:
        """The bug this ordering exists to avoid: filtering lifecycle *before*
        picking the latest version would drop the withdrawal row and hand back
        the last pre-withdrawal version, reporting a retracted story as live."""
        run = store.open_run(provider)
        store.store(
            [article()], provider_id=provider, run_id=run, ingested_at="2026-08-08T12:00:00.000Z"
        )
        store.store(
            [article(lifecycle="withdrawn")],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-09T12:00:00.000Z",
        )
        assert len(store.knowable_at("2026-08-08T18:00:00.000Z")) == 1
        assert store.knowable_at("2026-08-10T00:00:00.000Z") == []

    def test_the_symbol_filter_matches_the_versions_own_tags(self, store, provider) -> None:
        run = store.open_run(provider)
        store.store([article()], provider_id=provider, run_id=run)
        assert len(store.knowable_at("2030-01-01T00:00:00.000Z", symbol="SPY")) == 1
        assert store.knowable_at("2030-01-01T00:00:00.000Z", symbol="TLT") == []


class TestUntrustedContent:
    def test_an_oversized_title_is_refused(self, store, provider) -> None:
        with pytest.raises(UntrustedContentError, match="cap"):
            store.store(
                [article(title="x" * 5000)], provider_id=provider, run_id=store.open_run(provider)
            )

    def test_an_implausible_ticker_is_refused(self, store, provider) -> None:
        with pytest.raises(UntrustedContentError, match="implausible ticker"):
            store.store(
                [article(symbols={"'; DROP TABLE article; --": (None, None)})],
                provider_id=provider,
                run_id=store.open_run(provider),
            )

    def test_a_quote_in_a_headline_is_stored_as_data(self, store, provider) -> None:
        """Parameterized queries throughout, so SQL syntax in vendor text is
        content rather than code."""
        hostile = "Analyst says '); DROP TABLE article_version; -- is overvalued"
        store.store([article(title=hostile)], provider_id=provider, run_id=store.open_run(provider))
        found = store.knowable_at("2030-01-01T00:00:00.000Z")
        assert found[0].title == hostile
        assert (
            store._connection.execute("SELECT COUNT(*) AS n FROM article_version").fetchone()["n"]
            == 1
        )

    def test_unicode_variants_of_one_headline_normalize_together(self) -> None:
        """Written as escapes on purpose: the confusable characters are the
        subject of the test, and as literals they are invisible in review.

        ``\uff37`` is FULLWIDTH LATIN CAPITAL W and ``\u00a0`` is NO-BREAK
        SPACE. Two vendors rendering one headline this way must dedupe to a
        single article rather than count as two.
        """
        exotic = "\uff37idgets\u00a0rise"
        assert normalize_text(exotic, limit=100) == normalize_text("Widgets rise", limit=100)

    def test_control_characters_are_stripped(self) -> None:
        assert normalize_text("clean\x00head\x07line", limit=100) == "cleanheadline"


class TestTimestamps:
    def test_a_naive_datetime_is_refused(self) -> None:
        """A naive timestamp shifts articles across session boundaries silently."""
        with pytest.raises(ConfigurationError, match="timezone"):
            to_utc_z(dt.datetime(2026, 8, 7, 13, 30))  # noqa: DTZ001 - the point of the test

    def test_an_offset_timestamp_is_normalized_to_z(self) -> None:
        eastern = dt.timezone(dt.timedelta(hours=-4))
        assert to_utc_z(dt.datetime(2026, 8, 7, 9, 30, tzinfo=eastern)).startswith(
            "2026-08-07T13:30:00"
        )
        assert to_utc_z(dt.datetime(2026, 8, 7, 9, 30, tzinfo=eastern)).endswith("Z")


class TestCoverageIsRecorded:
    def test_a_failed_run_is_kept_not_discarded(self, store, provider) -> None:
        """ "No articles that day" and "we never successfully asked" must stay
        distinguishable forever."""
        run = store.open_run(provider, window_start="2026-08-07", window_end="2026-08-07")
        store.settle_run(run, status="failed", failure_reason="HTTP 503 after 5 attempts")
        (record,) = store.coverage()
        assert record["status"] == "failed"
        assert record["failure_reason"] == "HTTP 503 after 5 attempts"


class TestCanonicalTimestamps:
    """One fixed-width shape, because SQLite compares these columns as text.

    Measured 2026-08-11 in SQLite directly: '...00.123Z' sorts *after*
    '...00.1234Z' though 0.123s < 0.1234s, and '...00.000000Z' compares unequal
    to '...00.000Z' for the same instant. Marketaux emits six fractional
    digits, Adanos emits offsets and epochs, this store writes three digits --
    so without one enforced width, every vendor mix silently corrupts the
    point-in-time filter.
    """

    def test_the_ordering_hazard_is_real(self) -> None:
        """The measurement itself, pinned, so the enforcement cannot be
        'simplified' away by someone who does not believe it."""
        import sqlite3 as sq

        connection = sq.connect(":memory:")
        wrong = connection.execute(
            "SELECT '2026-08-10T01:24:00.123Z' <= '2026-08-10T01:24:00.1234Z'"
        ).fetchone()[0]
        assert wrong == 0, "text ordering of mixed precision was expected to be broken"
        unequal = connection.execute(
            "SELECT '2026-08-10T01:24:00.000000Z' = '2026-08-10T01:24:00.000Z'"
        ).fetchone()[0]
        assert unequal == 0

    def test_a_six_digit_vendor_stamp_normalizes_to_canonical(self) -> None:
        from newsdesk.store import canonical_timestamp

        assert canonical_timestamp("2024-11-08T01:24:00.000000Z") == "2024-11-08T01:24:00.000Z"

    def test_an_offset_stamp_normalizes_to_utc_z(self) -> None:
        from newsdesk.store import canonical_timestamp

        assert canonical_timestamp("2026-03-05T12:30:00+00:00") == "2026-03-05T12:30:00.000Z"
        assert canonical_timestamp("2026-03-05T08:30:00-04:00") == "2026-03-05T12:30:00.000Z"

    def test_a_unix_epoch_normalizes(self) -> None:
        from newsdesk.store import canonical_timestamp

        assert canonical_timestamp(1754834551) == "2025-08-10T14:02:31.000Z"

    def test_a_milliseconds_epoch_is_a_unit_error_not_a_date(self) -> None:
        from newsdesk.store import canonical_timestamp

        with pytest.raises(UntrustedContentError, match="plausible range"):
            canonical_timestamp(1754834551000)

    def test_a_naive_stamp_is_refused_not_guessed(self) -> None:
        from newsdesk.store import canonical_timestamp

        with pytest.raises(UntrustedContentError, match="no timezone"):
            canonical_timestamp("2026-08-10T14:00:00")

    def test_the_store_refuses_a_non_canonical_published_at(self, store, provider) -> None:
        from newsdesk.errors import StoreIntegrityError

        with pytest.raises(StoreIntegrityError, match="canonical"):
            store.store(
                [article(published_at="2026-08-07T13:30:00.000000Z")],
                provider_id=provider,
                run_id=store.open_run(provider),
            )

    def test_the_query_refuses_a_non_canonical_as_of(self, store) -> None:
        from newsdesk.errors import StoreIntegrityError

        with pytest.raises(StoreIntegrityError, match="canonical"):
            store.knowable_at("2026-08-08T12:00:00Z")

    def test_the_schema_itself_refuses_an_off_shape_stamp(self, store, provider) -> None:
        """Defence in depth: a writer bypassing the Python API meets the GLOB
        constraint. The earlier LIKE pattern admitted any precision."""
        run = store.open_run(provider)
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute(
                "INSERT INTO article(dedup_key, first_seen_at)"
                " VALUES ('k1', '2026-01-01T00:00:00.000Z')"
            )
            store._connection.execute(
                "INSERT INTO article_version(article_id, provider_id, run_id, published_at,"
                " ingested_at, title, url, content_sha256)"
                " VALUES (1, ?, ?, '2026-08-07T13:30:00.000000Z',"
                " '2026-08-07T13:30:00.000Z', 't', 'https://example.test', 'x')",
                (provider, run),
            )


class TestAuditFindings20260811:
    """Each confirmed finding from the adversarial audit, pinned.

    The verification half of the audit workflow died on a session limit, so
    every finding below was re-confirmed by hand against the code before being
    fixed. A finding without a regression test is a finding that returns.
    """

    def test_symbol_filter_does_not_resurrect_a_superseded_version(self, store, provider) -> None:
        """THE HIGH FINDING. v1 is tagged SPY; v2 revises the article and drops
        the tag. Filtering the recency CTE by symbol ranked only v1 -- the
        superseded version -- as current and served it. The symbol filter must
        interrogate the *winner*, never shape the ranking."""
        run = store.open_run(provider)
        store.store(
            [article()],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-08T12:00:00.000Z",
        )
        store.store(
            [article(title="Retagged: no longer about SPY", symbols=None)],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-09T12:00:00.000Z",
        )
        after = store.knowable_at("2026-08-10T00:00:00.000Z", symbol="SPY")
        assert after == [], "a superseded version was resurrected by the symbol filter"
        # Before the re-tag, the SPY-tagged version was genuinely current:
        before = store.knowable_at("2026-08-08T18:00:00.000Z", symbol="SPY")
        assert len(before) == 1

    def test_symbol_filter_does_not_resurrect_a_withdrawn_article(self, store, provider) -> None:
        run = store.open_run(provider)
        store.store(
            [article()],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-08T12:00:00.000Z",
        )
        store.store(
            [article(lifecycle="withdrawn", symbols=None)],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-09T12:00:00.000Z",
        )
        assert store.knowable_at("2026-08-10T00:00:00.000Z", symbol="SPY") == []

    def test_count_knowable_at_is_exact_not_saturated(self, store, provider) -> None:
        """len() of a limited query saturates at the limit and the saturated
        number reads as a population. deskreview journaled exactly that."""
        run = store.open_run(provider)
        records = [article(url=f"https://example.test/story-{index}") for index in range(7)]
        store.store(records, provider_id=provider, run_id=run)
        assert len(store.knowable_at("2030-01-01T00:00:00.000Z", limit=3)) == 3
        assert store.count_knowable_at("2030-01-01T00:00:00.000Z") == 7

    def test_distinct_query_routed_articles_stay_distinct(self, store, provider) -> None:
        """?id=101 and ?id=202 are different stories on a query-routed CMS. The
        first dedup_key stripped the whole query string and merged them -- the
        second silently became a 'revision' of the first, hidden forever."""
        run = store.open_run(provider)
        store.store(
            [
                article(url="https://example.test/article?id=101", title="Story one"),
                article(url="https://example.test/article?id=202", title="Story two"),
            ],
            provider_id=provider,
            run_id=run,
        )
        assert store.count_knowable_at("2030-01-01T00:00:00.000Z") == 2

    def test_tracking_parameters_still_collapse(self, store, provider) -> None:
        run = store.open_run(provider)
        store.store(
            [
                article(url="https://example.test/story?id=7&utm_source=a"),
                article(url="https://example.test/story?utm_campaign=b&id=7"),
            ],
            provider_id=provider,
            run_id=run,
        )
        assert store.count_knowable_at("2030-01-01T00:00:00.000Z") == 1

    def test_withdraw_then_reinstate_is_recordable(self, store, provider) -> None:
        """The global-unique (article, provider, digest) index swallowed every
        A->B->A sequence: reinstating the original content after a withdrawal
        hit the old digest and was ignored, leaving the article withdrawn
        forever. Idempotency now compares against the LATEST version only."""
        run = store.open_run(provider)
        store.store(
            [article()],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-08T12:00:00.000Z",
        )
        store.store(
            [article(lifecycle="withdrawn")],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-09T12:00:00.000Z",
        )
        assert store.knowable_at("2026-08-09T13:00:00.000Z") == []
        reinstated = store.store(
            [article()],
            provider_id=provider,
            run_id=run,
            ingested_at="2026-08-10T12:00:00.000Z",
        )
        assert reinstated == 1, "the reinstatement was swallowed by historical idempotency"
        assert len(store.knowable_at("2026-08-10T13:00:00.000Z")) == 1

    def test_consecutive_reserves_still_collapse(self, store, provider) -> None:
        run = store.open_run(provider)
        assert store.store([article()], provider_id=provider, run_id=run) == 1
        assert store.store([article()], provider_id=provider, run_id=run) == 0

    def test_a_clock_step_back_cannot_reorder_ingestion(self, store, provider) -> None:
        """utc_now() is wall-clock. A backward step between two ingests would
        give the LATER version an EARLIER ingested_at, and the recency window
        would serve the stale version forever. The auto-stamp path clamps to
        the store's high-water mark."""
        run = store.open_run(provider)
        future = "2030-06-01T00:00:00.000Z"
        store.store([article()], provider_id=provider, run_id=run, ingested_at=future)
        # Auto-stamped now, which is far before the explicit future stamp:
        store.store(
            [article(title="Revision after the clock 'stepped back'")],
            provider_id=provider,
            run_id=run,
        )
        rows = store._connection.execute(
            "SELECT ingested_at FROM article_version ORDER BY version_id"
        ).fetchall()
        assert rows[1]["ingested_at"] >= rows[0]["ingested_at"]
        (current,) = store.knowable_at("2031-01-01T00:00:00.000Z")
        assert "Revision" in current.title

    def test_a_calendar_impossible_stamp_is_refused(self) -> None:
        """2026-02-31 has the right digits and no place on any calendar."""
        from newsdesk.errors import StoreIntegrityError
        from newsdesk.store import _require_canonical

        with pytest.raises(StoreIntegrityError, match="not a real instant"):
            _require_canonical("published_at", "2026-02-31T12:00:00.000Z")
        with pytest.raises(StoreIntegrityError, match="not a real instant"):
            _require_canonical("published_at", "2026-08-10T99:00:00.000Z")

    def test_a_boolean_is_not_a_timestamp(self) -> None:
        """bool subclasses int; JSON true would otherwise become an article
        dated one second after the epoch."""
        from newsdesk.store import canonical_timestamp

        with pytest.raises(UntrustedContentError, match="boolean"):
            canonical_timestamp(True)

    def test_settle_run_refuses_a_nonexistent_run(self, store) -> None:
        from newsdesk.errors import StoreIntegrityError

        with pytest.raises(StoreIntegrityError, match="does not exist"):
            store.settle_run(999, status="complete")

    def test_the_article_and_provider_tables_are_append_only_too(self, store, provider) -> None:
        """The schema header claimed everything was trigger-protected; article
        and provider were not, so a direct writer could rewrite a dedup_key or
        a provider name and silently re-attribute history."""
        run = store.open_run(provider)
        store.store([article()], provider_id=provider, run_id=run)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute("UPDATE article SET dedup_key = 'rewritten'")
        with pytest.raises(sqlite3.IntegrityError, match="provenance"):
            store._connection.execute("UPDATE provider SET name = 'someone-else'")
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute("DELETE FROM article")

    def test_a_running_run_can_only_settle(self, store, provider) -> None:
        """While a run was 'running', ANY column could be rewritten -- including
        its window and provider, i.e. what the coverage record attests."""
        run = store.open_run(provider, window_start="2026-08-01")
        with pytest.raises(sqlite3.IntegrityError, match="identity fields are frozen"):
            store._connection.execute(
                "UPDATE ingestion_run SET query_window_start = '1999-01-01' WHERE run_id = ?",
                (run,),
            )
        store.settle_run(run, status="complete")
