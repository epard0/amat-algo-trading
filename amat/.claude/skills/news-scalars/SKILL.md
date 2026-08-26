---
name: news-scalars
description: Operate the newsdesk pipeline - vendor ingestion with the circuit breaker, coverage checks, point-in-time queries, and exporting the v3 weight-scalar file. Covers the two-clocks rule and fail-closed alignment with amat study.
argument-hint: "[ingest|coverage|export]"
---

# News pipeline operations

The `newsdesk` package pulls news metadata into an append-only SQLite store
and answers one kind of question: *what was knowable at instant T on our own
ingestion clock?* Full architecture: `docs/news-pipeline.md`.

## The two clocks (never confuse them)

- `published_at` — the vendor's revisable claim. Interpretation only.
- `ingested_at` — our clock, stamped at first observation. Every knowability
  filter uses this and only this. The store cannot know things earlier than
  its first ingestion (2026-08-11); zero results before then are correct.

## Ingestion (operator shell; sandbox cannot reach vendor hosts)

```bash
export MARKETAUX_API_TOKEN=...   # names only; values never in files
export ADANOS_API_KEY=...

uv run newsdesk ingest-marketaux --symbols SPY,QQQ,GLD \
  --published-after 2026-08-10 --published-before 2026-08-11
uv run newsdesk ingest-adanos --ticker SPY --start 2026-08-01 --end 2026-08-10

# Read the run ledger BEFORE trusting any query
uv run newsdesk coverage
```

Overlap consecutive windows deliberately (boundary inclusivity is
undocumented; the store is idempotent). **Circuit breaker:** 3 consecutive
`failed` runs suspend a provider before any request is built — read the
recorded reasons in `coverage`, fix the cause, then `--force`. A success
resets the streak. Both vendors meter by quota; the breaker exists so a
standing failure cannot burn it.

## Exporting the v3 conditioning series

```bash
uv run newsdesk export-scalars --start 2015-12-01 --end 2026-08-10 \
  --symbols SPY,QQQ,IWM,EFA,EEM,TLT,IEF,GLD,SLV,DBC,USO,UUP \
  --out outputs/scalars/news_v3.csv
```

- One row per **calendar date**; sessions are a subset, so any panel inside
  the window aligns. The consumer (`amat study --scalars`) fails closed on a
  missing session — re-export over a wider window rather than hand-editing.
- The rule's parameters (baseline, spike multiple, de-risk scalar) travel in
  the file header; changing any of them is a new experiment.
- With today's empty store every scalar is 1.0 and v3 is numerically v1.
  That is correct behaviour, not an error.

## Inspection

```bash
uv run newsdesk knowable --as-of 2026-08-11T14:00:00+00:00 --symbol SPY
```

`--symbol` matches vendor entity tags on the current version — a lower bound,
not content understanding.

## Hard rules

- The store is append-only; triggers refuse UPDATE/DELETE. Never work around
  them.
- `amat` and `newsdesk` never import each other; the scalar file is the only
  bridge.
- Backfilled archives cannot produce valid point-in-time features here, by
  construction. Do not try.
