# Kalshi crypto perpetuals: implementation plan

Status: forward paper data gaps resolved with a qualified external perpetual signal source. Nine frozen strategy presets and a modular strategy API are implemented. See README.md and FORWARD_DATA_VERIFICATION.json for delivered scope and observed coverage. Dated October 19, 2026 (America/Detroit).

## 1. Outcome and boundaries

Create `kalshi-perps/`, an independently installable Python application with its own lockfile, environment, CLI, configuration, tests, database, logs, and deployment unit. It runs the current AMAT primary decision engine through a versioned process boundary and implements Kalshi perpetual market data, instrument normalization, risk admission, paper execution, demo execution, and live execution locally.

No imports from `executor`, `frontend`, or `autoresearch`; no shared writable database, runtime directories, credentials, or account state. No changes to their operating modes or deployment. Sharing the primary strategy means calling its actual implementation, rather than copying its formulas into a new strategy. REST and WebSocket are in scope; FIX, automatic fund transfers, a frontend integration, inverse products, and automatic strategy promotion are deferred.

## 2. Repository findings and strategy identity

The guided frontend currently selects `amat/configs/strategy/amat_group_selection_daily.toml` through `frontend/server/strategy_defaults.py`. That profile is version 14.1.0, uses native v13 sizing/caps, daily selection, and long-only exposure. Its header explicitly identifies it as an opt-in research profile with no promotion. The October 19 engine update also says the original strategy remains the reference. Therefore a UI default is not sufficient authority to choose a live policy.

Use the primary decision implementation in `amat/src/amat/portfolio/__init__.py`: `precompute_decision_inputs` and `decide`, together with the existing validated strategy configuration and applicable risk overlays. Resolve the exact candidate/reference configuration during phase 0 and bind it by hash. There must be no silent fallback to a different config if it is unavailable. Retain the original reference for comparisons; identify the selected test profile explicitly in every run.

Implementation steering: the user explicitly selected the current primary **crypto perps** engine. The retained crypto studies identify the frozen symmetric four-hour BTC/ETH winner (20-day entry, 10-day exit, vol20, 2x) in `autoresearch.crypto_perpetual_study.strategy_weights`. The standalone package now calls that exact implementation out of process. The ETF frontend default described above is background, not this package’s strategy. The existing `autoresearch.perpetual_sim` is a useful source of accounting fixtures, but has synthetic multipliers and only a single-market in-memory wallet. Do not treat its fills, leverage, liquidation model, or synthetic .001 multiplier as verified Kalshi behavior.

### Strategy process contract

Run an AMAT bridge in the AMAT environment, invoked by absolute executable path. The proposed bridge is a small separately tested AMAT entry point; existing operational paths remain unchanged. `kalshi-perps` only knows a strict JSON contract, `strategy-decision/1`, through stdin/stdout. A one-way dependency on the engine service is the deliberate exception to complete runtime independence.

Request: request/run ID, decision timestamp, completed normalized candle panel and ordered symbols, calendar/annualization policy, exact engine/config hashes, actual equity/positions and persisted risk state when required. Response: matching identity, signed target weights, decision reasons, warm-up status, input watermark, and hashes. Finite scalar values and deterministic symbol ordering are required. Reject unknown versions, mismatched identities, future data, missing history, timeout, or stale responses. Persist requests/responses for replay. A bridge failure creates no new exposure; reconciliation and cancellation remain available independently.

Preserve current long-only policy initially. Perpetuals support shorts operationally, but enabling a short strategy is a separately versioned research decision. Preserve the current signal formulas and constraint ordering. Define a crypto-specific universe and UTC 24/7 session/calendar profile; using 365 daily observations instead of 252 is an explicit configuration change requiring validation. Do not relabel ETF results as crypto evidence. If the current engine cannot accept these sessions, extend its calendar boundary in a separate tested change; do not patch private methods from this package.

## 3. Package layout and module responsibilities

```text
kalshi-perps/
  pyproject.toml                  # own dependencies and kalshi-perps CLI
  uv.lock
  README.md
  .env.example                   # names/paths only
  configs/{paper,demo,live}.toml
  docs/{implementation-plan,operations,api-contract}.md
  src/kalshi_perps/
    cli.py                       # explicit commands, composition root
    config.py                    # mode, endpoints, account and policy validation
    domain/{instruments,events,orders,positions,decisions}.py
    strategy/{client,contract}.py # primary-engine process adapter
    venue/{auth,rest,websocket,schemas,errors}.py
    instruments/{registry,normalization}.py
    marketdata/{book,candles,recording}.py
    risk/{sizing,admission,limits}.py
    execution/{service,lifecycle,reconciliation}.py
    brokers/{base,paper,kalshi}.py
    accounting/{ledger,funding,settlement,margin}.py
    storage/{database,migrations,journal}.py
    reporting/{metrics,status}.py
    runtime/{clock,runner,health}.py
  tests/{unit,contract,replay,integration}/
```

Interfaces: `StrategyClient.decide`, `Broker.submit/cancel/reconcile`, `InstrumentRegistry.resolve`, `RiskGate.evaluate`, `EventStore.append`, and injectable `Clock`. Domain and accounting modules do not depend on HTTP or CLI. Paper and Kalshi brokers consume the same validated order intent; neither computes strategy signals. Use Decimal for monetary and quantity arithmetic, decimal strings on the wire, UTC internally, and a single account writer with transactional SQLite/WAL storage initially.

## 4. Contract multipliers and price units

Source: existing authenticated GET-only production snapshot captured October 19 at 20:33:49 EDT (`2026-10-20T00:33:49Z`), under `amat/outputs/crypto-directional-2026-10-19/kalshi-inventory/`. This is a dated planning reference, not a fresh entitlement or tradability check. Its SHA-256 is `88c70bbf3df411699f0a89d83f5fb3a5baac7cd9de1c7b39c8fe23142499c9e9`.

The API exposes both `contract_size` and `underlying_multiplier`. Store both without conflating them with leverage. The inventory contains 20 crypto rows, including an inactive/zero-priced product that must remain excluded from trading.

| Ticker | API title | contract_size | underlying_multiplier | API price tick | Snapshot status |
|---|---|---:|---:|---:|---|
| KXAAVEPERP | 0.01 AAVE | 0.010000 | 1.000000 | 0.0001 | active |
| KXADAPERP | 1 ADA | 1.000000 | 1.000000 | 0.0001 | active |
| KXBCHPERP | 0.01 BCH | 0.010000 | 1.000000 | 0.0001 | active |
| KXBNBPERP | 0.001 BNB | 0.001000 | 1.000000 | 0.0001 | active |
| KXBTCPERP | 0.0001 BTC | 0.000100 | 1.000000 | 0.0001 | active |
| KXDOGEPERP | 100 DOGE | 100.000000 | 1.000000 | 0.0001 | active |
| KXETHPERP | 0.001 ETH | 0.001000 | 1.000000 | 0.0001 | active |
| KXHYPEPERP | 0.1 HYPE | 0.100000 | 1.000000 | 0.0001 | active |
| KXKPEPEPERP | 1K kPEPE | 1000.000000 | 1000.000000 | 0.0001 | inactive |
| KXKSHIBPERP | 1K kSHIB | 1000.000000 | 1000.000000 | 0.0001 | active |
| KXLINKPERP | 1 LINK | 1.000000 | 1.000000 | 0.0001 | active |
| KXLTCPERP | 0.1 LTC | 0.100000 | 1.000000 | 0.0001 | active |
| KXNEARPERP | 1 NEAR | 1.000000 | 1.000000 | 0.0001 | active |
| KXSOLPERP | 0.1 SOL | 0.100000 | 1.000000 | 0.0001 | active |
| KXSUIPERP | 10 SUI | 10.000000 | 1.000000 | 0.0001 | active |
| KXUNIPERP | 1 UNI | 1.000000 | 1.000000 | 0.0001 | active |
| KXVVVPERP | 0.1 VVV | 0.100000 | 1.000000 | 0.0001 | active |
| KXWLDPERP | 1 WLD | 1.000000 | 1.000000 | 0.0001 | active |
| KXXRPPERP | 1 XRP | 1.000000 | 1.000000 | 0.0001 | active |
| KXZECPERP | 0.01 ZEC | 0.010000 | 1.000000 | 0.0001 | active |

For ordinary crypto rows (`underlying_multiplier=1`), the captured quote is USD per contract: BTC at approximately $81,593 per BTC appears as an API price of $8.1593 for 0.0001 BTC. ETH approximately $2,396.30 appears as $2.3963 for 0.001 ETH. This unit interpretation is supported by the snapshot's quote/notional fields and must be confirmed against demo fills and account statements before live admission.

Define typed prices: `underlying_usd_per_unit` and `api_usd_per_contract`; never accept an untyped generic price in sizing. For validated ordinary contracts, let s be contract size, S underlying price, P API contract price, q signed contracts:

- `P = s * S`; base exposure = `q * s`.
- Notional = `abs(q) * P = abs(q) * s * S`.
- Price P&L = `q * (P_exit - P_entry) = q * s * (S_exit - S_entry)`.
- In API-price space the P&L coefficient is 1; in underlying-price space it is s. Applying s to an already scaled API price again is an error.
- Percentage funding cash flow = `-q * P_event * rate`; fees derive from filled contract notional and the applicable account fee terms.
- Desired contracts = target USD exposure / API contract price, rounded toward zero to the allowed count step; submit the delta after subtracting positions and outstanding order commitments.
- Leverage changes margin requirements; it does not multiply fixed-contract P&L a second time.

Example: 100 BTC contracts at S=$80,000 represent 0.01 BTC and $800 notional, with P=$8. A move to S=$81,000 (P=$8.10) earns $10 before costs. At 2x policy leverage the initial collateral estimate would be $400, subject to actual venue risk requirements.

The `kSHIB` and `kPEPE` rows have BOTH fields set to 1000 and ambiguous display-unit conventions. Preserve these exact values; do not multiply them together or infer a million-token exposure from their names. Mark both `normalization_unverified` until authoritative economics and demo statement examples establish base units, API quote mapping, and funding notional. Discovery includes them; order admission excludes them until that gate passes. Inactive/zero-priced products remain excluded even after normalization is resolved.

Refresh metadata on startup and periodically; persist raw responses, retrieval time, market ID, exchange index, market version, status, ticks, fractional flags, funding units, marks, and leverage estimates. Require a positive valid price, complete metadata and a verified normalization mapping. Unknown instruments fail closed. A changed multiplier/tick/version freezes new exposure, cancels affected open orders where possible, and requires renormalization before resuming. Never reinterpret historical fills using current metadata.

## 5. Kalshi connectivity and API contract

Use the official [Perps API](https://docs.kalshi.com/margin), [market metadata](https://docs.kalshi.com/margin-rest/market/get-market), and [create-order schema](https://docs.kalshi.com/margin-rest/orders/create-order), checked October 19, 2026.

| Environment | REST base | WebSocket URL |
|---|---|---|
| Demo | `https://external-api.demo.kalshi.co/trade-api/v2/margin/` | `wss://external-api-margin-ws.demo.kalshi.co/trade-api/ws/v2/margin` |
| Production | `https://external-api.kalshi.com/trade-api/v2/margin/` | `wss://external-api-margin-ws.kalshi.com/trade-api/ws/v2/margin` |

Build read clients for entitlement, markets/books/trades/candles, balances, positions, orders/fills, account risk, fees, and funding estimates/history/payments. Phase 0 freezes the exact current operation paths, schemas, pagination, rate budgets and available account permissions in `api-contract.md`; implement no guessed risk endpoint. Restrict the write client to supported order operations. No balance-transfer capability in v1.

Sign timestamp + method + request path with the configured key algorithm, matching official auth rules. Keep credentials in package-specific environment variables and key paths; redact headers and never probe neighboring projects for keys. Use request deadlines, bounded retries for safe reads, rate limiting, clock-drift detection and redacted structured errors. Do not assume write idempotency from a locally chosen client ID.

Order schema uses bid/ask, contract-count and dollar-price strings, explicit time-in-force and self-trade prevention. Use actual nonzero market version checks. V1 supports marketable IOC limits and FOK where needed; no unbounded market orders. Current docs restrict `reduce_only` to IOC/FOK. Reversals close with reduce-only first, reconcile, then admit the new side against fresh margin. GTC/amend support is a later feature with separate paper modeling and tests. Do not assume margin supports batch orders or queue-position endpoints.

Consume margin orderbook, ticker, trade, fill and user-order channels. Margin timestamps are epoch milliseconds. Bootstrap books, check documented sequence continuity, deduplicate, reconnect with bounded backoff, and resnapshot on gaps. A WebSocket reconnect triggers REST reconciliation before resuming. Persist settlement, liquidation and reference marks separately. Use the correct mark for each accounting purpose; none is a guaranteed executable quote.

## 6. Shared trading flow and durable state

```text
Market events -> validated normalized candles -> primary AMAT engine
             -> target weights -> contract sizing -> account risk gate
             -> journaled intent -> paper/demo/live broker
             -> fills/funding/settlement -> ledger -> reconciliation
```

Use completed candles only, explicit UTC daily boundaries initially, and a documented decision cadence. Preserve actual missing intervals; warm-up is per symbol and cannot invent pre-listing Kalshi history. External spot/proxy history may support research warm-up only with an explicit source policy and basis checks; it is never proof of historical Kalshi execution.

State machine: planned -> risk_approved -> submission_pending -> acknowledged -> partially_filled -> filled/canceled/rejected. `submission_unknown` is a separate durable condition. Persist intent, immutable payload hash and client ID before network submission. On timeout or restart, query orders and fills with pagination and reconcile the original identity; do not create a replacement while the outcome is unknown. Reserve conservative exposure for unresolved orders. Cancellation acknowledgments do not undo fills. Process fill IDs exactly once and prevent stale events from reverting terminal state.

Journal strategy identity, raw event reference, normalization version, pre/post risk, venue/client order IDs, cumulative fill quantity, remaining reservation, fees, funding, settlements and timestamps. Commit ledger updates and processed-event markers atomically. Isolate databases by mode, environment and account; reject reuse across identities. Enforce one writer through a process lease. Rebuild state from the journal and compare to venue balances/positions/open orders before sending anything new.

## 7. Paper trading

Paper is the default and has no venue order-write client. Support deterministic recorded replay and forward paper using real Kalshi market data. Select data environment explicitly; production quotes do not imply production execution. Persist starting collateral and all state across restart; reset requires a new run/account identity.

Implement one portfolio cash/margin ledger with symbol positions, average entry, realized/unrealized P&L, reserved margin, fees, funding and settlement cash flows. Separate marked total P&L from cash settlement to avoid counting variation margin twice. Use observed funding timestamps/rates/marks with stable deduplication IDs; unknown funding is flagged and blocks performance qualification rather than silently becoming zero.

Match IOC orders against recorded executable bid/ask depth at the modeled arrival time, honoring limits, quantity steps, partial fills and available liquidity. FOK fills fully or not at all. Inject latency and adverse slippage without violating the limit. Do not assume fills from midpoint, last trade, mark, or touched OHLC. Top-of-book-only data permits a capped top-of-book approximation, explicitly labeled. Shared event ordering is venue timestamp plus recorded sequence/receive order; equal-time ambiguity must be recorded or conservatively replayed.

Model margin conservatively from verified account requirements, outstanding orders and collateral buffers. Venue risk admission remains authoritative in live. Until exact risk tiers/liquidation semantics are established, paper liquidation is a disclosed approximation and cannot certify survival. Preserve negative equity in gap scenarios, halt that paper account, and report the deficit.

Reports: equity/drawdown, realized/unrealized P&L, gross/net notional, effective leverage, free/reserved collateral, fees, funding, turnover, rejection reasons, stale periods, fill assumptions and metadata/strategy hashes. Show replay and forward paper separately.

## 8. Demo and live

Treat execution mode (`paper`, `demo`, `live`) separately from market-data environment. Demo uses actual demo order endpoints and credentials; local paper uses its own simulated ledger. Live requires explicit CLI selection, an account-bound deployment configuration, current entitlement, verified metadata, exact policy hash and successful reconciliation. Paper configuration cannot instantiate a live broker.

Risk admission checks account equity freshness, available collateral from the venue, initial/maintenance headroom, all pending orders, portfolio gross/net exposure, per-symbol concentration, policy leverage, spread, depth, price-band constraints and daily loss/drawdown limits. Use conservative side/size-dependent margin estimates and the venue's supported risk checks; leverage estimates are informational, not executable guarantees. Start at at most 1x gross policy exposure and one explicitly selected liquid symbol. Define numerical collateral, order, loss and spread limits in the rollout profile before any live run; configuration rejects missing values.

Stale data, sequence gaps, metadata changes, account drift, unknown submissions or loss limits stop new risk. Cancel outstanding risk-increasing orders and reconcile. A kill command cancels orders; an explicitly separate flatten command submits bounded reduce-only closes and reports remaining exposure until confirmed. A stopped process does not imply a flat account. Keep enough independent transport capacity for cancels and reconciliation under load. Alerts cover unresolved submissions, account mismatch, margin deterioration and failed cancels.

## 9. Implementation sequence and acceptance gates

| Phase | Deliverables | Required acceptance evidence |
|---|---|---|
| 0 — Freeze contracts | Exact strategy/config/source hashes; engine bridge contract; API operation inventory; dated crypto registry and unit fixtures | Primary profile identified; no silent strategy switch; ordinary price mappings proven; k-token ambiguity explicit; account risk/fee/funding sources identified |
| 1 — Standalone foundation | Package/lockfile/CLI, config, domain types, SQLite journal, fake clock, mode isolation | Independent install/test; no imports from other trading apps; strict account/mode boundaries; transactional restart replay |
| 2 — Read-only venue | Auth, REST pagination, market registry, WS book recovery, candle recorder | Redacted fixtures; signing/clock tests; no-gap book recovery; metadata-change and stale-data tests; GET-only smoke report |
| 3 — Engine integration | AMAT process bridge, normalized completed panels, hashed decisions | Same inputs/config yield same primary-engine weights; prefix/causality tests; missing history blocks; crypto calendar changes separately validated |
| 4 — Paper execution | Sizing, depth-aware IOC/FOK broker, portfolio ledger, fees/funding/settlement, reports | All ordinary multipliers validated; conservation of equity; deterministic replay including restarts; no live-write capability in paper |
| 5 — Demo integration | Write broker, durable order lifecycle, reconnect reconciliation, kill/flatten | Partial fill/cancel races, dropped acknowledgments, duplicate/delayed fills, crash recovery and reduce-only behavior demonstrated against demo |
| 6 — Forward qualification | Forward paper + matched demo on same recorded decisions and market data | At least 14 consecutive calendar days including funding and settlement boundaries; zero unexplained ledger drift/duplicate fills/unknown orders at completion; predeclared numerical execution tolerances pass |
| 7 — Limited live | Explicit deployment profile, one-symbol small allocation, operational runbook | Account/limits/policy pinned; operator explicitly starts live; first fills/fees/funding reconcile to statements; rollback and independent cancel path demonstrated |
| 8 — Expansion | Additional verified crypto instruments, exposure increases, optional shorts/resting orders | Separate promotion per change, sufficient warm-up/liquidity, regression and forward evidence; k-tokens require completed unit proof |

Phases are dependency ordered. API entitlement, sufficient history, instrument normalization or unresolved venue semantics can block a symbol without blocking work on the package. Fourteen days is an operational qualification minimum, not evidence of strategy profitability. Freeze a statistical strategy-evaluation protocol and capital decision separately; existing ETF/proxy/Binance results do not establish a Kalshi edge.

## 10. Meaningful verification

- Unit economics: parameterize every crypto registry row; verified rows have golden notional/P&L/funding/fee/tick cases, unverified rows must reject admission. Include BTC example above, DOGE/SUI large contract sizes, negative positions, reversals and rounding toward zero.
- Accounting invariants: adding collateral and realizing/settling P&L preserve equity appropriately; funding and fills apply once; replay after restart equals uninterrupted state; liquidation deficits remain visible.
- Execution faults: timeout after venue acceptance, crash before acknowledgment storage, duplicate clients, fill during cancel, partial IOC, FOK insufficient depth, unexpected version, stale mark, 429, clock drift, WS gap and account mismatch.
- Strategy equivalence: golden fixtures against the actual AMAT functions with identical data/config; no copied implementation; incomplete candle mutation cannot change earlier decisions. The broker mode must not change pre-execution decisions for identical account state.
- Environment isolation: paper cannot create a signed order-write request; demo never resolves production write hosts; live rejects wrong account/policy/database. Verify no secret material appears in fixtures or logs.
- Qualification: compare paper and venue fills on recorded arrival/depth, fees and funding with declared tolerances; investigate every difference rather than demanding impossible exact fill parity. Run package lint/types/tests plus targeted AMAT bridge regression tests. Existing packages need broader regression only if shared engine code changes.

## 11. CLI and operations

The user requested a small command surface. The implemented CLI has exactly three commands:

```sh
kalshi-perps run
kalshi-perps status
kalshi-perps stop
```

Configuration selects paper, demo, or live. `run --recording FILE` replays paper data;
`run --once` observes one forward step; `stop --flatten` also attempts bounded closes.
`run` resumes a previously stopped account. See README.md for paths and qualification limits.
No background activation or venue order placement occurred during implementation.

## 12. Decisions to close during phase 0

1. Exact primary test profile and reference identity. Use the current primary AMAT code; the daily group-selection UI default is a research candidate until explicitly selected and validated for crypto.
2. Crypto universe grouping, calendar, annualization and history source. BTC/ETH are reasonable initial candidates only after liquidity/history checks; discovering all instruments does not enable them.
3. Authoritative kSHIB/kPEPE unit mapping; exact API account risk semantics, variation-margin behavior and liquidation details. Keep unresolved instruments/features gated.
4. Account-specific fee tier, risk limits, starting paper collateral, demo/live account identity and operational tolerances. These are explicit deployment inputs, not guessed defaults.

## Sources and evidence

- Local primary code: `amat/src/amat/portfolio/__init__.py`, `amat/src/amat/planning/policy.py`, `amat/src/amat/backtest/runner.py`.
- Current test/default identity: `frontend/server/strategy_defaults.py`, `amat/configs/strategy/amat_group_selection_daily.toml`, `docs/research/strategy-engine-update-2026-10-19.md`.
- Existing research limitations: `docs/research/crypto-perpetual-simulator.md`, `docs/research/crypto-perpetual-study.md`.
- Dated production inventory: `amat/outputs/crypto-directional-2026-10-19/kalshi-inventory/{markets,status,fees,historical-coverage}.json`. Preserve as evidence; implementation must create its own registry/cache.
- [Official environment and channel overview](https://docs.kalshi.com/margin).
- [Official instrument metadata schema](https://docs.kalshi.com/margin-rest/market/get-market).
- [Official order, authentication, and reduce-only constraints](https://docs.kalshi.com/margin-rest/orders/create-order).
- [Official BTC contract specification](https://help.kalshi.com/en/articles/15357587-btc-perpetual-futures-contract-specifications).
- [Published perps fee schedule](https://kalshi.com/fee-schedule?product=perps); reconcile against actual account terms before use.
