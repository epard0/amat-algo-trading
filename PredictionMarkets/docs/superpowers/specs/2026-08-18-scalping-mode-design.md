# Scalping Mode — Design Specification

**Date:** 18 August 2026
**Status:** Draft for review — no implementation has begun
**Author:** drafted with Claude Code, decisions taken by @supasuge

---

## 1. Summary

Scalping mode adds a second, independently-accounted trading strategy to the
Kalshi 15-minute crypto bot. It differs from the existing predictor strategy in
three ways:

1. **It quotes rather than crosses.** Orders rest as `post_only` limits inside
   the spread and only cross the book as a fallback.
2. **It holds several positions at once**, ranked against the existing strategy
   in one shared capital pool.
3. **It exits early.** A position whose edge reverses is sold back into the book
   rather than carried to settlement.

A second, smaller signal source — a no-arbitrage scanner over the daily
`KXBTCD` strike ladder — is specified here but ships disabled and paper-only.

---

## 2. Evidence base

### 2.1 The paper

Bürgi, Deng & Whelan (2026), *Makers or Takers: The Economics of the Kalshi
Prediction Market*, GWU Center for Economic Research WP 2026-001. 313,972
contracts.

| Finding | Value |
| --- | --- |
| Average return, contracts ≤10¢ | **−60%+** |
| Average return, contracts >50¢ | small positive, post-fee |
| Average return, contracts >70¢ | positive, **statistically significant** |
| Average return, **Makers** | **−9.64%** |
| Average return, **Takers** | **−31.46%** |
| Makers buying ≥50¢ | **+2.6%** |
| Taker fee modelled | `$0.07 · P · (1−P)`, rounded up to the cent |

Two limitations that constrain how far this may be pushed, and which must not be
quietly dropped later:

- **The sample is 24-hour-to-10-day markets, not 15-minute crypto markets.** The
  favorite–longshot bias is a robust finding across prediction markets generally,
  but its magnitude on a 15-minute horizon is unmeasured here.
- **Kalshi began charging maker fees after April 2025.** The −9.64% vs −31.46%
  gap was measured under a fee structure where makers paid nothing. Part of that
  gap is therefore historical. The maker advantage is expected to persist because
  it also comes from buying at better prices, but its size must be re-measured
  from our own fills, not assumed.

### 2.2 Live market structure (probed 18 Aug 2026, public API, unauthenticated)

| Series | Live markets | Total volume | Spread | Top-of-book depth |
| --- | --- | --- | --- | --- |
| `KXBTC15M` | **1** | 446,137 | **1¢** (0.59 / 0.60) | 5,225 × 1,949 |
| `KXBTCD` (daily ladder) | 36 | 160,035 | 1¢ | ~5,000+ |
| `KXBTC` (daily, `between`) | 13 | 784 | 2–3¢ | often 5 |

Three consequences, each of which killed a candidate signal:

- **`KXBTC15M` exposes one strike per event, one event at a time.** There is no
  vertical ladder on the 15-minute series, so no intra-series monotonicity or
  butterfly arbitrage exists there.
- **YES/NO cannot disagree.** Kalshi quotes one YES book; `no_ask ≡ 1 − yes_bid`
  by definition. A "YES + NO ≠ $1" scanner would detect nothing, ever.
- **The `between` surface is too thin to trade.** Total volume 784 with resting
  sizes of 5 contracts.

What remains real: the 15-minute market is *very* liquid with a 1¢ spread, which
is an excellent venue for maker quoting; and the `KXBTCD` ladder is a genuine
no-arbitrage surface. A live check of that ladder found monotonicity intact
(0.94 → 0.83 → 0.72 → 0.59 → 0.30 → 0.18 → 0.09 → 0.04) but a **convexity
violation** at the 63999.99 / 64249.99 / 64499.99 butterfly:

```
0.83 − 2(0.72) + 0.59 = −0.02      # must be ≥ 0
```

A real 2¢ inefficiency — but the same order of magnitude as the 1¢ spread plus
the ~1.7¢ quadratic fee. This is why ladder arbitrage ships **off by default**.

### 2.3 SDK capability (verified against the installed `kalshi-python-async`)

`CreateOrderV2Request` already exposes everything a maker strategy needs, none of
which the bot currently uses:

```
post_only, reduce_only, expiration_time, order_group_id,
self_trade_prevention_type, subaccount
```

Plus `amend_order_v2`, `decrease_order_v2`, `batch_create_orders_v2`,
`batch_cancel_orders_v2`, and `get_order_queue_position`.

### 2.4 Independent corroboration

Two findings from the wider search, both supporting decision 3:

- **Kalshi has filed with the CFTC for direct payments to traders who
  consistently post bids and offers near the market price** — an explicit
  liquidity-provider rebate. This partially offsets the post-April-2025 maker
  fee noted in §2.1 and pushes the economics further toward quoting. It is a
  filing, so it must be confirmed as live before any rebate is assumed in the
  fee model; the fee model reads `fee_multiplier` from the venue and therefore
  needs no change to benefit if it lands.
- Independent commentary converges on the same two edges the paper measures:
  fading longshots, and **passive liquidity provision rather than active
  market-making** — the distinction being that the former underwrites the
  favorite side rather than trying to earn the spread symmetrically. That is
  precisely the shape of §5: quote only inside a favourite band, never quote
  both sides.

Community implementations remain uniformly on the other side of this. All six
comparable public repositories are taker-side spot-vs-contract models — the
design this bot already has — and one author states plainly that it lost him
$100. No public implementation was found that quotes maker-side on the
15-minute series.

### 2.5 Structural blockers in the current ledger

Both are enforced in SQLite, not Python (`execution/trade_ledger.py:158-196`):

```sql
UNIQUE(mode, environment, ticker)                       -- a ticker may be traded ONCE, ever
CREATE UNIQUE INDEX idx_trades_one_open
  ON trades(mode, environment) WHERE status = 'OPEN';   -- one open position, globally
```

There is also no `strategy` column, so two strategies cannot be attributed
separately. All three are addressed by the migration in §7.

---

## 3. Decisions taken

| # | Decision | Choice |
| --- | --- | --- |
| 1 | Venue scope | Kalshi only. Polymarket stays read-only; no wallet, no EIP-712, no custody. |
| 2 | Win-rate objective | EV-optimised, **constrained to a price band** that structurally yields ≥55%. |
| 3 | Order style | **Maker-first** (`post_only`) with taker fallback. |
| 4 | Reversal handling | **Exit** — sell the position back into the book with `reduce_only`. |
| 5 | Statistics reset | **Archive live, hard-reset paper.** Live rows preserved in place. |
| 6 | Concurrency | **asyncio + explicit locks.** No OS threads; SQLite stays thread-bound. |
| 7 | Coexistence | **Shared capital pool**, candidates ranked and the best filled first. |
| 8 | Validation | **No backtester.** Validate in paper mode; thresholds seeded from the paper. |
| 9 | Strategy core | **Maker scalping on 15m** primary, **ladder arb on daily** secondary. |

### 3.1 How decisions 2 and 8 reconcile

Decision 8 removes the ability to *fit* a price band from our own data. Decision
2 still requires the band to yield ≥55% wins. These reconcile through an
identity rather than a measurement:

> Break-even win rate = `price + fee`.

If entries are confined to prices ≥ 0.60, then **any strategy that is
EV-positive at all must win more than 60% of the time**. The win-rate floor is
therefore a consequence of the price band, not an independent constraint that
could be gamed. This is what makes a ≥55% target meaningful without a backtest.

**Stated assumption, to be revisited:** the band's *placement* is seeded from
the paper's measured bands, not from this bot's own history. Every quote, fill,
miss and exit is logged with the fields needed to re-fit it later (§9). The band
is configurable and its provenance is recorded in the spec and in `config.py`.

---

## 4. Architecture

```
                 ┌──────────────────────────────────────┐
                 │              bot.py                  │
                 │      KalshiCryptoBot.run_cycle       │
                 └───────────────┬──────────────────────┘
                                 │ collects candidates from all registered strategies
              ┌──────────────────┴───────────────────┐
              ▼                                      ▼
  ┌───────────────────────┐            ┌──────────────────────────┐
  │  PredictorStrategy    │            │    ScalperStrategy       │
  │  (existing, wrapped)  │            │    (new)                 │
  └───────────┬───────────┘            └────────────┬─────────────┘
              │                                     │
              │        both emit TradeCandidate with ev_per_dollar
              └──────────────────┬──────────────────┘
                                 ▼
                 ┌──────────────────────────────────┐
                 │  CandidatePool — rank by EV/$,   │
                 │  fill until capital or           │
                 │  concurrency limit binds         │
                 └───────────────┬──────────────────┘
                                 ▼
                 ┌──────────────────────────────────┐
                 │  OrderLifecycle                  │
                 │  post_only → wait → cross | give │
                 │  up; cancel/amend; reduce_only   │
                 │  exits                           │
                 └───────────────┬──────────────────┘
                                 ▼
                 ┌──────────────────────────────────┐
                 │  PositionBook (asyncio.Lock)     │
                 │  in-memory mirror of the ledger  │
                 └───────────────┬──────────────────┘
                                 ▼
                 ┌──────────────────────────────────┐
                 │  TradeLedger (SQLite, v2 schema) │
                 └──────────────────────────────────┘
```

### 4.1 New modules

| Module | Purpose | Pure? |
| --- | --- | --- |
| `core/strategy_brain/strategies/scalper.py` | Quote price selection, band membership, EV per dollar, exit decision | **Yes** |
| `core/strategy_brain/strategies/ladder_arb.py` | Monotonicity / convexity violation detection over a strike ladder | **Yes** |
| `execution/strategy.py` | `Strategy` protocol; `TradeCandidate` gains `strategy` and `ev_per_dollar` | Yes |
| `execution/order_lifecycle.py` | Resting-order state machine; place / amend / cancel / cross / exit | No (I/O) |
| `execution/position_book.py` | Lock-guarded registry of open positions and committed capital | No (state) |
| `execution/scalp_gate.py` | Pure gates specific to scalping (band, adverse selection, exit) | **Yes** |

The purity column is load-bearing. `execution/risk_gate.py` is pure today and
that is what makes every guard testable without a clock, a network or a ledger.
The new gate module follows the same rule.

### 4.2 Modified modules

| Module | Change |
| --- | --- |
| `config.py` | New `ScalpSettings`, validated and bounded at startup like everything else |
| `execution/kalshi_client.py` | `place_resting_order`, `cancel_order`, `amend_order`, `queue_position`, `place_exit_order` |
| `execution/trade_ledger.py` | v2 schema migration (§7); `strategy` column; exit fields; multi-position support |
| `bot.py` | Strategy registry; candidate pool; concurrent evaluation; open-position supervisor |
| `monitoring/*` | Per-strategy breakdown in metrics, dashboard and Prometheus output |
| `reporting/report.py` | Per-strategy section in the PDF |

---

## 5. The scalping strategy

### 5.1 Entry

A market is a scalp candidate when **all** hold:

| Gate | Default | Rationale |
| --- | --- | --- |
| `SCALP_PRICE_BAND` | `[0.60, 0.92]` | Paper: >70¢ significantly positive, ≤10¢ catastrophic. Lower bound also sets the ≥55% win-rate floor (§3.1). Upper bound because above ~0.92 a loss costs 11× the gain and the edge is inside the tick. |
| `SCALP_MIN_EV_PER_DOLLAR` | `0.015` | Common ranking currency; must clear fees plus a buffer. |
| `SCALP_MAX_SPREAD` | `0.02` | A wide spread means the maker premium is being paid away on exit. |
| `SCALP_MIN_BOOK_DEPTH` | `500` contracts | Thin books cannot be exited; the exit path is what bounds the loss. |
| Model agreement | reuse `check_model_market_divergence` | The existing divergence gate is the important one and is not weakened here. |
| Candle freshness | reuse `check_candle_freshness` | Unchanged. |

### 5.2 Quote placement

Given `yes_bid`, `yes_ask` and model probability `p`:

```
quote = yes_bid + tick                     if (yes_ask − yes_bid) > tick
quote = yes_bid                            otherwise (already 1c wide — join, do not cross)
reject if quote ∉ SCALP_PRICE_BAND
reject if ev_per_dollar(p, quote) < SCALP_MIN_EV_PER_DOLLAR
```

Submitted with `post_only=True`, so an order that would cross is **rejected by
the venue rather than silently converted into a taker fill**. That rejection is
a benign outcome, handled like `BENIGN_ORDER_ERRORS`, not an exception.

### 5.3 The resting-order state machine

```
        ┌────────┐
        │ IDLE   │
        └───┬────┘
            │ candidate selected
            ▼
        ┌────────┐  edge gone / spot moved > SCALP_REQUOTE_TICKS
        │ QUOTING├──────────────────────────────► CANCELLED ──► IDLE
        └───┬────┘
            │ fill (partial or full)
            ▼
        ┌────────┐
        │ FILLED │
        └───┬────┘
            │
            ▼
        ┌──────────┐  probability recovers
        │MONITORING├────────────────────────┐
        └───┬──────┘                        │
            │ exit condition (§5.4)         │
            ▼                               │
        ┌────────┐                          │
        │EXITING │◄─────────────────────────┘
        └───┬────┘
            │ exit filled          │ never filled and close approaching
            ▼                      ▼
        ┌────────┐            ┌──────────────┐
        │ CLOSED │            │ HOLD TO SETTLE│
        └────────┘            └──────────────┘
```

`QUOTING` has a hard lifetime of `SCALP_QUOTE_TTL_SECONDS` (default 20). On
expiry, if the edge still survives against the *current* book, the order may
cross once as a taker; otherwise it is cancelled.

**Adverse selection is the central risk of this design.** A resting bid is
filled precisely when someone wants to sell — which correlates with the market
moving against us. Four mitigations, all mandatory:

1. Cancel the quote when the model edge disappears, checked every cycle.
2. Cancel and requote when spot moves more than `SCALP_REQUOTE_TICKS`.
3. Cap quote lifetime (`SCALP_QUOTE_TTL_SECONDS`).
4. Never quote inside `MINIMUM_SECONDS_TO_CLOSE`; a fill there cannot be exited.

### 5.4 Exit on reversal

Checked once per cycle for every open scalp position. Exit when:

```
ev_hold  =  p · 1.00 − cost_basis
ev_exit  =  current_yes_bid − exit_fee
exit when   ev_exit > ev_hold + SCALP_EXIT_BUFFER
       or   p < SCALP_EXIT_PROBABILITY        (default 0.45)
```

The exit order is `side="ask"`, `reduce_only=True`, posted `post_only` first for
`SCALP_EXIT_QUOTE_TTL` seconds, then crossed. `reduce_only` is what guarantees
an exit can never accidentally open a short.

If the market is inside `STATUS_CHECK_LEAD_SECONDS`, exiting is abandoned and
the position is held to settlement — a late exit pays the spread and both fees
to avoid a risk that is about to resolve anyway.

### 5.5 Ladder arbitrage (secondary, disabled by default)

Pure functions over `[(strike, yes_bid, yes_ask, bid_size, ask_size)]` for one
event, sorted by strike.

**Monotonicity.** For `K₁ < K₂`, `P(>K₁) ≥ P(>K₂)`. Violation when
`yes_bid(K₂) > yes_ask(K₁)`; captured by buying `K₁` and selling `K₂`.

**Convexity.** For equally spaced `K₁ < K₂ < K₃`,
`P(K₁) − 2·P(K₂) + P(K₃) ≥ 0`. Violation captured by a butterfly: buy `K₁`,
buy `K₃`, sell two of `K₂`.

Selling is expressed as `side="ask"`, which on one YES book is economically
buying NO at `1 − price`. No separate NO book is introduced.

Fires only when **every** condition holds:

- net edge exceeds total fees plus `LADDER_ARB_BUFFER`,
- every leg has resting depth ≥ the intended size,
- all legs submit together via `batch_create_orders_v2` under one
  `order_group_id`,
- a partial group fill triggers **immediate unwind** of the filled legs.

Leg risk is the reason this is off by default. A partially-filled butterfly is a
naked directional position that nothing in the design wanted to hold.

Config: `LADDER_ARB_ENABLED=false`, and it is refused outright in live mode until
it has accumulated a configurable number of profitable paper fills.

---

## 6. Concurrency

Everything stays on one asyncio event loop. No OS threads: the SQLite connection
is bound to its creating thread by design and the workload is entirely I/O-bound,
so threads would add failure modes and buy nothing.

`PositionBook` guards all mutable position state with a single `asyncio.Lock`:

```python
async with self._lock:
    if self._open_count() >= self.max_concurrent:   return None
    if self._committed + cost > self.capital_limit: return None
    self._reserve(candidate)                        # reserve BEFORE the await
```

**Capital is reserved before the order is placed and released on rejection.**
Checking capacity, awaiting a fill, and then recording it would let two
coroutines both pass the check and jointly exceed the limit — the classic
check-then-act race, and the reason the lock exists at all.

The ledger write stays synchronous inside the lock. It is a sub-millisecond
local WAL write; moving it to a thread would break the connection's thread
affinity for no measurable gain.

### 6.1 Shared-pool ranking

Both strategies emit candidates carrying `ev_per_dollar`:

```
ev_per_dollar = (p · 1.00 − cost_basis) / cost_basis
```

This is the common currency. A probability edge in points and an arbitrage edge
in cents are not otherwise comparable, and ranking them on the wrong scale would
systematically favour whichever strategy happened to use bigger numbers.

Per cycle: evaluate all markets concurrently → collect candidates → sort by
`ev_per_dollar` descending → attempt fills in order until concurrency or capital
binds. The existing "single highest-edge candidate per pass" rule generalises to
"highest-edge candidates first, up to the limits".

---

## 7. Ledger migration (v1 → v2)

SQLite cannot drop a constraint, so this is a table rebuild, executed once,
guarded by a `schema_version` row in `ledger_metadata`.

**Order of operations:**

1. **Archive first.** Copy `trade_ledger.db`, `-wal`, `-shm` and `trades.jsonl`
   to `archive/<UTC timestamp>/`. Nothing is deleted at any point.
2. Create `trades_v2` with the new schema.
3. Copy **all** rows, live included, with `strategy='predictor'`.
4. **Delete paper rows only.** Live rows are preserved in place, per decision 5.
5. **Re-seed the paper balance.** `PAPER_BALANCE_USD` is normally written only
   when the ledger is first created, so deleting the paper rows without also
   rewriting that metadata row would leave a starting balance that reflects a
   history no longer present. The reset must set it explicitly, and this is the
   one place where changing `PAPER_BALANCE_USD` legitimately rewrites history.
6. Drop `trades`, rename `trades_v2` → `trades`, rebuild indexes.
7. Write `schema_version=2`.
8. `PRAGMA integrity_check`, and abort the whole transaction on anything but `ok`.

**Schema changes:**

```sql
strategy       TEXT NOT NULL DEFAULT 'predictor'
exit_price     TEXT             -- NULL unless exited early
exit_contracts TEXT
exit_fees      TEXT
exited_at      TEXT
status         CHECK(status IN ('OPEN','WON','LOST','VOID','EXITED'))

-- replaces UNIQUE(mode, environment, ticker):
UNIQUE(mode, environment, ticker, strategy)

-- idx_trades_one_open is DROPPED.
```

Dropping `idx_trades_one_open` removes the database's guarantee of a single open
position. That guarantee moves into `PositionBook` under the lock, and the
per-strategy concurrency cap replaces it. **This is a deliberate weakening of a
durable invariant into a process-lifetime one, and it is the single riskiest
change in this document.** Compensating controls:

- `UNIQUE(mode, environment, ticker, strategy)` still prevents one strategy from
  double-entering the same ticker.
- On startup, `PositionBook` is rebuilt from the ledger, so a restart cannot
  lose track of open positions.
- The predictor strategy keeps `max_concurrent = 1`, so its behaviour is
  **bit-identical** to today unless explicitly reconfigured.

`execution/trade_ledger.py` retains zero `try`/`except`. The migration is
transactional; a failure must surface to the caller, not be swallowed.

---

## 8. Configuration

All bounded and validated at startup, following `KalshiSettings.from_env()`.

```
SCALP_ENABLED=false                  # ships off; opt in explicitly
SCALP_PRICE_BAND_LOW=0.60
SCALP_PRICE_BAND_HIGH=0.92
SCALP_MIN_EV_PER_DOLLAR=0.015
SCALP_MAX_SPREAD=0.02
SCALP_MIN_BOOK_DEPTH=500
SCALP_QUOTE_TTL_SECONDS=20
SCALP_REQUOTE_TICKS=2
SCALP_EXIT_PROBABILITY=0.45
SCALP_EXIT_BUFFER=0.01
SCALP_EXIT_QUOTE_TTL=8
SCALP_MAX_CONCURRENT=3
SCALP_MAX_CAPITAL_FRACTION=0.50    # safety ceiling, NOT an allocation - see below
SCALP_TAKER_FALLBACK=true

LADDER_ARB_ENABLED=false
LADDER_ARB_SERIES=KXBTCD
LADDER_ARB_BUFFER=0.02
LADDER_ARB_MAX_CONTRACTS=25
LADDER_ARB_MIN_PAPER_FILLS=50        # before live is permitted at all
```

Live trading continues to require **both** `ENABLE_LIVE_TRADING=true` and
`--live`. `SCALP_ENABLED` grants no live permission of its own.

**`SCALP_MAX_CAPITAL_FRACTION` is a ceiling, not a budget.** Decision 7 chose a
shared pool, so scalping does not receive an allocation it may always spend;
it competes for the same capital as the predictor and wins only by ranking
higher on `ev_per_dollar`. The fraction exists solely to stop one strategy from
committing the entire balance and starving the other of any capacity to act. If
the two decisions are ever reconciled the other way — toward real per-strategy
budgets — this setting is where that change lands.

---

## 9. Observability

Every quote, requote, cancel, fill, miss and exit is written to
`decisions.jsonl` with `record_type="scalp"`. The run-collapsing added in the
previous session applies: repeated identical verdicts fold into runs, and
`traded` / `order_rejected` / `halted` / `error` are never folded.

Fields logged specifically to make the price band re-fittable later, since no
backtest informed it:

```
quote_price, mid_at_quote, spread_at_quote, queue_position_at_placement,
fill_latency_ms, filled | cancelled | expired, spot_move_during_quote,
realised_pnl, exit_reason, band_at_decision
```

That set is deliberately the minimum needed to answer "was `[0.60, 0.92]` the
right band?" from our own fills, without another data-collection project.

Monitoring gains a per-strategy breakdown (summary, equity curve, win rate,
calibration) and Prometheus gains a `strategy` label. The dashboard remains
read-only and never writes the ledger.

---

## 10. Testing

Tests use fakes and never send an order or touch the network. `asyncio_mode` is
`auto`, and async fakes must be real coroutine functions.

| Area | Tests |
| --- | --- |
| Band gate | in-band accepted; 0.05 and 0.97 rejected; boundaries exact |
| EV ranking | scalp and predictor candidates ordered correctly on one scale |
| Quote placement | joins at 1¢ spread; improves at 2¢+; never crosses |
| State machine | every transition; TTL expiry; cancel on edge loss; requote on spot move |
| Adverse selection | fill arriving after edge reversal is exited, not held |
| Exit logic | exits when `ev_exit > ev_hold`; holds inside the lead window |
| `reduce_only` | an exit can never open a short |
| **Concurrency** | 50 coroutines racing `PositionBook`; never exceeds cap or capital |
| Ladder monotone | a monotone ladder yields **no** signal (guards false positives) |
| Ladder violation | the measured `0.83 / 0.72 / 0.59` butterfly is detected |
| Ladder fees | a violation smaller than fees+buffer is **rejected** |
| Leg risk | partial group fill triggers unwind |
| **Migration** | v1→v2 preserves every live row, drops paper rows, `integrity_check ok` |
| Migration safety | a failure mid-migration rolls back and leaves v1 intact |
| Back-compat | predictor at `max_concurrent=1` behaves identically to today |

---

## 11. Implementation phases

Each phase ends green (`uv run pytest`, `uv run ruff check .`) and is
independently revertible.

| Phase | Deliverable | Risk |
| --- | --- | --- |
| **1** | Archive script + ledger v2 migration + `strategy` column | **High** — touches durable money records |
| **2** | `Strategy` protocol; wrap the existing predictor; `ev_per_dollar`. No behaviour change | Low |
| **3** | `PositionBook` with the lock; multi-position support; predictor pinned to 1 | Medium |
| **4** | Client: `post_only`, cancel, amend, queue position | Medium |
| **5** | `OrderLifecycle` state machine + tests | **High** — new order semantics |
| **6** | `scalper.py` + `scalp_gate.py`, paper only | Medium |
| **7** | Exit-on-reversal with `reduce_only` | **High** — can realise losses |
| **8** | Shared-pool ranking; both strategies live together in paper | Medium |
| **9** | Monitoring + reporting per strategy | Low |
| **10** | `ladder_arb.py`, disabled by default, paper only | Medium |

Phases 1, 5 and 7 are the ones to review most carefully. Phase 1 is the only one
that is difficult to undo, which is why it archives before it touches anything.

---

## 12. Wider improvements found while exploring

Not part of this build; recorded because the evidence surfaced here.

1. **The bot is 100% taker.** On the paper's evidence that is the −31.46% side of
   a 22-point split. This spec addresses it for scalping only; the predictor
   strategy remains taker-only and could adopt maker-first later.
2. **Discovery fetches 20 markets per series and discards 19.**
   `_get_asset_market` keeps only the nearest-closing one
   (`execution/kalshi_client.py:265`). The data for a ladder scanner is already
   being paid for and thrown away.
3. **`liquidity_dollars` and `get_order_queue_position` are available and
   unused.** Both are directly relevant to maker execution.
4. **Throughput is capped at ~4 trades/hour** by the one-open-position rule —
   which also caps the rate at which any statistical confidence can accumulate.
5. **No constant in the predictor has been fitted to an outcome** (carried over
   from the previous session's analysis). The counterfactual labeller remains the
   highest-value unblocked item.
6. **`subaccount` exists in the SDK** and could isolate strategies at the venue,
   not merely in our own ledger.

---

## 13. Open risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Maker fills are adversely selected | **High** | Four mitigations in §5.3; measured from logged fills |
| Paper mode cannot simulate queue position | **High** | Paper fills modelled pessimistically: fill only when the book trades *through* our price, never *at* it |
| Price band is unvalidated on 15m markets | **High** | §9 logs everything needed to re-fit; band is configurable |
| Maker fee change (post-Apr 2025) erodes the paper's edge | Medium | Fees computed from the live `fee_multiplier`, never hardcoded |
| Dropping `idx_trades_one_open` | Medium | §7 compensating controls; predictor pinned to 1 |
| Ladder leg risk | Medium | Off by default; order groups; immediate unwind |
| Multi-position drawdown is faster than single-position | Medium | Circuit breakers become per-strategy **and** global |
| Paper/live switch may never find a flat moment | Medium | See below |

**The runtime paper/live switch needs one adjustment.** `run()` applies a
requested mode change only on the branch where the ledger reports no open trade.
With up to `SCALP_MAX_CONCURRENT` positions plus a predictor position, a busy
bot may rarely be flat, so a requested switch could queue indefinitely. The fix
is to stop opening *new* positions as soon as a switch is requested and apply it
once the last one closes — draining rather than waiting for coincidence. The
invariant that a switch lands only when flat is preserved; only the waiting
strategy changes. The dashboard already displays the pending state, so a
draining switch is visible rather than silent.

The paper-fill model deserves emphasis. A paper engine that fills a resting
order whenever the market trades *at* our price will systematically overstate
maker performance, because in reality we sit behind the existing queue. Filling
only when the book trades *through* the price is deliberately pessimistic — it
is better for paper mode to understate this strategy than to overstate it.

---

## 14. What this spec does not do

- No Polymarket order path, wallet, USDC or EIP-712 signing.
- No backtester or counterfactual labeller (decision 8).
- No change to the predictor's entry gates, sizing or settlement monitoring.
- No live ladder arbitrage.
- No new runtime dependency.
- No licence check anywhere on the trading path.
