-- Point-in-time news store.
--
-- Two rules shape every table here.
--
-- 1. APPEND ONLY. There is no UPDATE and no DELETE anywhere in this package,
--    and triggers below abort them rather than trusting that discipline. A
--    store that can be updated cannot answer "what did we know at time T",
--    because the answer changes whenever someone corrects a row.
--
-- 2. TWO CLOCKS, NEVER MERGED. `published_at` is the vendor's claim about when
--    a story appeared; `ingested_at` is when this system observed it. Features
--    filter on `ingested_at`, because that is the only one that cannot be
--    revised after the fact. See the module docstring in __init__.py.
--
-- Revisions are new rows, not edits: a vendor changing a headline produces a
-- second `article_version` for the same `article`, and the validity interval of
-- the first is derived at query time with LEAD(). There is deliberately no
-- `valid_to` column -- a column would have to be written, which means an
-- UPDATE, which is the thing being forbidden.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- ---------------------------------------------------------------------------
-- Provenance
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS provider (
    provider_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    base_host     TEXT NOT NULL,
    -- Free text describing the plan/tier in force, because rate limits and
    -- field availability differ by tier and a result depends on which was used.
    tier          TEXT,
    added_at      TEXT NOT NULL
) STRICT;

-- One row per attempt to pull data. An ingestion that failed or paginated
-- incompletely is recorded, not discarded: "we have no articles for that day"
-- and "we never successfully asked" must be distinguishable forever.
CREATE TABLE IF NOT EXISTS ingestion_run (
    run_id            INTEGER PRIMARY KEY,
    provider_id       INTEGER NOT NULL REFERENCES provider(provider_id),
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    -- 'complete' only when pagination was followed to a proven end.
    status            TEXT NOT NULL CHECK (status IN ('running', 'complete', 'incomplete', 'failed')),
    -- The request that produced this run, minus any credential. Stored so a
    -- coverage gap can be attributed to what was actually asked for.
    query_window_start TEXT,
    query_window_end   TEXT,
    query_symbols      TEXT,
    pages_fetched      INTEGER NOT NULL DEFAULT 0,
    rows_reported      INTEGER,
    rows_stored        INTEGER NOT NULL DEFAULT 0,
    failure_reason     TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS ingestion_run_provider_idx
    ON ingestion_run(provider_id, started_at);

-- ---------------------------------------------------------------------------
-- Articles
-- ---------------------------------------------------------------------------

-- The stable identity of a story, independent of any one vendor's row for it.
-- `dedup_key` is what makes two vendors' copies of the same wire story one
-- article rather than two, which matters because an article count is a feature
-- and double-counting a syndicated story inflates it everywhere it appears.
CREATE TABLE IF NOT EXISTS article (
    article_id   INTEGER PRIMARY KEY,
    dedup_key    TEXT NOT NULL UNIQUE,
    first_seen_at TEXT NOT NULL
) STRICT;

-- One immutable observation of an article. A vendor revision, a re-tagging, or
-- a withdrawal each append a new row.
CREATE TABLE IF NOT EXISTS article_version (
    version_id    INTEGER PRIMARY KEY,
    article_id    INTEGER NOT NULL REFERENCES article(article_id),
    provider_id   INTEGER NOT NULL REFERENCES provider(provider_id),
    run_id        INTEGER NOT NULL REFERENCES ingestion_run(run_id),

    -- The vendor's own identifier, kept so a row can be traced back.
    vendor_uuid   TEXT,

    -- THE TWO CLOCKS. Both ISO-8601 UTC with a 'Z' suffix, enforced below.
    published_at  TEXT NOT NULL,
    ingested_at   TEXT NOT NULL,

    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    source_domain TEXT,
    language      TEXT,
    -- Snippet/description. Full bodies are not stored by default: they are
    -- large, most vendors' terms forbid redistribution, and no feature so far
    -- needs more than the headline and lede.
    snippet       TEXT,

    -- 'active' or 'withdrawn'. A withdrawal is an append, so the article's
    -- history still shows it was known before it was retracted -- which is
    -- exactly what a point-in-time query must reproduce.
    lifecycle     TEXT NOT NULL DEFAULT 'active'
                  CHECK (lifecycle IN ('active', 'withdrawn')),

    -- sha256 of the normalized (title, snippet, url) triple. Two consecutive
    -- versions with the same digest are a vendor re-serving the same content,
    -- not a revision, and are collapsed by the writer.
    content_sha256 TEXT NOT NULL,

    -- Exact-width canonical shape, not merely "ends in Z". These columns are
    -- compared as TEXT, and mixed fractional precision breaks text ordering in
    -- both directions (measured 2026-08-11: '...00.123Z' sorts after
    -- '...00.1234Z', and '...00.000000Z' != '...00.000Z' for one instant). The
    -- earlier LIKE pattern admitted any precision, so a Marketaux six-digit
    -- stamp stored verbatim would have silently corrupted every point-in-time
    -- filter. GLOB is anchored and fixed-width, so nothing off-shape can enter
    -- even through a writer that bypasses the Python API.
    CHECK (published_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    CHECK (ingested_at GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    -- The GLOB checks digits, not calendars: 2026-02-31 and hour 99 pass it.
    -- SQLite's datetime() returns NULL for shapes that are not real instants.
    CHECK (datetime(published_at) IS NOT NULL),
    CHECK (datetime(ingested_at) IS NOT NULL),
    CHECK (length(title) <= 2000),
    CHECK (snippet IS NULL OR length(snippet) <= 20000),
    CHECK (length(url) <= 4000)
) STRICT;

-- Lookup index for the writer's latest-digest idempotency check. Deliberately
-- NOT unique: the first design made (article, provider, digest) globally
-- unique, which silently swallowed every A->B->A sequence -- a withdrawal
-- could never be followed by reinstatement of the original content, because
-- the original digest already existed in history. The writer now compares
-- against the article's LATEST version only, so consecutive re-serves of one
-- payload still collapse while genuine reverts append.
CREATE INDEX IF NOT EXISTS article_version_identity_idx
    ON article_version(article_id, provider_id, content_sha256);

-- The index that carries the point-in-time query.
CREATE INDEX IF NOT EXISTS article_version_ingested_idx
    ON article_version(ingested_at, article_id);
CREATE INDEX IF NOT EXISTS article_version_published_idx
    ON article_version(published_at);
CREATE INDEX IF NOT EXISTS article_version_article_idx
    ON article_version(article_id, ingested_at);

-- ---------------------------------------------------------------------------
-- Entities
-- ---------------------------------------------------------------------------

-- Which instruments a version mentions. Attached to the *version*, not the
-- article, because vendors re-tag: a story that gains a "TSLA" tag next week
-- was not a TSLA story at decision time, and attaching tags to the article
-- would rewrite history retroactively.
CREATE TABLE IF NOT EXISTS symbol_mention (
    version_id  INTEGER NOT NULL REFERENCES article_version(version_id),
    symbol      TEXT NOT NULL,
    -- Vendor-supplied relevance/sentiment, nullable because not every provider
    -- supplies them and a fabricated 0.0 is worse than a NULL.
    relevance   REAL,
    sentiment   REAL,
    PRIMARY KEY (version_id, symbol),
    CHECK (symbol = upper(symbol) AND length(symbol) BETWEEN 1 AND 12),
    CHECK (relevance IS NULL OR (relevance >= 0.0 AND relevance <= 1.0)),
    CHECK (sentiment IS NULL OR (sentiment >= -1.0 AND sentiment <= 1.0))
) STRICT;

CREATE INDEX IF NOT EXISTS symbol_mention_symbol_idx ON symbol_mention(symbol, version_id);

-- ---------------------------------------------------------------------------
-- Raw payload archive
-- ---------------------------------------------------------------------------

-- The vendor's response exactly as received, so a parsing change can be
-- re-applied to history instead of requiring a re-pull that would no longer
-- return the same thing. This is the only defence against a vendor silently
-- changing a field's meaning.
CREATE TABLE IF NOT EXISTS raw_payload (
    payload_id  INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES ingestion_run(run_id),
    page_number INTEGER NOT NULL,
    fetched_at  TEXT NOT NULL,
    body        BLOB NOT NULL,
    body_sha256 TEXT NOT NULL,
    UNIQUE (run_id, page_number)
) STRICT;

-- ---------------------------------------------------------------------------
-- Append-only enforcement
--
-- Discipline in application code is not a control; a trigger is. These make the
-- append-only claim provable rather than aspirational, and they are what let
-- the validity interval be derived instead of stored.
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS article_version_no_update
BEFORE UPDATE ON article_version
BEGIN
    SELECT RAISE(ABORT, 'article_version is append-only: append a new version instead');
END;

CREATE TRIGGER IF NOT EXISTS article_version_no_delete
BEFORE DELETE ON article_version
BEGIN
    SELECT RAISE(ABORT, 'article_version is append-only: deleting destroys point-in-time history');
END;

CREATE TRIGGER IF NOT EXISTS symbol_mention_no_update
BEFORE UPDATE ON symbol_mention
BEGIN
    SELECT RAISE(ABORT, 'symbol_mention is append-only: re-tagging appends a new version');
END;

CREATE TRIGGER IF NOT EXISTS symbol_mention_no_delete
BEFORE DELETE ON symbol_mention
BEGIN
    SELECT RAISE(ABORT, 'symbol_mention is append-only');
END;

CREATE TRIGGER IF NOT EXISTS raw_payload_no_update
BEFORE UPDATE ON raw_payload
BEGIN
    SELECT RAISE(ABORT, 'raw_payload is the received bytes and is never edited');
END;

CREATE TRIGGER IF NOT EXISTS raw_payload_no_delete
BEFORE DELETE ON raw_payload
BEGIN
    SELECT RAISE(ABORT, 'raw_payload is the received bytes and is never deleted');
END;

-- An ingestion_run is written twice by design -- once as 'running', once with
-- its outcome -- so it permits exactly that one transition and nothing else.
CREATE TRIGGER IF NOT EXISTS ingestion_run_settles_once
BEFORE UPDATE ON ingestion_run
FOR EACH ROW WHEN OLD.status <> 'running'
BEGIN
    SELECT RAISE(ABORT, 'a settled ingestion_run is immutable');
END;

CREATE TRIGGER IF NOT EXISTS ingestion_run_no_delete
BEFORE DELETE ON ingestion_run
BEGIN
    SELECT RAISE(ABORT, 'ingestion_run is the coverage record and is never deleted');
END;

-- The audit of 2026-08-11 found the append-only claim was narrower than the
-- header said: article, provider, and the identity fields of a running
-- ingestion_run were all freely rewritable. Closed below.

CREATE TRIGGER IF NOT EXISTS article_no_update
BEFORE UPDATE ON article
BEGIN
    SELECT RAISE(ABORT, 'article identity is append-only; dedup_key and first_seen_at are history');
END;

CREATE TRIGGER IF NOT EXISTS article_no_delete
BEFORE DELETE ON article
BEGIN
    SELECT RAISE(ABORT, 'deleting an article orphans its versions and destroys history');
END;

CREATE TRIGGER IF NOT EXISTS provider_no_update
BEFORE UPDATE ON provider
BEGIN
    SELECT RAISE(ABORT, 'provider rows are provenance; register a new provider instead of editing one');
END;

CREATE TRIGGER IF NOT EXISTS provider_no_delete
BEFORE DELETE ON provider
BEGIN
    SELECT RAISE(ABORT, 'provider rows are provenance and are never deleted');
END;

-- A running run may only SETTLE: status moves to a terminal value and the
-- outcome fields fill in. Its identity -- who asked, when, for what window --
-- is frozen at open_run. Without this, the settle window was a loophole in
-- which any column could be rewritten.
CREATE TRIGGER IF NOT EXISTS ingestion_run_settle_shape
BEFORE UPDATE ON ingestion_run
FOR EACH ROW WHEN OLD.status = 'running' AND (
    NEW.status NOT IN ('complete', 'incomplete', 'failed')
    OR NEW.provider_id IS NOT OLD.provider_id
    OR NEW.started_at IS NOT OLD.started_at
    OR NEW.query_window_start IS NOT OLD.query_window_start
    OR NEW.query_window_end IS NOT OLD.query_window_end
    OR NEW.query_symbols IS NOT OLD.query_symbols
)
BEGIN
    SELECT RAISE(ABORT, 'a running ingestion_run may only settle; its identity fields are frozen');
END;
