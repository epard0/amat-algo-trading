# Portfolio, selection and model implementation workstream

Prepared October 8, 2026 by source inspection and retained local evidence. This is an implementation plan; no strategy code, broker state or deployment settings were changed. Paths below are relative to `/home/supasuge/Documents/amat-workspace` unless explicitly absolute. Test results quoted in existing verification documents are prior receipts, not tests rerun for this memo.

## Verified current architecture and corrections

| Capability | Current implementation | Required work |
|---|---|---|
| Allocation | `amat/src/amat/portfolio/__init__.py`: `decide`, `target_weights`, `apply_constraints`; Ledoit–Wolf covariance, volatility targeting, nonnegative symbol/cluster/gross caps, no releveraging | Covariance-aware contribution and scenario constraints, diagnostic reporting |
| Quality-based sizing | `portfolio/risk_budget.py`: causal `signal_payoffs`, `trailing_quality`, bounded `budget_multipliers` | Reuse as an existing comparison; this is a trailing payoff sizing mechanism, not risk-contribution enforcement |
| Membership | `portfolio/cross_section.py`: `select_cross_section`, `select_on_schedule`; fixed daily/five-session refresh with daily eligibility exits | Stateful, executable cost-aware replacement policy |
| Research selector | `autoresearch/selection.py:select_assets`; retention bonus, volume gates, cluster counts | Reuse as a control; retained selection is previous intent, not actual broker holdings |
| Risk settings | `config/models.py:ConstraintConfig.risk_contribution_limit` raises in `apply_constraints`; `RiskOverlayConfig` explicitly describes v1 as inert | Label inactive settings; new versioned active policy, without silently reactivating rejected v2 |
| Live decision | `planning/__init__.py:build_plan` and backtest `runner.py:_schedule_decision` call `portfolio.decide` | Retain shared signal/allocation implementation and introduce explicit context/state for new stateful decisions |
| Terminal display | `simulation/__init__.py:SimulatedLiveAccount._terminal_targets` calls stateless `decide`; `deskreview/passes.py` also calls it | Update when new engine enabled or explicitly mark unsupported; avoid showing stateless targets as current executable advice |
| Entry quality | `autoresearch/entry_model.py:_Fitter.fit` uses **HistGradientBoostingRegressor**, four return/adverse/deferral/quantile heads, pooled asset events with equal aggregate date weighting | Review incorrectly called this ridge. Ridge exists in separate `SignalSpec.learner`. Partial pooling must target actual sparse calibration/policy evidence, not duplicate existing pooled training |
| Sleeve support | `backtest/sleeves.py` provides separate single-asset diagnostic accounts; `StanceBlend`, `backtest/stances.py`, `autoresearch/routing.py` offer partial blend/router support | Independent sleeve account returns must never be averaged into claimed portfolio returns; implement one executable combined account |
| Pair research | `amat/stat_arb.py:analyze_pair`, `scan_pairs` measure structure only | No trade-producing pairs sleeve exists; shorting/borrow changes require a separate contract |

The shared `decide` API currently receives only strategy, prices, universe and a decision index/cache. Its purity is why `build_plan` can calculate the last decision without a synthetic replay. New selection or drawdown logic must not silently introduce hidden state or reconstruct a real account from fictitious historical fills.

## P13 work already completed and integration boundary

The isolated `/home/supasuge/Documents/amat-p13-workspace/amat` contains `backtest/runner.py:ScheduledDecisionContext`, `_prepare_schedule`, continuous exit provenance, and `autoresearch/genetic/continuous_study.py`, `continuous_report.py`, sensitivity and synthetic modules. These are absent from the main runtime. Scheduled activation freezes inputs, enforces selection cutoff before activation, and prohibits changes to fixed accounting/execution configuration.

Read `docs/research/genetic-patterns/p13-sensitivity-verification.md`: **76 historical paths and adoption-delay/component-removal controls are completed**, with baseline highest return at all four costs. Do not list those controls as unimplemented. The 544-run synthetic replay and coherent generator also exist (`p13-synthetic-replay-verification.md`); selected recipes outperform baseline in some synthetic worlds but historical results remain adverse, and synthetic families/seeds are dependent, constructed stress cases.

The October 7 isolated `outputs/genetic-patterns/2026-10-07-p13-event-timing/p13-event-timing.md` registers 416 event-timing accounts. The inspected bundle contains design, archived source and tests, with no completed study results. Treat it as prepared implementation/design, not measured completion. Existing October 7 intraday integration design and October 8 minute-refit contract are adjacent proposals, not necessary expansions of the alpha-review work.

**Integration milestone:** extract the scoped registered P13 runtime changes into an isolated integration checkout; compare base revisions and current main changes; preserve old source archives and result identities. Re-run frozen default-runner parity and scheduled transition, split/dividend, exit-basis/embargo, pending-order, resumed-result integrity tests before building new policy state. Do not copy the entire isolated checkout or relabel its old receipts with a new source digest. Preserve suspended P7 lineage. Add a migration record mapping retained runtime registration to new integrated source, then create new experiment registrations for changed behavior.

## Shared decision and state interfaces

Proposed names below are new interfaces, not present APIs:

1. Add `amat/portfolio/context.py` with immutable `DecisionContext`: completed session/availability cutoff, reconciled holdings/cash/equity, pending order reservations, account snapshot hash, accepted model/policy identity, cost estimates with availability timestamps, and previous `PolicyState`.
2. Add `PolicyState`: last decision ID, selected membership/holding age, cooldown/deferral state, turnover used, accepted sleeve weights/model IDs, and any explicitly enabled realized-equity high-water mark. Separate account state from policy state and record transition reason. Use session IDs, not offsets into a mutable panel.
3. A new versioned `decide_with_context(...) -> DecisionResult` composes the same feature/allocator functions; returns target intent, reasons/forced reductions, diagnostics and proposed next policy state. Keep legacy `decide` and default outputs unchanged. Feature caches remain market-derived; account-dependent decisions cannot be precomputed from prices alone.
4. Backtest `_schedule_decision` and `planning.build_plan` must invoke the same decision transition. Supply actual settled and pending state; do not substitute previous selected mask for held positions. Research `DecisionOverlay` adapters may propose bounded effects but cannot become a separate decision engine.
5. Persist immutable state transitions with decision/plan identity and account-event watermark. An idempotent replay of the same decision returns the same proposal; reconcile fills/cancels before next state transition. On missing/incompatible state, reject discretionary additions and require explicit bootstrap from reconciled account state; allowed risk reductions follow separately declared rules. Do not infer successful fills from plan creation.
6. Distinguish target intent, funding authorization, submitted quantities and fills. The executor consumes a versioned serialized plan/authorization envelope and records account events; it must not import AMAT to independently rerank or rescale. Forced eligibility/risk exits bypass discretionary switch hurdles and turnover retention, but remain subject to market availability, sellable holdings and declared execution/liquidity handling. Rebalance deadbands must not suppress explicitly forced reductions.

The funding workstream owns plan envelope semantics and feasible order authorization. Portfolio logic owns expected allocations and mandatory reasons; execution shortfalls feed future decisions through reconciled state, not implicit target success.

## Risk-contribution and stress constraints

Add a dedicated pure `amat/portfolio/risk_constraints.py` and versioned active config in `config/models.py`; pass covariance from `target_weights` rather than estimating it inside `apply_constraints`. Preserve existing cap order and legacy no-policy branch exactly. New policy applies after nominal caps, then recomputes all diagnostics and verifies constraints jointly.

For annualized covariance `C`, variance `V = wᵀCw`, Euler variance contribution is `v_i = w_i(Cw)_i`; Euler volatility contribution is `v_i/sqrt(V)`. For positive `V`, signed fraction `r_i = v_i/V` sums to one. Correlations can make contributions negative even with long-only weights. Report signed contributions; explicitly declare enforcement semantics, e.g. conservative positive share `max(v_i,0) / Σ_j max(v_j,0)`, whose denominator differs from total variance. Apply cluster limits consistently using declared cluster aggregation. Do not mix these definitions under one unnamed limit.

**Normalized fractions are unchanged by uniform portfolio scaling.** Reducing all positions cannot cure a contribution-share breach. A cap below `1/n` for `n` positive contributors may admit no nonzero portfolio; an all-one-asset signal is a concrete infeasibility case. A deterministic bounded shape-adjustment solver should only reduce baseline-proposed positions (`0 ≤ w ≤ w_nominal`) and verify residual violations numerically. Record solver status, tolerance, iterations and reasons. If no verified nonzero solution exists, return an explicitly labeled cash target under the registered fallback; never silently violate limits, lever up or introduce unsignaled assets. Cash has zero absolute risk and undefined contribution percentages, which reports must label as such.

Absolute scenario-loss budgets are separate. With predefined adverse return vector `s_k`, enforce `max(0, -wᵀs_k) ≤ B_k` in equity-return units; group-only scenarios capture cluster concentration. Uniform scale-down can satisfy these absolute budgets. Scenario values must be predeclared or estimated on past-only data; no calibration specifically to the observed January 30 loss. Test covariance and scenario risk jointly after every adjustment; reducing a hedge can increase some other risk measure.

Report target, settled and pending worst-case exposures separately. Post-close risk estimation cannot undo a gap. A settled violation should produce an auditable earliest-eligible reduction intent, not a claim that a limit was continuously enforced. Full pending fills and failed pending sells need adverse exposure cases instead of naive signed netting.

`RiskOverlayConfig` remains historical inert configuration for v1. Emit a capability report marking its fields inactive; reject an explicit request to enable an unsupported active guard in a new engine. Avoid modifying legacy config serialization/digests just to remove inert defaults. Any newly enabled daily-loss or drawdown stop needs realized account high-water mark, session return basis, restart/bootstrap, exit timing and re-entry contract. Kelly activation is not part of the recommended first phase.

Tests: extend `tests/unit/test_portfolio.py` and `test_execution_plan.py`; add analytic diagonal/correlated/negative-contribution cases, cash/zero-variance, infeasible caps, solver nonconvergence, stale/nonfinite covariance, overlapping group constraints, no releveraging and no unsignaled additions. Add replay/plan parity, overnight gaps, pending sale failures, deadband-bypassing forced exits and restart equivalence. Evaluate against nominal caps and matched lower-risk/static controls, with tail loss, geometric growth and rebound participation specified before selection.

## Cost-aware selection with meaningful units

Add a `SelectionPolicy` version alongside `CrossSectionConfig`, rather than changing the numerical meaning of existing scores or `retention_bonus`. The core ranking score is dimensionless and research scores are preferences in `[0,1]`; neither is an expected return. Comparing raw score differences directly with bps costs is invalid.

Initial low-complexity policy can use rank-persistence/retention plus a predeclared turnover budget as a heuristic, labeled accordingly. A true economic switch policy requires training-only calibration from signals to expected holding-horizon return and uncertainty (or direct incremental utility labels). Proposed switch utility in portfolio-return units:

`ΔU = expected_return(w_new − w_keep, H) − incremental_execution_cost − uncertainty_penalty − transition_risk_penalty`.

Both portfolios must be sized with the same constrained allocator; use actual current/pending holdings and include every required sell/buy and expected exit-cost difference. Cost estimates use decision-time information; completed next-window VWAP/volume cannot enter sizing. Return forecasts, covariance and penalties use consistent horizon and units. Replace only when a conservative lower utility bound exceeds a predeclared hurdle. Reject discretionary replacement when estimates are missing, with explicit reason and unchanged valid holdings; mandatory eligibility/risk reductions remain available.

Start with deterministic one-at-a-time substitutions and capped candidate count; recompute joint costs/risk after each accepted change. Do not sum independent pair gains when cluster/cash constraints interact. Log rank, keep/replacement candidate targets, expected benefit, costs, uncertainty, reason and state transition. Freeze calibration and cost model identities per experiment.

Tests: extend `test_selection_schedule.py`, `test_cross_section_and_scan.py`, `test_adaptive_selection.py`; causal prefix/future mutation, actual-versus-target holdings, pending orders, retries, permutation/tie determinism, zero-cost reduction to declared control, high costs retaining incumbents, poor estimate fallback, mandatory exits, restart identity and dimensional conversion. Controls: daily, weekly, existing retention bonus, fixed turnover budget, static risk/exposure matched and random-K turnover matched. Measure traded notional and forgone gains, not only trade count.

## Entry-quality improvement

Retain mature label construction in `autoresearch/entry_labels.py:entry_outcomes` and head-specific maturity in `_Fitter.fit` (horizon+1, five and two sessions plus embargo). Current quantile disagreement is explicitly a penalty, not a calibrated confidence interval. `policy_evidence` requires three validation folds, regime-specific event/date sufficiency and positive fold evidence; this is where sparse regimes often force baseline fallback.

Implement a new spec/model version for hierarchical calibration: global return/timing estimate plus strongly regularized asset/regime adjustments; shrink sparse groups toward global estimates. Assess hierarchical evidence or posterior calibration on past-only validation dates while preserving minimum distinct-date support and dependence-aware uncertainty. New asset/group fallback must be defined. Compare current pooled HGB, neutral baseline, simple linear pooled model and partial pooling; avoid claiming model-capacity replacement is intrinsically better.

Align labels to incremental net utility under the actual holding/deferral policy. Include missed favorable trends and differential execution costs. Existing `net_return` uses `autoresearch/labels.py:forward_returns`, which deducts a default 2 bps per side; `entry_outcomes` does not pass a varying cost model. Version labels/cache keys when adopting conditional costs or other cost levels; do not silently train on fixed 2 bps labels while claiming another execution policy. The deferral-advantage label assumes equal proportional costs and common eventual exit, so differential costs need explicit new semantics. Maintain bounded sizing/deferral and recent-health rollback in `apply_entry_policy`, not permanent hard rejection.

Update `forecast_key` to include new learner/calibration/hierarchy identities and training-affecting settings; forecast cache currently keys only existing training/horizon fields. Keep policy-only ablations sharing forecasts only when training inputs truly match. Add counts, group shrinkage and calibration errors to diagnostics.

Tests: extend `tests/unit/test_entry_quality.py` for missing/sparse/unseen groups, maturity by head, train/validation embargo, cross-date weighting, calibrated coverage under controlled data, prefix invariance, cache collision prevention, missed-winner penalty, bounded deferral and baseline fallback. Run out-of-time and held-out-asset tests as research validation. Reuse `test_delayed_entry_permissions.py` to ensure entry reductions cannot suppress sales or double-count pending orders.

## Complementary sleeves after foundational parity

Implement a small new `amat/portfolio/sleeves.py` interface yielding causal sleeve target vectors and provenance, then combine inside one account before shared risk/funding constraints. Begin with fixed/equal weights and baseline-only identity; only then add shrinkage-based, slowly changing sleeve weights from past realized marginal utility. Preserve a bounded baseline allocation/fallback. Joint tail correlation and concentration constrain the combined portfolio. Net conflicting intents before authorizing orders; do not book a fictitious buy/sell round trip between internal sleeves.

Define attribution separately from the real cash ledger: sleeve target contributions and cost attribution reconcile to the one executable account, with a declared rule for netting savings. Whole-portfolio risk exits have priority over sleeve entry intent. P13 schedule/context work supports causal activation but does not already implement multi-sleeve capital allocation. Independent diagnostic accounts in `backtest/sleeves.py` remain diagnostics.

Controls are baseline, equal/fixed mix, hard switching and component removals; require positive marginal net contribution and held-out stability. Medium-term trend/relative-strength candidates overlap and need residual exposure analysis. Mean reversion/new information must independently earn admission. Pair shorting, futures, borrow and leverage are out of this initial long-or-cash implementation scope.

## Delivery order and verification gates

1. Correct capability/entry-model descriptions; freeze old regression receipts and new metric/evidence identities with the metrics workstream.
2. Integrate registered P13 runtime with unchanged default-path parity. Reuse completed sensitivity/synthetic infrastructure; await verified event-timing outputs before interpreting them.
3. Implement shared explicit account/policy context, persisted transitions and serialized authorization interface with the execution workstream. Legacy engine remains unchanged.
4. Implement target contribution/scenario limits, settled-risk reporting and forced-reduction reasons; validate numerically and on ledger replay.
5. Implement simple cost-aware retention/turnover policy, then calibrated economic switching only after valid return-unit estimates and cost data exist.
6. Improve hierarchical entry-policy calibration as a separate experiment; integrate one complementary sleeve only after allocation/funding/metrics parity.

Every stage must satisfy engineering invariants before economic experiments. Economic improvements require separate causal chronological studies under new source/data/config identities; historical suite success or synthetic stress wins alone do not authorize promotion. Existing approved model and historical receipts remain identifiable throughout.
