# The execution repository: specification

This repository forms decisions. It does not place trades, and it will not gain
the ability to. Execution belongs in a separate repository that imports `amat`
as a library, consumes the artifact described below, and owns everything this
one deliberately refuses: the trading calendar, the account, the broker session,
and the submission itself.

This document is the contract between the two.

---

## 1. Why the split is real and not cosmetic

Three reasons, in order of how much they cost when ignored.

**Blast radius.** A research repository is optimised for reproducibility: it
reruns freely, rewrites outputs, and is edited constantly. An execution
repository is optimised for the opposite — it must be boring, rarely changed,
and auditable line by line. Those are incompatible development styles to hold in
one tree, and the failure mode of mixing them is that a refactor motivated by a
backtest changes what gets submitted.

**Evidence integrity.** This repository's guarantees — immutable snapshots, a
network surface of one read-only host, no path to an account — are enforced by
tests and hooks that a broker dependency would have to weaken. Once weakened
they no longer certify anything about the historical results either.

**Credential surface.** Research code is where exploratory scripts, notebooks,
and agents run. Account credentials should never be reachable from that process
at all, and the cleanest way to guarantee it is for the code that holds them to
live somewhere the research code cannot import.

## 2. What this repository guarantees to the executor

- Every plan is derived from completed sessions only. A decision labelled
  `as_of_session` was formed after that session's authoritative close.
- Every plan states a session it may not execute before. That constraint is
  enforced in the domain type that produced it, not merely documented.
- Every plan carries the configuration digest, snapshot id, code revision, and
  the count of sessions appended past the snapshot's frozen manifest.
- Target weights are non-negative, capped per symbol and per cluster, and sum to
  no more than the configured gross cap. Long-or-cash: there is no short.
- A trade the rebalance rule declined to emit is reported in `withheld`, never
  dropped.
- A held symbol outside the strategy universe is reported in
  `unrecognized_holdings` and never traded.
- Identical inputs produce a byte-identical plan.

## 3. What this repository will never provide

- Order submission, order status, account balances, positions, or fills.
- A trading calendar. It has no holiday or half-day data.
- An expected fill price. `reference_price` is the decision session's close, used
  to convert a weight into a share count. Nothing more.
- Any credential handling beyond the two read-only market-data variable names.

Positions and equity are **inputs** to plan generation, supplied by the caller.
The executor is the only component that knows what the account actually holds.

---

## 4. The artifact

Produced by `amat plan`, schema `1.0.0`. Version is major.minor.patch; an
executor **must refuse** a major version it was not written against. Tolerating
an unknown shape is how a renamed field becomes a wrong-sized position.

```
schema_version                 "1.0.0"
generated_at                   ISO-8601, UTC
engine                         "v1-frozen" | "v2-managed"

strategy_name, strategy_version
config_sha256                  content digest of the configuration
snapshot_id                    immutable data snapshot
appended_sessions              sessions added past the frozen digest
code_revision                  git commit, or "<undetected: reason>"

as_of_session                  completed session that formed the decision
earliest_execution_session     execution must occur in a session on or after this
window_start_local             e.g. 09:30:00
window_end_local               e.g. 09:35:00
exchange_timezone              e.g. America/New_York
expires_at                     UTC instant after which the plan must be discarded

account_equity                 decimal string, as supplied
gross_target_exposure          sum of target weights
target_weights                 {symbol: weight}, positive entries only
current_positions              {symbol: decimal string}, as supplied

intents[]                      symbol, side, quantity, reference_price,
                               notional, current_weight, target_weight, reason
withheld[]                     symbol, side, quantity, reason
unrecognized_holdings[]        symbols held but outside the universe
caveats[]                      free text, all of which must be surfaced
```

Every quantity, price, and cash amount is a **decimal string**. Parse it into a
decimal type. A share count that round-trips through a binary float is a wrong
share count.

---

## 5. What the executor must do

### 5.1 Preflight, in this order, all blocking

A plan that fails any check is not executed and not partially executed.

1. **Schema.** Major version matches what this executor understands.
2. **Freshness.** `now < expires_at`.
3. **Session.** The current session is the *first* exchange session on or after
   `earliest_execution_session`, resolved against the executor's own calendar.
   The `expires_at` timestamp is a backstop; this is the binding control. A plan
   whose session was missed is discarded, not executed late — regenerate it from
   the newer close instead.
4. **Window.** Now is inside `[window_start_local, window_end_local]` in
   `exchange_timezone`, and the market is open (not a halt, not a half-day
   close).
5. **Provenance.** `code_revision` is detected and clean, and `config_sha256`
   matches the configuration this executor is authorised for. An undetected or
   dirty revision fails preflight.
6. **Reconciliation.** The broker's positions match `current_positions` within a
   declared tolerance. They will not match if anything moved since the plan was
   generated, which is exactly the condition that must stop execution.
   `unrecognized_holdings` must be empty.
7. **Equity.** The broker's equity is within tolerance of `account_equity`.
   Outside tolerance, every share count in the plan is wrong.
8. **Notional bounds.** Total intended notional and per-symbol notional are
   within the executor's own configured limits — limits it owns, independent of
   anything the plan asserts.
9. **Idempotency.** No plan with this `(config_sha256, as_of_session, engine)`
   has been executed before. See 5.3.

### 5.2 Kill switches

At least these, each independently able to stop submission, each requiring
explicit human action to clear:

- **Global disable flag**, checked immediately before every submission, not
  cached at start-up.
- **Daily notional cap** across all submissions.
- **Order count cap** per session.
- **Drawdown halt** on realised account equity, independent of the strategy's
  own overlay.
- **Divergence halt**: if realised fills differ from `reference_price` by more
  than a configured amount, stop and require review.
- **Dead-man switch**: if the executor cannot confirm the state of a submission,
  it stops rather than retrying.

Default every one of them to the restrictive setting. An executor that starts up
into a permissive state after a crash is a liability.

### 5.3 Idempotency and replay protection

Every submission carries a deterministic client-side identifier derived from
`(config_sha256, as_of_session, engine, symbol, side)`. A duplicate must be
rejected by the broker rather than deduplicated locally, because local
deduplication fails exactly when the local process is the thing that crashed.

Persist the plan and every submission attempt **before** submitting, in a
durable, append-only journal. On restart, reconcile the journal against broker
state before doing anything else.

### 5.4 The execution window, honestly

The backtest fills at the raw VWAP of the 09:30–09:35 window. **No single order
achieves that.** A market order at the open receives the opening print plus
spread; a limit order may not fill at all.

This is a real gap between the simulation and anything attainable, and it must
be closed from one side or the other:

- **Approximate it**: slice each intent across the window and accept tracking
  error against the modelled fill; or
- **Change the assumption**: re-run the strategy with a next-open fill model,
  which the robustness protocol already lists as a required alternative, and
  execute against *that* number instead.

The second is more honest and is the recommended path. Whichever is chosen, the
executor must journal realised fill prices so the gap can be measured rather
than assumed. Measured slippage against `reference_price` is the single most
important number the executor produces.

### 5.5 Fractional shares

Plans emit fractional quantities. Brokers restrict them: typically market orders
only, day-only time in force, and only for instruments flagged as fractionable.

The executor must check fractionability per instrument, round **down** to the
tradable increment, and record the residual. Rounding up buys more than the plan
asked for.

### 5.6 Partial fills, rejects, and halts

- A partial fill is recorded and **not** topped up within the same session. The
  next plan sees the resulting position and decides afresh; chasing a fill
  intra-session is a policy the strategy never modelled.
- A reject is recorded with its reason and does not trigger an automatic retry.
- A halt mid-window stops the remaining intents for that session.

In all three cases the account simply differs from target until the next plan.
That is the designed behaviour — the strategy rebalances daily and tolerates
being off-target, which is what the 2-point rebalance band is for.

### 5.7 Journalling

Append-only, one record per event, never rewritten: plan received, preflight
result per check, each submission, each fill, each reject, each kill-switch
trip, end-of-session reconciliation. This journal is the only evidence that will
exist about what actually happened.

## 6. What the executor must never do

- Modify target weights, quantities, or sides. It executes a plan or refuses it.
- Compute a signal, a weight, or a size of its own.
- Trade a symbol not in `intents`.
- Execute a plan whose session it missed.
- Continue past a failed preflight check by any automatic path.
- Hold credentials in source, configuration files, or logs.
- Import anything from this repository other than the plan-reading surface.

---

## 7. Suggested layout

```
amat-execution/
  pyproject.toml            depends on: amat (as a library), the broker SDK
  src/amat_exec/
    plan.py                 read and validate the artifact; refuse unknown major versions
    calendar.py             exchange calendar; resolves "the next session"
    preflight.py            the checks in 5.1, each independently testable
    limits.py               kill switches (5.2), restrictive by default
    broker.py               the only module that touches the broker SDK
    reconcile.py            broker state vs. plan state, with tolerances
    journal.py              append-only event log
    run.py                  the single entry point
  tests/
    ...                     every preflight check has a test that it *blocks*
```

Keep `broker.py` the only module importing the SDK, and inject it everywhere
else. The test suite must be able to run the entire path with no network and no
credentials.

## 8. Before any of this runs

Neither engine is currently promoted, and promotion in this project means only
"eligible for shadow/paper operational testing" — not "safe for capital."

As measured on `alpaca-sip-2026-08-08` over 2016-12-30 to 2026-08-07:

- **v2 fails an automatic rejection condition.** 2021 supplies 71.9% of total
  P&L against a 40% cap. `promotion-gates.md` names "depends primarily on one
  instrument, year, or handful of trades" as grounds for rejection.
- **v1 passes both concentration gates** (best symbol 12.5%, best year 20.9%) at
  Sharpe 0.55 against a 0.50 minimum, which is marginal.
- **Neither beats buy-and-hold SPY** on return or on Sharpe over this sample.
- **Most research gates have never been computed at all**: no walk-forward, no
  locked holdout, no bootstrap intervals, no cost or delay stresses, no
  parameter-neighbourhood analysis, no signal-free allocator comparison.

The honest sequencing is: build the executor, run it in shadow mode against
plans for as long as it takes to trust it, complete the validation protocol, and
treat the capital decision as a separate question answered by evidence that does
not yet exist.

Shadow mode costs nothing and is the only way to discover the gap in 5.4.
