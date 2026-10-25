# Execution, costs, and cash implementation memo

Read-only source assessment, October 8, 2026. Production code was not changed, tests were inspected but not executed, and no account, broker, network, or credential access was used. This memo implements the planning implications of priorities 2 and 7 and the cash-efficiency experiment in the September 30 alpha review. HEAD remains `7fd145a411e8f8a925b28cd4343dbf4f9a98a950`; preserve and hash the working tree because HEAD alone does not describe its implementation.

## Verified current behavior and existing functionality

| Area | Current implementation | Consequence |
|---|---|---|
| Intent sizing | `amat/src/amat/execution/__init__.py:build_instructions` sizes from decision close, applies minimum notional/weight band, nets in-flight quantities | Preserve causal sizing and delayed-order netting; do not replace with fill-window hindsight |
| Simulated funding | `SimulatedExecutionModel.execute` sorts sells before buys, buys alphabetically; participation clips buys; insufficient cash rejects full purchase | Scarce cash makes results symbol-order dependent; no batch funding authorization exists |
| Simulation integration | `amat/src/amat/backtest/runner.py:_schedule_decision`, `_settle_session`, `_clamp_sales_to_holdings`, `_rescale_pending_across_split` | Funding state must work with delayed orders, split restatement, partial fills and actual holdings |
| Plan artifact | `amat/src/amat/execution/intent.py:ExecutionPlan`, `TradeIntent`, `build_execution_plan`, schema `1.0.0`; plans sort intents alphabetically across sides | Paper order order is different from simulated sells-first ordering |
| Planning boundary | `amat/src/amat/planning/__init__.py:build_plan`; `amat/src/amat/cli/main.py` plan command | Account equity and quantities are inputs; available settled cash/reservations are absent |
| Paper behavior | `executor/src/executor/submit.py:submit_plan` iterates plan order; `_resolve_quantity` supports rounding and sale clamping; `alpaca_paper.py:submit_market_order` sends market DAY orders | No cash funding allocation, no sell completion barrier, and no guarantee of opening-window VWAP |
| Existing account data | `executor/src/executor/broker/alpaca_paper.py:Account` includes cash and buying power; daemon observes cash | Do not infer spendable cash from equity minus stale marked holdings, or substitute margin buying power |
| Retry controls | `executor/src/executor/daemon.py` reuses persisted plan; rejects unrelated open orders; `submit.py:client_order_id_for` binds to original quantity | Keep identities stable through retry/partial fill; changed quantities currently create new IDs |
| Existing reports | `executor/src/executor/report/metrics.py:compute_execution_quality` matches IDs, reports filled/partial/unmatched/overfilled orders and decision-close-to-fill adverse markout | Extend this; do not claim basic execution attribution is absent. Arrival quotes, fill-event decomposition and cash reconciliation are absent here |
| Cost model | `CostConfig`, `one_way_cost` flat bps; `Fill.cost_cash` books into accountant fees | Conditional costs require a new declared model; changing fill price and adding same cost separately would double count |
| Cash | `CashConfig`, `SessionAccountant.advance/_accrue`, `_require_constant_cash_rate` | Constant yield sensitivity already exists; `treasury_3m` intentionally fails. Primary zero yield enforced in config and `autoresearch/contracts.py` |

A specific accounting-record edge case needs a regression: when participation first records a remainder and funding subsequently rejects the instruction, `_miss(instruction, INSUFFICIENT_CASH)` records the original full quantity. The same overlap can occur with a stress drop after participation clipping. Existing missed-record counts are therefore unsuitable as unique-order or additive missed-quantity totals. Correct this only in a versioned result schema or clearly labeled corrected replay; preserve historic artifacts.

## Contract decision before implementation

`amat/docs/execution-repo.md` explicitly forbids executor signal/weight/size computation and quantity modification except its documented mechanical rounding behavior. AMAT remains pure research/intent code; the executor owns broker state and submission. Funding-aware resizing cannot be silently inserted into `submit_plan` under schema 1.

Recommended design: schema 2 carries an immutable funding authorization envelope. Strategy output still owns requested targets and maximum quantities; a deterministic, broker-free resolver applies an already authorized policy to timestamped cash/price facts. The executor supplies facts and enforces the envelope; it does not rank assets or invent allocation discretion. Implement the resolver as a pure broker-free module in AMAT. The simulator calls it directly; AMAT exposes an isolated resolver CLI accepting a versioned facts artifact and returning a versioned authorization artifact. The executor supplies facts through its existing subprocess boundary and only verifies/enforces the returned authorization. This preserves the executor’s stdlib-only/no-AMAT-import boundary. Both runtimes pin resolver digest/version and use JSON contract conformance fixtures. Update execution-repo specification to explicitly authorize bounded downward funding resolution. Keep schema 1 path and baseline semantics intact.

A separately distributed tiny shared contract package is a deferred alternative only if dependency and boundary policy deliberately change. Do not duplicate subtly different funding arithmetic in both repositories.

Proposed interfaces (new, not existing):

- `FundingPolicy(policy_id, version, allocation_rule='proportional', reserve_cash, reserve_bps, max_price_age, allow_sale_proceeds, residual_policy='next_decision')`.
- `FundingFacts(as_of, spendable_cash, reserved_cash, observed_prices, price_available_at, completed_sale_proceeds, tradable_increments)` supplied by simulation/adapter. The first implementation can use decision-close inputs and already available cash only; using opening facts requires an explicitly modeled arrival decision stage.
- `resolve_funding(requests, policy, facts) -> FundingAuthorization`: per-intent requested and authorized quantity, reserved budget, estimates, reduction reason, source timestamps, stable IDs, account snapshot/event watermark, parent intent/proposed PolicyState binding, and resolver digest. Plan creation does not mark an intent held or consume a filled-turnover budget; next state transitions reconcile actual outcomes. Authorization never increases quantity or changes side/symbol/target.
- `ExecutionOutcome`: stable plan/intent/authorization IDs; requested, authorized, submitted, filled and disjoint residual quantities; reason codes, order/fill timestamps and associated references. Carry explicit cumulative vs incremental fill semantics.
- `CostEstimate`: estimated fee/spread/impact components, information timestamp, model version, uncertainty. `RealizedCost` is separate and cannot be fed back to the same decision.

Schema 2 is appropriate because permission semantics change. Update both parser/producer, CLI input, fixtures, report adapters, plan digest, run manifests, strategy config and research program identity. Major-1 consumers must refuse major 2 until deliberately upgraded. Historical readers should continue to render legacy artifacts with unavailable fields rather than fabricated values.

## Funding algorithm and causality

1. Form target deltas with existing causal signal code. Preserve raw requests even if funding prevents authorization.
2. Deduct durable reservations for existing orders from usable cash. Resolve price and quantity increments from facts available at the authorization timestamp.
3. Estimate requested purchase notional plus fees and a predeclared gap reserve. Allocate available purchase budget proportionally across requests. Round down; deterministic rounding residue policy must be declared and must not reintroduce alphabetic allocation of economically meaningful cash. Do not add learned utility ranking in the first experiment.
4. A submitted sale is not cash. Either initially fund buys only from already spendable cash, or model sell acknowledgments/fills and refresh spendable funds before a later buy authorization stage. The latter needs intraday time/event data and a declared deadline. Simulator instant same-window sell proceeds are a distinct legacy assumption, not evidence of paper parity.
5. Affordability is estimated before execution, so an actual gap may still produce a rejected order. Affordable partial purchases require either pre-submission observable prices and explicit authorization, or a supported budget-constrained order contract. Never recalculate purchase quantities using completed future five-minute VWAP and label it executable. Market DAY share orders do not enforce a hard maximum debit; reserve breaches must be observable and modeled, not silently deemed impossible.
6. Partial/rejected residuals return to the next decision, consistent with current specification. No same-session top-up mechanism in this initial scope. Durable authorization persists across restarts; refresh of facts must not generate a second order for an intent already submitted or in uncertain state.

Simulation can evaluate two clearly separated policies: decision-close reserve sizing on current bar granularity; and a later arrival-based resolver after timestamped price/quote observations exist. Keep legacy behavior as the control. Forecasted participation must use trailing completed volume; realized full-window volume may constrain simulated attainable fills but cannot determine an opening decision size.

## Conditional costs and shortfall

Start by extending measurement rather than fitting an unidentifiable impact model. Extend order/quote journal and `Order`/report structures with observed arrival bid/ask/mid timestamp, submission timestamp, fill-event quantity/price/time, actual fee data, cancel/reject state and completeness flags. The existing `submitted_at` and average fill price alone do not support separating arrival cost from delay or adverse selection.

Report signed currency attribution and bps on declared denominators: signal-close-to-arrival movement; arrival-to-fill execution movement; explicit fees; and opportunity cost for unfilled residual at a predeclared evaluation horizon. Missing or stale arrival observations remain null and suppress unsupported estimates. Decision-to-fill markout remains available with its current honest label. Opportunity cost is a counterfactual reporting diagnostic, not ledger P&L.

Add `CostModel` protocol at the simulated execution boundary, retain `FlatBpsCostModel` exact behavior, then opt-in a bounded conditional model from asset, trailing liquidity/volatility, arrival spread when available, size and causal participation estimate. Frozen training calibration and uncertainty stress must accompany the model version. Account for costs exactly once; either keep total implementation expense in `cost_cash` with component metadata or migrate attribution explicitly. Update `amat/src/amat/reporting/artifacts.py`, backtest attribution and trade monitor consumers.

Exit liquidity needs a new optional symmetric/bounded participation policy. Existing unit test deliberately asserts risk-reducing sales are never clipped; change the new-policy expectation without altering legacy tests. Maintain exit intent and report residual risk when liquidation cannot finish. Do not bypass liquidity for a risk exit merely to produce a reassuring drawdown curve. Broader risk module determines urgency; execution determines attainable fills. Explicit forced-reduction reasons must bypass `build_instructions` discretionary minimum-weight deadbands as well as selection retention rules, with below-minimum tradability still recorded. Pending-risk diagnostics must include failed-sale/full-buy adverse cases rather than rely exclusively on signed net pending quantities.

Opening-price execution is also not currently configurable (`price_method` is literal `vwap`). A next-open variant requires a new execution contract and verification of available raw bars in `MarketPanel`, not a flag that silently reuses VWAP. Prefer that bounded alternative before building a slicer; intrawindow slicing, replacement IDs and rate scheduling are separate scope.

## Cash-efficiency extension

Reuse existing `SessionAccountant` constant-rate sensitivity; do not rebuild accrual. Add a versioned rate provider/input series with observation/availability/effective timestamps, actual account eligibility, day-count and posting conventions, missing-data policy and immutable snapshot identity. Extend runner rate lookup and `AccountingBoundary` explicitly rather than letting `_require_constant_cash_rate` accept unsupported sources.

Observed Treasury yield is a diagnostic proxy until it represents an attainable account return. A bill/ETF sleeve requires actual instrument holdings, cost, settlement and mark accounting; it must not simultaneously receive synthetic cash interest. Clarify timing: simulation currently passes opening `ledger.cash` to execution before accountant accrual and distributions. Crediting interest in accounting does not automatically make it spendable for that morning's authorization. A new cash model needs explicit cash availability/settlement timing, including weekends and delayed posting.

Create a new research program for nonzero primary cash, preserving current program's two-bps/zero-yield invariant. Metrics must consume a separately specified risk-free benchmark series for excess-return Sharpe; this need not equal actual attainable account cash yield. Portfolio returns include actual cash yield once, and the risk-free return is subtracted once from the portfolio return independently of cash weight. Report strategy trading return, financing/cash return and total growth separately. Dependencies: metric schema and data availability contract.

## Phased implementation and verification

**E0 — Freeze contracts and baseline (depends on metric/lineage work).** Save source/environment/config/data digests and legacy replay fixtures. Specify schema-2 envelope, shortfall metric contract, settlement convention and cash eligibility. Additive observational fields can land earlier; authorization changes cannot.

**E1 — Pure funding resolver and lifecycle.** Implement resolver plus exact Decimal/unit tests and shared conformance fixtures. Wire decision-close reserve policy into simulator and inert plan generation. Add IDs/disjoint residual quantities; preserve legacy mode. No default strategy change.

**E2 — Executor dry-run parity.** Update parser, `plan_source.build_plan`, CLI and planning inputs; persist authorization before submission attempts; staged sells/buys only under explicit new contract. Reconcile restarts/open orders and missing acknowledgments. Fake-client tests only initially, followed by observational paper work under the separate promotion process.

**E3 — Measured costs and execution variants.** Extend journal/report model; implement flat-cost protocol then causal conditional model and exit-liquidity option. Compare VWAP control with independently declared arrival/next-open variant. Funding and cost variants run separately before combining.

**E4 — Cash series sensitivity.** Implement dated rate inputs and attainable-return modeling; new research program/benchmark convention; no mixing with alpha evidence.

Existing suites to extend:

- `amat/tests/unit/test_execution_and_accounting.py`: exact funding, costs, participation, no fabricated fills, in-flight netting, accrual and reconciliation.
- `amat/tests/unit/test_execution_plan.py`: deterministic artifact, shared instruction logic, schema compatibility and withheld records.
- Backtest integration tests covering delayed execution, corporate actions, liquidity and accounting; locate existing cases by `_rescale_pending_across_split` and `UNBACKED_SALE` before adding fixtures.
- `executor/tests/test_submit.py`, `test_daemon.py`, `test_report.py`: rounding, IDs, dry runs, durable plan reuse, calendar/open-order checks, incomplete reconciliation.

Required new behavioral cases: multi-asset funding invariant under input/symbol permutations; price gaps and fees; partial sale/reject/no bar; zero spendable funds; integer/fractional increments; full quantity conservation; reservation release only on confirmed terminal state; crash before/after submission and uncertain acknowledgment; split while pending; lagged overlapping intents; future-window perturbation cannot alter prior authorization; no borrow or negative cash in simulator; unavailable arrival facts cannot become zero shortfall; arrival decomposition sums to declared total; future rates unavailable to prior session; bill holdings cannot earn duplicate interest.

Before adoption, evaluate unchanged legacy and each funding/cost variant on frozen chronological folds at multiple capital sizes and the existing cost ladder. Report net growth, conventional Sharpe, shortfall, tracking error, residual quantities, target/settled concentration and tail risk. Funding improvement is accepted only if executable behavior and accounting are correct and net benefit survives declared controls; a lower missed-record count alone is insufficient. Preserve v13 defaults unless the full new-program evidence and prospective observation gate pass.
