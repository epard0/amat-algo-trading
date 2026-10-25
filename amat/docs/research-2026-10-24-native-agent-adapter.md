# Research-only native action adapter: implementation and parity

This human-directed local run entered October 24, 2026 at 02:36:00 UTC (October 23 at 10:36 p.m. Detroit), covering the 02:40 campaign opportunity. The hard stop is 03:16 UTC. It implements a distinct, inert research policy; it does not change either paper bot, the current strategy default or the ordinary allocator.

**Implemented:** validated proposal → exact-quantity research plan → condition/limit/expiry and evidence gates → native simulated fills/misses → native accounting → source-bound sealed receipt. The 24 preregistered synthetic case/cost checks passed. This is proposal-to-ledger mechanics parity, not market-performance, broker-fill or current-strategy-policy equivalence.

## Functionality and preserved contracts

The new `autoresearch.improvements.agent_native_adapter` module exposes `compile_adapter`, `ObservedWindowEvidence` and `execute_adapter`. Plans retain immutable canonical proposal JSON, the complete original research context and a context-bound plan identity. The compiled plan stores exact decimal quantities, sides, limits, protective triggers, original execution time and expiry. There is no conversion through floating-point weights or the native allocator's 2% rebalance threshold. This is an explicit action policy, not an alteration of v14.3.0's rebalance rule.

The original action validator still runs first. Account, policy, market evidence and original authorized-intent identities must match. New risk remains blocked while any original intent has unresolved quantity; pending cash, restricted cash and the reserve remain unavailable for new purchases. Original intent ID, original quantity, cumulative fills, remaining quantity, account/policy identity, expiry and pending cash are carried unchanged. New adapter action fills and residuals are recorded separately; they do not replace existing authorization.

The adapter uses native `SimulatedOrder`, `SimulatedExecutionModel` and `SessionAccountant.advance`. Purchases use the native 1% supplied opening-volume cap and whole-share rules. Full exits retain native position-transition treatment so legitimate fractional legacy holdings can close fully; partial reductions retain native whole-share behavior. Original requested quantity is conserved as simulated filled quantity plus explicit remaining quantity.

Purchase funding is calculated from the original confirmed balance after pending/restricted/reserve amounts. Buys are executed against that original funding allowance independently of sells. Projected sale proceeds cannot finance same-batch purchases. A new, independently reproduced correction to the inert validator permits backed sells requiring zero purchase cash when an unmet reserve makes the spending allowance negative. Positive purchase commitments still fail the unchanged cash bound. The native fee comes out of the sell's own proceeds. Six new regressions cover fully invested close/partial/stop, hold, and retained purchase/mixed-action refusal.

## Conditions, evidence and expiry

Version 1 supports one attempt in the declared next-session 09:30–09:35 opening window, with ordinary long ETFs. It does not manage live orders or persistent intraday brackets.

- Stops and take-profit actions trigger only from an actual supplied opening observation known at the window start. The quote must match the correct window and source. Later window highs/lows never create an earlier trigger.
- Complete observed raw window ranges are used retrospectively for conservative fill eligibility. A buy requires the whole range at or below its limit; a sell requires the whole range at or above its limit. VWAP must lie within the supplied range. A triggered stop that gaps beyond its limit produces no fill and retains its unfilled quantity.
- The window must bind to the intended symbol, session, source and execution time and end no later than the original expiry. Windows are treated as intervals ending at the declared boundary; later receipt publication is retrospective evidence, not authority to submit a late order.
- Missing, incomplete, wrong-source, wrong-clock, unknown-range, expired and zero-volume evidence blocks the affected attempt. Unproved required valuation leaves the old dated ledger and unknown accounting, rather than constructing a new NAV from a nominal VWAP.
- Corporate-action coverage requires an explicit named source, date bounds, actual supplied action rows and their digest. Missing coverage and **all nonempty corporate-action sets, including dividends and splits**, are refused in this adapter version. No silent quantity-unit or cash transformation is made. Such a packet is a source declaration to audit, not proof that a vendor omitted nothing.

Actual historical OHLC/VWAP alone cannot establish limit queue priority or a later intrawindow trigger. These are conservative research fill conditions with explicit data requirements, not proof that a broker would have filled an order.

## Single attempts, pending commitments and restart limits

Receipts preserve original commitments and separately record original adapter quantity, cumulative simulated fill, remaining quantity, unfilled purchase reservation and unchanged expiry. Expiry does not automatically release uncertain original commitments. Hold remains legal.

Supplying a previous receipt refuses a second attempt, including after a miss or blocked condition. The managed study creates exclusive, fsynced, per-case attempt reservations before execution and immutable result/seal pairs afterward, under the existing singleton writer/run identity. It checks actual UTC and pinned source before and after reservations and outputs. This demonstrates receipt-aware refusal and controlled research ownership.

The pure adapter does not provide global production idempotency if a caller omits or rolls back its receipt. Durable account/journal ownership remains a required caller responsibility; production uncertain-outcome recovery is not replaced by this module. There is no broker client, network/model call, registered paper execution path or legacy executor-plan export.

## Frozen native parity results

The eight fixed scenarios use isolated **synthetic** $10,000 accounts, 10 QQQ shares at a $100 decision mark and $9,000 cash. Their raw opening bars, ranges, opening quotes and empty action-coverage declarations are explicit fixtures, not observed Alpaca liquidity. Every case was repeated at 2/6/10 bps. No synthetic inputs were substituted into a strategy-return backtest.

An independently constructed native control used predeclared eligibility and native instructions, then compared full fills, misses, ledger and accounting attribution. The driver did not use the adapter's own filter output to select its controls.

| Scenario | Result at each cost |
|---|---|
| Exact one-share resize | One share filled at the supplied $100.50 window VWAP; no weight conversion or rebalance suppression |
| Untriggered stop | No fill when opening quote is $95 versus a $90 stop, even though a later window low crosses $90 |
| Triggered stop | Ten shares sold when the opening quote reaches $90 and the complete range supports the original $89 limit |
| Stop gap beyond limit | No fill at the supplied $85 gap; all ten shares remain unfilled |
| Observed-volume cap | Request 20 shares; 1% of 550 is 5.5; native whole-share execution fills five and retains fifteen |
| Hold with prior partial intent | No new fill; original 7 shares / 3 cumulative fills / 4 remaining / $400 pending cash remain exact |
| Expired window | No fill; original one-share request and expiry remain, old ledger retained and accounting unknown |
| Take profit | Ten shares sold when the opening quote reaches $110 and the complete range supports the original $109 limit |

All **24 cases** passed exact native fill, missed-fill, ledger and attribution comparisons, context immutability, quantity conservation, original expiry, original pending-intent preservation and supplied-prior-receipt refusal.

| Modeled cost per side | Cases passed | One-share $100.50 buy fee | Five-share capped buy fee |
|---|---:|---:|---:|
| 2 bps | 8 / 8 | $0.020100 | $0.100000 |
| 6 bps | 8 / 8 | $0.060300 | $0.300000 |
| 10 bps | 8 / 8 | $0.100500 | $0.500000 |

These are modeled native costs. Synthetic valuation changes—for example marking ten retained shares at a $100.50 window VWAP—are bookkeeping fixtures, not a measured strategy gain, paper-account return or evidence of agent skill.

## Review, tests and source continuity

Independent source review preceded the managed measurement. Review/testing found and repaired window-source binding, full fractional exit rounding, unknown extra evidence, zero-volume valuation and the inherited reduction-cash guard. Prototype failure summaries/tool history remain distinct from final evidence; no full old-source archive or trace is claimed where none was saved.

- Root final affected suite: **169 passed in 1.31 seconds**, covering the adapter, cash/pending/action gates, execution plans and native execution/accounting.
- Independent combined source-review suite: **112 passed in 0.88 seconds**; independent owned adapter suite: **56 passed in 0.55 seconds**. These are repeated cases, not additional unique coverage.
- Scoped mypy: no issues in the new adapter and modified inert action validator. Scoped Ruff/format checks passed.
- Final dashboard UI build: **1,592 modules in 5.65 seconds**. Full API/launcher regressions: **165 passed in 33.04 seconds**, with the existing Starlette/httpx deprecation warning. Earlier full-package results remain separately dated and are not relabeled as a new full AMAT run.
- Managed synthetic measurement completed at 02:51:24 UTC in **19.31 seconds**, with three commands, 24 exclusive case reservations and no surviving processes.
- The native source set now has 314 files: 312 pre-existing files are unchanged, one inert action validator has the reviewed cash fix, and the adapter is new. Core allocator, native fill/accounting engine, controller and current v14.3.0 profile were not edited. The module and receipts carry a distinct research policy identity.

The scientific protocol is `df73806b03d329dde958849ee6a78c549fd54a988a574ea97b749b6215d0fccf`; argv manifest is `28ca5af87c45552e09b7a62dbad01e24e23dbee3d0d32c098936022b41b0b6fe`. See `validations/native-agent-adapter-0240/`, `cycles/manual-native-adapter-0240/` and `research-review/20261024-0240-native-adapter/` for source archives, tests, declarations, original plans, native controls and independent reviews. A separate, prospectively reviewed current-default replay is recorded below; these synthetic cases do not imply that result.

## Fresh current-default continuity replay

Three additional full native v14.3.0 controls completed at 02:58:53 UTC in **23.69 seconds** under the new 314-file native source set. At each of 2/6/10 bps, all **2,457 entire session rows**, their session digest and the metrics exactly match `studies/qqq-foundation-1732/baseline-<cost>bps.json`. The original profile and economic engine were unchanged. This replay uses full 2016 signal history, flat $100,000 from January 3, 2017 through October 12, 2026, the verified SIP snapshot, observed opening VWAP, native whole shares and 1% entry cap, explicit actions and zero cash yield.

The registered control protocol is `f12c7acb0b3163192b439e5ad54bb3cd962cce241a66f2326fcf6153640e37c6`, manifest `c6670d27aa1f39f7231adf59b0a9913bf1bbcee817f1e25dffc40d5615159042`, and receipt is `cycles/manual-adapter-incumbent-control-0240/receipt.json`. This verifies ordinary-strategy continuity; it does not turn the new explicit action policy into a strategy or validate its market performance.

## Dashboard and remaining gates

The dashboard shows the new research adapter separately from the earlier failed naive weight projection. It displays all three cost checks and modeled fixture fees, while retaining the 42 historical comparison rows, earlier rejections and actual default. Real paper costs and performance retain their separate monitoring view.

This implementation does not verify legacy executable profile/planner export, live or paper fills, bid/ask queue position, market-data vendor completeness, calendar/halts, all corporate actions, OCO brackets, direct shorts, inverse instruments, intraday decisions or genuinely prospective model performance. Those capabilities remain blocked. The prior free-model request remains unavailable; no new model request was made.

No new alpha experiment or strategy-return claim is made. Campaign alpha counts remain five completed experiments, ten rejected candidate arms and zero promotions; v14.3.0 remains the default. Native mechanical parity is a prerequisite, not a promotion verdict. All numerical, risk, robustness, causality, accounting, independent evidence, prospective and strategy-level native gates still apply before any default or paper rollout.
