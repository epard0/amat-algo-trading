"""The persistent news store: append-only writes and point-in-time reads.

Everything here goes through parameterized SQL. Article titles and snippets are
attacker-controllable strings from the open internet, and the one place a news
pipeline reliably gets compromised is a query built by concatenation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from newsdesk.errors import ConfigurationError, StoreIntegrityError, UntrustedContentError

__all__ = [
    "DEFAULT_DATABASE_PATH",
    "ArticleRecord",
    "NewsStore",
    "StoredArticle",
    "StoredMention",
    "canonical_timestamp",
    "normalize_text",
    "utc_now",
]

#: The store's one and only timestamp shape: millisecond precision, ``Z`` suffix.
#:
#: One *fixed-width* shape, not merely one timezone. SQLite compares these
#: columns as text, and mixed fractional precision breaks text ordering in both
#: directions -- measured directly on 2026-08-11:
#:
#: * ``...00.123Z`` sorts *after* ``...00.1234Z``, though 0.123s < 0.1234s;
#: * ``...00.000000Z`` and ``...00.000Z`` are the same instant and compare
#:   unequal.
#:
#: Marketaux stamps articles with six fractional digits, Adanos with an ISO
#: offset, and this module writes three digits -- so without a single enforced
#: width, every vendor mix silently corrupts the point-in-time filter. The
#: format is enforced three times: vendors' stamps pass through
#: :func:`canonical_timestamp` at parse time, this regex guards the Python API,
#: and the schema's GLOB constraints guard direct writers.
_CANONICAL_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")

#: 2100-01-01T00:00:00Z. An epoch outside (0, here) is a unit error -- vendor
#: milliseconds-since-epoch read as seconds, or vice versa -- not a date.
_MAX_EPOCH_SECONDS = 4_102_444_800.0

#: Query parameters that identify a *campaign*, never an article. Everything
#: else in the query string is kept: on query-routed CMSs the query string IS
#: the article identity.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset({"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref"})


def _is_tracking_parameter(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith(_TRACKING_PREFIXES) or lowered in _TRACKING_KEYS


#: Default location. Under ``data/news/`` rather than ``data/snapshots/``: the
#: snapshot tree is immutable checksum-addressed evidence and a mutable
#: database has no business there.
DEFAULT_DATABASE_PATH = Path("data/news/news.sqlite3")

_SCHEMA = Path(__file__).with_name("schema.sql")

#: Caps applied before anything reaches SQL. The schema repeats them as CHECK
#: constraints so a direct writer cannot bypass them either.
MAX_TITLE = 2000
MAX_SNIPPET = 20000
MAX_URL = 4000
MAX_SYMBOL = 12


def utc_now() -> str:
    """Current instant as ISO-8601 UTC with a ``Z`` suffix.

    One format everywhere. SQLite compares these as text, so a mixed
    representation -- ``+00:00`` here and ``Z`` there -- would silently break
    every range query rather than fail.
    """
    return dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def to_utc_z(moment: dt.datetime) -> str:
    """Render a timezone-aware datetime in the store's single format.

    Raises:
        ConfigurationError: on a naive datetime. A naive timestamp in a news
            store is a silent timezone bug that shifts articles across session
            boundaries, which is precisely the error a point-in-time filter
            cannot survive.
    """
    if moment.tzinfo is None:
        message = "refusing a naive datetime; news timestamps must carry a timezone"
        raise ConfigurationError(message)
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def canonical_timestamp(value: str | int | float) -> str:
    """Normalize a vendor timestamp into the store's single canonical shape.

    Accepts an ISO-8601 string carrying an explicit timezone (``Z`` or an
    offset, any fractional precision including Marketaux's six digits), or a
    Unix epoch in seconds (Adanos documents epoch on some mention rows). Returns
    the exact ``YYYY-MM-DDTHH:MM:SS.mmmZ`` shape and nothing else.

    Raises:
        UntrustedContentError: on a naive datetime (no timezone claim means no
            usable claim -- guessing one shifts articles across session
            boundaries), an unparseable string, or an epoch outside
            ``(0, 2100)`` -- the latter is how a milliseconds-for-seconds unit
            error surfaces, and it must surface here rather than as an article
            dated the year 55976.
    """
    if isinstance(value, bool):
        # bool is a subclass of int, so without this guard JSON `true` would
        # become 1970-01-01T00:00:01.000Z -- a stored article dated the epoch.
        message = "a boolean is not a timestamp"
        raise UntrustedContentError(message)
    if isinstance(value, int | float):
        seconds = float(value)
        if not math.isfinite(seconds) or not 0.0 < seconds < _MAX_EPOCH_SECONDS:
            message = f"epoch timestamp {value!r} is outside the plausible range"
            raise UntrustedContentError(message)
        return to_utc_z(dt.datetime.fromtimestamp(seconds, tz=dt.UTC))

    text = value.strip()
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        message = f"unparseable vendor timestamp {text[:64]!r}"
        raise UntrustedContentError(message) from exc
    if moment.tzinfo is None:
        message = f"vendor timestamp {text[:64]!r} carries no timezone; refusing to guess one"
        raise UntrustedContentError(message)
    return to_utc_z(moment)


def _require_canonical(name: str, stamp: str) -> str:
    """Refuse any timestamp not in the store's fixed-width canonical shape.

    Applied at the Python API so that a caller passing ``...T12:00:00Z`` (no
    fraction) or a six-digit vendor stamp is stopped before it can poison text
    ordering. The schema's GLOB constraints repeat this for direct writers.
    """
    if not _CANONICAL_STAMP.fullmatch(stamp):
        message = (
            f"{name} must be canonical 'YYYY-MM-DDTHH:MM:SS.mmmZ', received {stamp[:64]!r}; "
            "pass vendor stamps through canonical_timestamp() first -- mixed precision "
            "breaks the text ordering every point-in-time query depends on"
        )
        raise StoreIntegrityError(message)
    try:
        # The regex checks digits, not calendars: 2026-02-31 and hour 99 have
        # the right shape and would sort into places no real instant occupies.
        dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ")  # noqa: DTZ007 - shape already pins UTC
    except ValueError as exc:
        message = f"{name} {stamp!r} is shaped like a timestamp but is not a real instant"
        raise StoreIntegrityError(message) from exc
    return stamp


def normalize_text(value: str, *, limit: int) -> str:
    """Normalize untrusted vendor text to a comparable, bounded form.

    NFKC folding first, so that two vendors' renderings of the same headline --
    one with a non-breaking space or a fullwidth quote -- produce the same digest
    and deduplicate against each other. Control characters are stripped because
    they serve no purpose in a headline and do serve one in a log-injection.

    Raises:
        UntrustedContentError: if the value exceeds ``limit`` after folding.
    """
    folded = unicodedata.normalize("NFKC", value)
    cleaned = "".join(
        character
        for character in folded
        if character in "\t\n" or unicodedata.category(character)[0] != "C"
    ).strip()
    if len(cleaned) > limit:
        message = f"vendor text exceeds the {limit}-character cap at {len(cleaned)}"
        raise UntrustedContentError(message)
    return cleaned


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    """One article as a provider reported it, normalized but not yet stored."""

    vendor_uuid: str | None
    published_at: str
    title: str
    url: str
    source_domain: str | None = None
    language: str | None = None
    snippet: str | None = None
    #: symbol -> (relevance, sentiment); either may be ``None``.
    symbols: dict[str, tuple[float | None, float | None]] | None = None
    lifecycle: str = "active"

    def dedup_key(self) -> str:
        """Cross-provider identity for the same underlying story.

        Keyed on the normalized URL when there is one, because a syndicated wire
        story carries the same canonical link through every outlet that runs it,
        and on ``(published date, title)`` otherwise. Getting this wrong is not
        cosmetic in either direction: treating one story as two inflates every
        count feature, and treating two stories as one **hides an article
        forever** -- the second becomes a "revision" of the first.

        The query string is therefore kept, minus known tracking parameters.
        An earlier revision stripped the whole query string, which merged
        distinct articles on any query-routed CMS (``?id=101`` and ``?id=202``
        are different stories on the same path). Only the host is lowercased;
        paths are case-sensitive on enough servers that folding them would
        merge distinct pages. The scheme is dropped -- http and https serve the
        same article -- and remaining parameters are sorted so parameter order
        cannot split one story into two.
        """
        if self.url:
            split = urlsplit(self.url.strip())
            kept = sorted(
                f"{key}={value}"
                for key, value in parse_qsl(split.query, keep_blank_values=True)
                if not _is_tracking_parameter(key)
            )
            stem = split.netloc.lower() + split.path.rstrip("/")
            if kept:
                stem += "?" + "&".join(kept)
            return "url:" + hashlib.sha256(stem.encode("utf-8")).hexdigest()
        stem = f"{self.published_at[:10]}|{normalize_text(self.title, limit=MAX_TITLE).lower()}"
        return "title:" + hashlib.sha256(stem.encode("utf-8")).hexdigest()

    def content_sha256(self) -> str:
        """Digest of the fields a revision would change."""
        payload = json.dumps(
            {
                "title": normalize_text(self.title, limit=MAX_TITLE),
                "snippet": normalize_text(self.snippet or "", limit=MAX_SNIPPET),
                "url": self.url,
                "lifecycle": self.lifecycle,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredArticle:
    """One article version as it stood at a queried instant."""

    article_id: int
    version_id: int
    provider: str
    published_at: str
    ingested_at: str
    title: str
    url: str
    source_domain: str | None
    snippet: str | None
    lifecycle: str


@dataclass(frozen=True, slots=True)
class StoredMention:
    """One vendor-tagged symbol on the article version visible at an instant.

    Discovery reads these rows instead of scraping article text for ticker-like
    strings.  The title and snippet remain untrusted evidence; ``symbol`` and
    ``sentiment`` are the bounded fields the provider explicitly assigned.
    """

    article_id: int
    version_id: int
    provider: str
    published_at: str
    ingested_at: str
    title: str
    url: str
    snippet: str | None
    symbol: str
    sentiment: float | None


class NewsStore:
    """Append-only SQLite store for point-in-time news."""

    def __init__(self, path: Path | str = DEFAULT_DATABASE_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA.read_text(encoding="utf-8"))

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> NewsStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._connection.execute("BEGIN")
        try:
            yield self._connection
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    # -- provenance ---------------------------------------------------------

    def register_provider(self, name: str, base_host: str, *, tier: str | None = None) -> int:
        """Register a provider, or return the existing identifier."""
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO provider(name, base_host, tier, added_at) "
                "VALUES (?, ?, ?, ?)",
                (name, base_host, tier, utc_now()),
            )
            row = connection.execute(
                "SELECT provider_id FROM provider WHERE name = ?", (name,)
            ).fetchone()
        return int(row["provider_id"])

    def open_run(
        self,
        provider_id: int,
        *,
        window_start: str | None = None,
        window_end: str | None = None,
        symbols: Sequence[str] | None = None,
    ) -> int:
        """Record that an ingestion has begun, before any row is fetched.

        Opening the run first is what makes a crashed pull distinguishable from
        a day with no news: the run stays ``running`` and never settles, rather
        than leaving no trace at all.
        """
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO ingestion_run"
                "(provider_id, started_at, status, query_window_start, query_window_end,"
                " query_symbols) VALUES (?, ?, 'running', ?, ?, ?)",
                (
                    provider_id,
                    utc_now(),
                    window_start,
                    window_end,
                    ",".join(sorted(symbols)) if symbols else None,
                ),
            )
        return int(cursor.lastrowid or 0)

    def settle_run(
        self,
        run_id: int,
        *,
        status: str,
        pages_fetched: int = 0,
        rows_reported: int | None = None,
        rows_stored: int = 0,
        failure_reason: str | None = None,
    ) -> None:
        """Close a run with its outcome. Permitted exactly once."""
        if status not in {"complete", "incomplete", "failed"}:
            message = f"a run settles as complete, incomplete, or failed, not {status!r}"
            raise StoreIntegrityError(message)
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE ingestion_run SET finished_at = ?, status = ?, pages_fetched = ?,"
                " rows_reported = ?, rows_stored = ?, failure_reason = ? WHERE run_id = ?",
                (
                    utc_now(),
                    status,
                    pages_fetched,
                    rows_reported,
                    rows_stored,
                    failure_reason,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                # A silent no-op here means an ingest believes its outcome was
                # recorded when nothing was. (The already-settled case aborts
                # via trigger before reaching this check.)
                message = f"settle_run: run {run_id} does not exist"
                raise StoreIntegrityError(message)

    def archive_payload(self, run_id: int, page_number: int, body: bytes) -> None:
        """Keep the vendor's bytes so a parser change can be replayed."""
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO raw_payload"
                "(run_id, page_number, fetched_at, body, body_sha256) VALUES (?, ?, ?, ?, ?)",
                (run_id, page_number, utc_now(), body, hashlib.sha256(body).hexdigest()),
            )

    # -- writing ------------------------------------------------------------

    def store(
        self,
        records: Sequence[ArticleRecord],
        *,
        provider_id: int,
        run_id: int,
        ingested_at: str | None = None,
    ) -> int:
        """Append article versions. Returns the number of new versions written.

        Re-running an ingestion over the same window is a no-op: the unique
        index on ``(article_id, provider_id, content_sha256)`` absorbs a repeat,
        and only genuinely changed content produces a second version. That makes
        the whole pipeline safe to retry, which matters because the alternative
        to retrying a failed pull is a coverage hole.
        """
        explicit = ingested_at is not None
        stamp = _require_canonical("ingested_at", ingested_at) if ingested_at else utc_now()
        written = 0
        with self._transaction() as connection:
            if not explicit:
                # Monotonic guard for the auto-stamped path. utc_now() is
                # wall-clock, and a clock stepped backward between two ingests
                # would give a LATER version an EARLIER ingested_at -- the
                # recency window would then serve the stale version forever.
                # Clamping to the store's own high-water mark keeps ingestion
                # order and timestamp order aligned. The explicit path (tests
                # constructing historical timelines) is exempt by design.
                row = connection.execute(
                    "SELECT MAX(ingested_at) AS mark FROM article_version"
                ).fetchone()
                if row["mark"] is not None and stamp < row["mark"]:
                    stamp = row["mark"]
            for record in records:
                _require_canonical("published_at", record.published_at)
                title = normalize_text(record.title, limit=MAX_TITLE)
                snippet = (
                    normalize_text(record.snippet, limit=MAX_SNIPPET) if record.snippet else None
                )
                if len(record.url) > MAX_URL:
                    message = f"url exceeds the {MAX_URL}-character cap"
                    raise UntrustedContentError(message)

                key = record.dedup_key()
                connection.execute(
                    "INSERT OR IGNORE INTO article(dedup_key, first_seen_at) VALUES (?, ?)",
                    (key, stamp),
                )
                article_row = connection.execute(
                    "SELECT article_id FROM article WHERE dedup_key = ?", (key,)
                ).fetchone()
                article_id = int(article_row["article_id"])

                digest = record.content_sha256()
                # Idempotency compares against the LATEST version only, not the
                # whole history. A global unique index on (article, provider,
                # digest) -- the first design -- silently swallowed any
                # A->B->A sequence: a withdrawal could never be followed by a
                # reinstatement of the original content, because the original
                # digest already existed. Re-serving the same content twice in
                # a row still collapses; genuine reverts append.
                latest = connection.execute(
                    "SELECT content_sha256 FROM article_version"
                    " WHERE article_id = ? AND provider_id = ?"
                    " ORDER BY ingested_at DESC, version_id DESC LIMIT 1",
                    (article_id, provider_id),
                ).fetchone()
                if latest is not None and latest["content_sha256"] == digest:
                    continue
                cursor = connection.execute(
                    "INSERT INTO article_version"
                    "(article_id, provider_id, run_id, vendor_uuid, published_at, ingested_at,"
                    " title, url, source_domain, language, snippet, lifecycle, content_sha256)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        article_id,
                        provider_id,
                        run_id,
                        record.vendor_uuid,
                        record.published_at,
                        stamp,
                        title,
                        record.url,
                        record.source_domain,
                        record.language,
                        snippet,
                        record.lifecycle,
                        digest,
                    ),
                )
                written += 1
                version_id = int(cursor.lastrowid or 0)

                for symbol, (relevance, sentiment) in (record.symbols or {}).items():
                    ticker = symbol.strip().upper()
                    if not ticker or len(ticker) > MAX_SYMBOL or not ticker.isascii():
                        message = f"refusing an implausible ticker from vendor data: {symbol!r}"
                        raise UntrustedContentError(message)
                    connection.execute(
                        "INSERT OR IGNORE INTO symbol_mention"
                        "(version_id, symbol, relevance, sentiment) VALUES (?, ?, ?, ?)",
                        (version_id, ticker, relevance, sentiment),
                    )
        return written

    # -- reading ------------------------------------------------------------

    def knowable_at(
        self,
        as_of: str,
        *,
        symbol: str | None = None,
        since: str | None = None,
        limit: int = 500,
    ) -> list[StoredArticle]:
        """Every article this system could have known at ``as_of``.

        The filter is ``ingested_at <= as_of``. **Not** ``published_at``: the
        vendor's timestamp is revisable and an archive endpoint returns stories
        stamped long before we could have seen them, so filtering on it would
        admit exactly the news the strategy could not have had. ``published_at``
        is returned for interpretation and may be used to *narrow* further via
        ``since``, never to widen.

        For each article, the version returned is the latest one ingested at or
        before ``as_of`` -- so a headline revised tomorrow does not change what
        this query says about today.

        **Every filter except the time bound is applied to that winning version,
        never inside the recency ranking.** This is the load-bearing subtlety,
        and it has bitten twice in two forms. Filtering the CTE by lifecycle
        would resurrect the last pre-withdrawal version of a retracted story.
        And -- found by audit on 2026-08-11, after the lifecycle lesson was
        already documented -- filtering the CTE by *symbol* had exactly the same
        shape: a withdrawal or re-tag carries no mention row for the old symbol,
        so ranking only tag-carrying versions crowned the superseded version as
        ``recency = 1`` and served it as live. Only ``ingested_at <= as_of``
        may shape the ranking; everything else interrogates the winner.

        The symbol filter matches **vendor-assigned entity tags on the winning
        version**, not article content. An article about a company whose vendor
        entity-matching did not tag it is invisible to ``symbol=``; Adanos rows
        carry only the one ticker they were queried for. Symbol-filtered counts
        are therefore lower bounds on coverage, never the population.
        """
        sql, params = self._visible_query(
            as_of,
            symbol=symbol,
            since=since,
            select="SELECT * FROM visible",
            tail="ORDER BY published_at DESC, version_id DESC LIMIT ?",
        )
        params.append(int(limit))
        rows = self._connection.execute(sql, params).fetchall()
        return [
            StoredArticle(
                article_id=int(row["article_id"]),
                version_id=int(row["version_id"]),
                provider=str(row["provider_name"]),
                published_at=str(row["published_at"]),
                ingested_at=str(row["ingested_at"]),
                title=str(row["title"]),
                url=str(row["url"]),
                source_domain=row["source_domain"],
                snippet=row["snippet"],
                lifecycle=str(row["lifecycle"]),
            )
            for row in rows
        ]

    def count_knowable_at(
        self, as_of: str, *, symbol: str | None = None, since: str | None = None
    ) -> int:
        """The exact size of the knowable set -- no limit, same semantics.

        Exists because ``len(knowable_at(...))`` saturates at whatever ``limit``
        was passed, and a saturated length reads as a real count. The deskreview
        report printed exactly that number.
        """
        sql, params = self._visible_query(
            as_of,
            symbol=symbol,
            since=since,
            select="SELECT COUNT(*) AS n FROM visible",
            tail="",
        )
        row = self._connection.execute(sql, params).fetchone()
        return int(row["n"])

    def mentions_knowable_at(
        self,
        as_of: str,
        *,
        since: str | None = None,
        limit: int = 5000,
    ) -> list[StoredMention]:
        """Vendor symbol mentions on versions genuinely visible at ``as_of``.

        Version selection happens before the mention join.  A later withdrawal
        or retag therefore cannot resurrect a superseded mention, matching the
        point-in-time semantics of :meth:`knowable_at`.
        """
        outer = ["visible.recency = 1", "visible.lifecycle = 'active'"]
        params: list[Any] = [_require_canonical("as_of", as_of)]
        if since is not None:
            outer.append("visible.published_at >= ?")
            params.append(_require_canonical("since", since))
        params.append(int(limit))
        rows = self._connection.execute(
            f"""
            WITH visible AS (
                SELECT v.*, p.name AS provider_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY v.article_id
                           ORDER BY v.ingested_at DESC, v.version_id DESC
                       ) AS recency
                FROM article_version v
                JOIN provider p ON p.provider_id = v.provider_id
                WHERE v.ingested_at <= ?
            )
            SELECT visible.*, mention.symbol, mention.sentiment
            FROM visible
            JOIN symbol_mention mention ON mention.version_id = visible.version_id
            WHERE {" AND ".join(outer)}
            ORDER BY visible.published_at DESC, visible.version_id DESC, mention.symbol
            LIMIT ?
            """,  # noqa: S608 - clauses are fixed literals; values are parameters
            params,
        ).fetchall()
        return [
            StoredMention(
                article_id=int(row["article_id"]),
                version_id=int(row["version_id"]),
                provider=str(row["provider_name"]),
                published_at=str(row["published_at"]),
                ingested_at=str(row["ingested_at"]),
                title=str(row["title"]),
                url=str(row["url"]),
                snippet=row["snippet"],
                symbol=str(row["symbol"]),
                sentiment=float(row["sentiment"]) if row["sentiment"] is not None else None,
            )
            for row in rows
        ]

    def _visible_query(
        self,
        as_of: str,
        *,
        symbol: str | None,
        since: str | None,
        select: str,
        tail: str,
    ) -> tuple[str, list[Any]]:
        """The one point-in-time query, shared by every reader.

        The CTE is filtered by ``ingested_at`` alone; symbol, ``since`` and
        lifecycle apply to the per-article winner outside it. See
        :meth:`knowable_at` for why that split is load-bearing.
        """
        outer = ["recency = 1", "lifecycle = 'active'"]
        params: list[Any] = [_require_canonical("as_of", as_of)]
        if since is not None:
            outer.append("published_at >= ?")
            params.append(_require_canonical("since", since))
        if symbol is not None:
            ticker = symbol.strip().upper()
            if not ticker.isascii() or len(ticker) > MAX_SYMBOL:
                message = f"implausible ticker in a query: {symbol!r}"
                raise UntrustedContentError(message)
            outer.append(
                "EXISTS (SELECT 1 FROM symbol_mention m "
                "WHERE m.version_id = visible.version_id AND m.symbol = ?)"
            )
            params.append(ticker)

        sql = f"""
            WITH visible AS (
                SELECT v.*, p.name AS provider_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY v.article_id
                           ORDER BY v.ingested_at DESC, v.version_id DESC
                       ) AS recency
                FROM article_version v
                JOIN provider p ON p.provider_id = v.provider_id
                WHERE v.ingested_at <= ?
            )
            {select}
            WHERE {" AND ".join(outer)}
            {tail}
        """  # noqa: S608 - clauses are literals; values are parameters
        return sql, params

    def consecutive_failures(self, provider_id: int) -> int:
        """How many ``failed`` runs this provider has recorded since its last success.

        The circuit breaker's input. Counts settled ``failed`` runs backwards
        from the most recent; any ``complete`` or ``incomplete`` run ends the
        streak, because both mean the provider actually delivered data.
        Unsettled ``running`` rows are skipped rather than counted either way --
        a crashed run's cause is unknown, and guessing it into the streak would
        let one kill -9 suspend a healthy provider.
        """
        rows = self._connection.execute(
            "SELECT status FROM ingestion_run WHERE provider_id = ? AND status != 'running' "
            "ORDER BY run_id DESC",
            (provider_id,),
        ).fetchall()
        streak = 0
        for row in rows:
            if row["status"] != "failed":
                break
            streak += 1
        return streak

    def coverage(self, provider_id: int | None = None) -> list[dict[str, Any]]:
        """Ingestion runs and their outcomes.

        A feature series is only as trustworthy as the runs behind it, so this
        is the first thing to read before believing any news-derived number.
        """
        if provider_id is None:
            rows = self._connection.execute(
                "SELECT r.*, p.name AS provider_name FROM ingestion_run r "
                "JOIN provider p ON p.provider_id = r.provider_id ORDER BY r.started_at DESC"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT r.*, p.name AS provider_name FROM ingestion_run r "
                "JOIN provider p ON p.provider_id = r.provider_id "
                "WHERE r.provider_id = ? ORDER BY r.started_at DESC",
                (provider_id,),
            ).fetchall()
        return [dict(row) for row in rows]
