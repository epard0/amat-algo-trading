# The news pipeline: point-in-time intake, storage, and use

**Status (2026-08-11):** built, adversarially audited, gate-clean. Collecting
forward from first ingestion; contains no history. The sandbox this repository
is developed in cannot reach either vendor host, so ingestion runs from the
operator's own shell.

---

## What it is

A separate package (`src/newsdesk/`) that pulls financial news metadata from
vendor APIs into an append-only SQLite store, so that a *future* strategy
version can read point-in-time news features from a database — never from an
API call inside the strategy core.

The isolation is enforced by tests, not convention: `amat` and `newsdesk` never
import each other in either direction; only three client modules may perform
network transport; no mutating HTTP verb literal exists anywhere in the
package; the whole package is stdlib-only.

```text
vendor APIs ──GET only──▶ clients/ ──pages──▶ ingest.py ──▶ store (SQLite, append-only)
                                                                  │
                              deskreview (article counts, caveats) ┘
                              [future v3 features, gated on accrued history]
```

## How news articles are used — exactly, today

**Three consumers exist.**

1. `deskreview` (the morning/midday/evening review passes) asks the store one
   question: *how many articles were knowable at this instant?*
   (`src/deskreview/cli.py`, `_count_news`). The count and its caveats appear
   in the review report a human reads. An unreachable or empty store is
   reported as exactly that — the caveat distinguishes "no events knowable"
   from "the check could not run".
2. `newsdesk export-scalars` (`src/newsdesk/features.py`) derives the **v3
   conditioning series**: one de-risk scalar per calendar date from the
   attention-spike rule (a day whose new-article count reaches 3x the trailing
   63-day median exports 0.5 instead of 1.0). The file — not the store — is
   what `amat study --scalars` reads, so the strategy still never touches the
   database or the network: `amat` and `newsdesk` remain import-isolated, and
   the file's header records the exact parameters that produced it.
3. `newsdesk discover-assets` matches point-in-time headlines to the reviewed
   `configs/news/themes.toml` taxonomy and emits a short-lived JSON artifact.
   `amat plan --news-assets ...` may admit those companies to a dedicated 10%
   gross-capped cluster. News only changes eligibility: the ordinary price
   trend, covariance sizing, symbol cap, cluster cap, and rebalance rules still
   decide whether a trade exists.

No strategy process reads the store or calls a news vendor. The v1 strategy is
frozen and news-blind by definition; v3 sees news only through the exported
scalar file, while optional plan-time universe discovery sees only the inert,
expiring candidate artifact. Headline text never becomes code or a weight.

**Why not more, yet.** The store filters knowability on `ingested_at` — the
moment *we* first observed a row — because the vendor's `published_at` is
revisable and an archive query returns what survived, not what existed.
Collection began 2026-08-11, so a backtest over 2016–2026 finds zero knowable
articles at every decision point. That is correct behaviour, not a gap: any
news-derived backtest result before months of forward collection exist would be
built on backfill, which is contaminated by revision and survivorship and can
only ever be a rough sensitivity.

**The path to use in a strategy.** A news feature (per-symbol article counts,
sentiment aggregates, attention spikes vs. trailing baseline) would enter as a
new strategy version under the freezing rule, reading only
`knowable_at(decision_time)`, and must survive the falsification controls in
`amat.validation.falsification` (shuffled, turnover-matched-random, and
static-mean controls) before its result means anything. The regime-conditioned
v3 was rejected by exactly that suite on 2026-08-10; a news channel gets no
lower bar.

## Point-in-time semantics — the two clocks

| Column | Whose clock | Trustworthy? |
|---|---|---|
| `published_at` | The vendor's claim of when the story appeared | No — revisable, backfillable |
| `ingested_at` | Our store, stamped at first observation | Yes — ours, monotonic |

Every knowability query filters on `ingested_at`. Timestamps are stored in one
fixed-width canonical shape (`YYYY-MM-DDTHH:MM:SS.mmmZ`) enforced at three
layers, because SQLite compares these columns as text and mixed fractional
precision breaks text ordering in both directions (measured: `.123Z` sorts
*after* `.1234Z`). Revisions append versions; withdrawals append; nothing
updates or deletes — triggers abort both on every history-bearing table.

## Commands

Credentials are exported by name, never stored:

```bash
export MARKETAUX_API_TOKEN=…   # Marketaux (query-parameter auth; scrubbed everywhere)
export ADANOS_API_KEY=…        # Adanos (X-API-Key header)
```

```bash
# Pull one window of news for chosen symbols (overlap windows deliberately --
# boundary inclusivity is undocumented, and the store is idempotent)
uv run newsdesk ingest-marketaux --symbols SPY,QQQ,GLD \
  --published-after 2026-08-10 --published-before 2026-08-11

# Broad market intake is what theme discovery needs; omitting --symbols leaves
# the vendor's symbol filter out of the request entirely.
uv run newsdesk ingest-marketaux \
  --published-after 2026-08-23 --published-before 2026-08-24

# Pull one ticker's article-level mentions (Adanos Professional tier only;
# lower tiers get a 403, recorded as a failed run -- that is the honest outcome)
uv run newsdesk ingest-adanos --ticker SPY --start 2026-08-01 --end 2026-08-10

# The run ledger: read this before trusting any query
uv run newsdesk coverage

# What was knowable at an instant (defaults to now)
uv run newsdesk knowable --as-of 2026-08-11T14:00:00+00:00 --symbol SPY

# The v3 conditioning series: one scalar per calendar date, self-describing
# header, consumed by `amat study configs/strategy/amat_v3.toml --scalars ...`
uv run newsdesk export-scalars --start 2015-12-01 --end 2026-08-10 \
  --symbols SPY,QQQ,IWM,EFA,EEM,TLT,IEF,GLD,SLV,DBC,USO,UUP \
  --out outputs/scalars/news_v3.csv

# Positive event themes -> short-lived candidate artifact for plan-time use.
uv run newsdesk discover-assets --themes configs/news/themes.toml \
  --exclude-symbols SPY,QQQ,IWM,EFA,EEM,IEF,GLD,SLV,DBC,USO,UUP \
  --out outputs/news/assets.json

uv run amat plan configs/strategy/amat_v6.toml --snapshot data/snapshots/... \
  --equity 100000 --news-assets outputs/news/assets.json --json
```

Re-running `discover-assets` against the same `--out` path carries candidates
that have left the lookback as `active: false`. `amat plan`
keeps those symbols recognized but forces their targets to zero, so existing
news positions are sold through the ordinary rebalance path instead of becoming
unknown holdings that halt the executor. Retired symbols remain exit-only until
they are deliberately removed from the reviewed taxonomy, which also covers
dust positions and withheld small sales. Active candidates are limited to 8;
the whole dynamic cluster is hard-capped at 10% gross exposure.

The exporter's timing rule: the scalar for date `d` counts articles ingested by
`d`T23:59:59.999Z — after the session close that forms the decision, hours
before the earliest next-morning execution window (13:30/14:30 UTC). Counts
filter on `ingested_at` (our clock), so dates before the store's first
ingestion always export 1.0.

The `--symbol` filter matches **vendor-assigned entity tags on the article's
current version**, not article content. An untagged article about the company
is invisible to it; symbol-filtered counts are lower bounds.

## The circuit breaker

**After 3 consecutive `failed` runs, a provider is suspended and further
attempts are refused before any request is built.** Both vendors meter by
quota — Marketaux 100 requests/day, Adanos 250 requests/*month* — and a run
failing for a standing reason (revoked key, wrong tier, exhausted plan) fails
identically on every retry; repeating it spends the allowance a fixed
configuration would have used.

- `failed` runs count toward the streak; `complete` and `incomplete` reset it
  (both mean data flowed); unsettled `running` rows are skipped (a crashed
  run's cause is unknown).
- Each provider's breaker is independent — Adanos tripping never blocks
  Marketaux.
- To resume: read `newsdesk coverage` for the recorded failure reasons, fix the
  cause, then re-run with `--force`. A successful run resets the breaker on its
  own; a failed `--force` attempt deepens the streak.
- Within a single run, transport already retries transient statuses (429/5xx)
  up to 5 times with backoff. The breaker operates above that, on settled runs.

## Other free data sources — evaluated 2026-08-11

Ranked by fit for *this* system's constraints (stdlib client, honest
point-in-time story, terms that permit storing metadata):

| Source | What | Fit | Notes |
|---|---|---|---|
| **Alpaca News API** | Ticker-tagged news (Benzinga-sourced), `data.alpaca.markets/v1beta1/news` | **Best next candidate.** Uses the credentials this repo already holds (`ALPACA_MARKET_API_KEY`), the exact host the existing `amat.data.alpaca` client pins, and carries `created_at`/`updated_at` per article. A client here is ~50 lines on `newsdesk.clients.base`. | History to ~2015 exists but is backfill like any archive; forward collection is what counts. |
| **SEC EDGAR** | Filings full-text + submissions API, no key | **Strong.** `acceptanceDateTime` is a genuine vendor-side point-in-time stamp — the only source here whose *historical* timestamps are trustworthy by construction. Fair-access policy: ≤10 req/s, descriptive User-Agent required. | Filings, not news; a different feature family (8-K events, ownership changes). |
| **FRED / ALFRED** | Macro series (rates, CPI, claims), free key | **Strong for macro context.** ALFRED serves *vintages* — the series as it stood on a past date — which is real point-in-time macro data, the thing news archives cannot offer. `api.stlouisfed.org` is even on this sandbox's allowlist already. | Not news; feeds regime/context, not articles. |
| **GDELT 2.0** | Global news metadata, no key, 15-min updates | Moderate. Free and vast, but no ticker tagging (theme/actor codes), noisy, and its own timestamps are collection-time approximations. | Would need entity mapping this repo shouldn't hand-roll. |
| Finnhub free tier | Company news, 60 req/min | Weak: ~1 year lookback, terms restrict storage. | |
| NewsAPI.org free tier | Headlines | Poor: development-only license, 24 h delay, no commercial use. | |
| Alpha Vantage free tier | News & sentiment | Poor: 25 requests/day since 2024 — below useful. | |

Recommendation, in order: an **Alpaca news client** (zero new credentials, host
already trusted, same client discipline), then **EDGAR** if filing events
become a feature family, with **ALFRED** vintages as the macro complement.
None is built yet; each would be a new `newsdesk.clients` module behind the
same base class, breaker, and store.

## Operator checklist before the first production pull

Both vendor contracts were reconstructed through mediated fetches (the
documentation hosts block automated readers); re-validate live first:

1. Marketaux: confirm the `meta{found,returned,limit,page}` envelope, the
   six-fractional-digit `published_at`, and current free-tier limits against a
   real response.
2. Adanos: pull `https://api.adanos.org/openapi.json` **whole** and diff its
   GET surface against this client's single `/news/stocks/v1/` prefix; confirm
   whether mention `published_at` is ISO or epoch (both are handled; which one
   arrives should be known, not assumed).
3. Rotate any credential that has ever appeared in a terminal or transcript.
4. After the first pull, read `newsdesk coverage` and spot-check
   `newsdesk knowable` against the vendor dashboard's article counts.

## Known limitations

- **No history until it accrues.** The store answers "what did *we* know" —
  it cannot be backfilled into knowing things earlier than 2026-08-11.
- **Adanos article rows are Professional-gated**; on lower tiers the ingest
  records a failed run (and the breaker will suspend after three).
- **Symbol filters are vendor tags**, not content understanding.
- **Aggregate sentiment endpoints are never stored** — Adanos documents no
  point-in-time guarantee for them, so persisting them would fabricate as-of
  facts. They remain live queries only.
- The full audit record — 22 findings, 3 HIGH, all fixed and each pinned by a
  regression test — lives in the test files named
  `tests/unit/test_newsdesk_*.py` under `TestAuditFindings20260811`.
