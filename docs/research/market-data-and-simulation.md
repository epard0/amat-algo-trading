# Market data, sleeves, and the simulated live environment

Reference for the modules added on 2026-08-08. Answer-first; every command below
is exactly what was run.

## 1. Where the data comes from

| Field | Value |
|---|---|
| Provider | Alpaca Market Data v2 |
| Feed | SIP (consolidated tape) |
| Host | `data.alpaca.markets`, read-only |
| Current snapshot | `alpaca-sip-2026-08-11`: 2016-01-04 through 2026-08-10, 2,665 sessions, 30 US-listed ETFs (EMB added for the v5 credit cluster) |
| Prior snapshot | `alpaca-sip-2026-08-08`: 2016-01-04 through 2026-08-07, 2,664 sessions, 29 ETFs — cited by results measured before 2026-08-11 |
| Credentials | `ALPACA_MARKET_API_KEY`, `ALPACA_MARKET_API_SECRET`, read from the environment by name |

Credential *values* are never read into a report, a log, a manifest, or a
snapshot. `AlpacaCredentials.__repr__` redacts them, and
`tests/unit/test_read_only_data_access.py` asserts that they cannot be rendered.

### Why not yfinance

`yfinance` was the first thing tried and it does not work from this environment:
`query1`/`query2.finance.yahoo.com` return HTTP 429 to every request, with and
without a browser user-agent, on both endpoints. Stooq returns a JavaScript
interstitial rather than CSV. Alpaca's SIP feed was reachable, authenticated,
and covers the full window at both daily and intraday granularity, so it is the
source. This is recorded because "we used Alpaca" is a data-provenance fact, not
an implementation detail.

### Timing semantics

| Field | Semantics |
|---|---|
| `session_date` | Exchange session date in `America/New_York`, recovered by converting the vendor's UTC daily stamp to the exchange zone. Truncating the UTC instant would shift every session back one day. |
| `adjusted_close` | Split- and dividend-adjusted. **Features only.** |
| `raw_close` | Unadjusted. **Valuation only.** |
| `window.vwap` | Raw VWAP of 09:30–09:35 `America/New_York`. **Execution only.** Resolved per session through `ZoneInfo`, so it tracks daylight saving rather than assuming a fixed UTC offset. |
| `as_of` | UTC instant the pull completed. |

The end bound is **exclusive of the current session**. A daily bar for a session
that has not closed is not a completed observation, and the SIP feed rejects the
request outright (HTTP 403) rather than serving a partial bar.

### Running an ingestion

```bash
uv run python scripts/ingest_market_data.py --snapshot-id alpaca-sip-2026-08-08
```

Roughly 25 minutes, dominated by one request per session for the execution
window: the vendor cannot filter intraday bars by time of day, so a bulk pull
would return every five-minute interval of every session and discard 97% of it.
Progress checkpoints to `data/interim/<id>.windows.json` after each session, so
an interrupted pull resumes rather than restarting. The checkpoint is mutable
scratch and is never evidence.

### Snapshot layout

```
data/snapshots/<snapshot-id>/
  daily.parquet     one row per (session, symbol): adjusted + raw + OHLCV
  window.parquet    one row per (session, symbol) with a usable execution window
  actions.parquet   cash dividends and splits by ex-date
  manifest.json     provider, feed, as_of, coverage, per-partition SHA-256
```

`data/snapshots` is an immutable evidence root. `reserve_immutable_directory`
creates a new versioned directory with `mkdir` and no `exist_ok`, so re-running
an ingestion with the same identifier fails rather than overwriting evidence.
`load_manifest` re-verifies every partition digest on read, so a snapshot that
was valid when written cannot be silently reinterpreted later.

> `data/raw` is the root the original layout documented. It is unusable in the
> agent sandbox, where it is masked by a character device — a write there would
> be silently discarded rather than persisted. `data/snapshots` carries identical
> immutability guarantees and is the working root.

### Quality gates (all fail closed)

| Gate | Rejects |
|---|---|
| OHLC ordering | `high < low`, or open/close outside `[low, high]` |
| Positive prices | any non-positive raw or adjusted close |
| Adjusted continuity | a single-session move above 60% in a *split-adjusted* series with no split recorded on that ex-date |
| Duplicate keys | more than one row per (session, symbol) in either pull |
| Pull alignment | raw and adjusted pulls disagreeing on the calendar by more than one session at each edge |
| Pagination | a `next_page_token` is followed to exhaustion; a partial page is never treated as complete coverage |

An untradable execution window (zero volume, or a non-positive VWAP) is dropped
rather than priced. It becomes an *absence* in the panel, which the execution
model records as a missed fill. It is never imputed from a close.

## 2. Universe

29 symbols in 9 clusters. The hard selection criterion is **continuous SIP
coverage from 2016-01-04**: the Ledoit-Wolf estimator needs complete rows, and a
symbol that listed mid-sample would either shorten the whole panel or require
padding returns the market never produced. IBIT was excluded on exactly this
ground (2024 inception).

| Cluster | Gross cap | Symbols |
|---|---|---|
| `us_equity` | 40% | SPY QQQ IWM VTI |
| `us_sectors` | 30% | XLK XLF XLE XLV XLI |
| `international` | 25% | EFA EEM VEA VWO EWJ FXI |
| `rates` | 35% | TLT IEF SHY AGG TIP |
| `credit` | 20% | LQD HYG |
| `metals` | 20% | GLD SLV |
| `commodities` | 20% | DBC USO |
| `real_estate` | 15% | VNQ IYR |
| `us_dollar` | 15% | UUP |

**This is a survivorship-biased universe.** Every symbol was chosen in 2026 for
having persisted since 2016. That biases results upward by an amount this
project has not measured, and no result below is corrected for it.

Broad equity and sector ETFs sit in separate clusters deliberately: XLK and SPY
are not independent, and pooling them would let one tech-beta bet consume the
entire equity budget while appearing diversified. The correlation guard handles
what the static split cannot.

## 3. Strategy versions

`configs/strategy/amat_managed_v2.toml` is a **new strategy version**, not an
edit of v1. The freezing rule makes any change to a frozen value a new version,
and this configuration changes two: the universe (12 → 29) and
`max_symbol_weight` (20% → 10%). It also adds the `[risk]` overlay section.

`configs/strategy/amat_v1.toml` is untouched, and v1 is **not** retired: both
engines are first-class and `amat study` and `amat report` default to running
both. Beyond v1 having outperformed v2 on this data, it is also the **benchmark
comparator** `.claude/rules/30-quant-methodology.md` requires ("compare against …
a signal-free portfolio using the same allocator and execution assumptions"). A
v2 result reported without it would not be interpretable.

The overlay is inside the hashed configuration, not passed at the call site. An
overlay tuned outside the hashed configuration would let two runs with identical
manifests produce different results — the exact reproducibility failure the
manifest exists to prevent.

## 4. Per-asset sleeves

```bash
uv run amat study configs/strategy/amat_managed_v2.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-08 \
  --sleeve-equity 100000 \
  --html outputs/reports/amat_study.html
```

The combined portfolio starts at `sleeve_equity × symbol_count`; each sleeve
starts at `sleeve_equity`. Equal bases make per-asset profit comparable in
currency, and make the portfolio-versus-sleeves difference attributable to
pooling and constraints rather than to account size.

Four settings change in a sleeve, all structural:

| Setting | Portfolio | Sleeve | Why |
|---|---|---|---|
| universe | 29 symbols | 1 symbol | that is what a sleeve is |
| `max_symbol_weight` | 10% | 100% | a cap on one name's share of a *portfolio* is meaningless in an account holding only that name; leaving it would report a 90%-cash account |
| `max_positions` | 15 | 1 | ditto |
| `min_effective_bets` | 2.0 | 1.0 | a single position scores exactly 1.0 by construction, so any floor above 1.0 vetoes every entry and the sleeve sits in cash forever — a flat line that reads as "no signal" rather than "guard misapplied" |

**A sleeve is a diagnostic, not a deployable strategy.** Sleeve results are not
additive and must never be summed or averaged into portfolio performance: 29
sleeves each risking their own equity is 29× the capital with none of the
diversification.

## 5. Simulated live environment

```bash
uv run amat simulate configs/strategy/amat_managed_v2.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-08
```

A paper account. It reads current bars, forms the same decisions the backtest
forms, fills them with the same `SimulatedExecutionModel`, and appends a JSON
lines observation to `outputs/paper/`.

**No account is connected and none can be.** The only network component is the
read-only market-data client. The reachable API surface is bars and corporate
actions; `_get` refuses any other path, and
`tests/unit/test_read_only_data_access.py` asserts across the whole package that
no module names a trading host, an account-scoped path, or a state-changing HTTP
method.

### Replay, not incremental state

Each step recomputes the account from inception rather than mutating saved
state. Incremental live state is where paper systems silently diverge from their
backtests — one missed session, one restart mid-window, and the two disagree
forever with no way to tell when it started. Replay makes divergence impossible
by construction, and costs seconds on a daily-frequency strategy.

The live tail is **not** written into the snapshot. A snapshot is evidence; a
live tail is a moving target that the vendor revises as corporate actions
settle. Mixing them would make the snapshot digest meaningless. Every report
that used appended sessions says so in its caveats.

### What it does not model

Queue position. Partial fills beyond the cash constraint. Market impact from its
own size. Borrow, taxes, or financing. It measures what the strategy *would*
have done, not what it did.

### Defect fixed 2026-08-09: `next_session_targets`

The field documented as "weights the strategy would seek in the next execution
window" was assigned the account's *current* positions. Anyone diffing the two
to find trades would have found none — silently, forever, and most convincingly
when the account was flat and both were empty.

The cause was structural rather than a typo: the session loop schedules each
decision into a later session *inside the panel*, and on the final session there
is none, so the decision was never formed and there was nothing to report.
`include_terminal_decision` on the managed runner now forms it. On the fixture
that regression-tests this, the old code reported `{GLD: 0.1543}` (held) where
the decision was `{GLD: 0.1471}`.

## 6. Execution plans

```bash
uv run amat plan configs/strategy/amat_v1.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-08 \
  --engine v1-frozen --equity 100000 \
  --positions my-positions.json --out outputs/plans/latest.json
```

Emits the decision formed from the last completed session as versioned JSON:
target weights, the trades that reach them, provenance, the session it may not
execute before, and an expiry. **It describes intent and submits nothing.**

Positions and equity are *supplied*, never fetched — so a plan is reproducible
from a journal without contacting anything, and this repository keeps no path to
an account. A held symbol outside the universe is reported and never traded.

Order submission belongs in a separate repository; `docs/execution-repo.md` is
the contract it implements against.

Two properties to know before relying on it:

- **v1 returns in seconds, v2 takes minutes.** v1's decision is a pure function
  of the panel. v2's overlay is path-dependent — drawdown level, heat, measured
  expectancy, regime fit, Kelly estimate — so its terminal decision can only be
  read out of a full replay from inception.
- **v2's overlay state is simulated, not realized.** A real account opened today
  has no trade history, so the overlay inherits one from a backtest of trades
  that were never placed. Every v2 plan says so in its caveats.

## 7. Parameter search

```bash
uv run python scripts/optimize_overlay.py \
  --config configs/strategy/amat_managed_v2.toml \
  --snapshot data/snapshots/alpaca-sip-2026-08-08 \
  --train-end 2022-12-31
```

Protocol:

1. The split date is fixed before any candidate is evaluated and never moved.
2. Every candidate is scored on the training window only.
3. Ranking uses `fold_score`, **not** in-sample Sharpe: `0.5 × median + 0.5 ×
   minimum` of per-fold Sharpe over four contiguous folds. The minimum term is
   the point — it refuses to reward a configuration carried by one favourable
   stretch, which an aggregate Sharpe cannot distinguish from steady
   performance. A candidate under 40 training trades scores `-inf`: it is not a
   weak candidate, it is an unmeasured one.
4. Only then are the finalists run on validation, once.
5. The winner's neighbourhood is probed on each axis, so a reader can tell a
   plateau from a cliff.

Every candidate — including rejected ones — is written to
`outputs/optimization.json`. A search that reports only its winner cannot be
audited for selection effects.

**This cannot make an overfit result honest.** The universe, the signal, and the
guard structure were all chosen knowing this market history. The validation
window is optimistic even though it was read once.
