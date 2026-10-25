# Metrics, objectives, and evidence implementation plan

Read-only source analysis on October 8, 2026. This memo implements no strategy changes and claims no return improvement. It translates priorities 1 and 7 of the September 30 alpha review into bounded engineering work. Paths below are relative to the workspace root. Source inspection, rather than the September review alone, establishes current functionality.

## Findings that change the implementation scope

1. `amat/src/amat/metrics/__init__.py:96`, `summarize`, still computes `sharpe = annualized_return / annualized_volatility`; `annualized_return` is compounded using 252 sessions. `autoresearch/diagnostics.py:202`, `sharpe`, uses the same geometric convention in uncertainty comparisons. Neither accepts an aligned risk-free return series. The core docstring's reference to excess return over the cash assumption is not a subtraction performed by the implementation.
2. `autoresearch/objective.py` already versions the legacy annual-fold score and exposure-matched incremental score. `apply_incremental_score` preserves v5 semantics, requires valid matched evidence, retains risk gates, and makes the fixed baseline score zero. Do not replace its meaning in place.
3. Newer `amat/scripts/nested_strategy_campaign.py:232`, `score`, already ranks annual paired log-growth improvement less variation across four time blocks, with drawdown and daily expected-shortfall constraints. Its `metrics` function includes anchored returns, calendar-time CAGR, 252-session log growth/volatility, traded-notional turnover, accounting reconciliation, regime breakdowns and fill/miss reasons. Its October 8 protocol freezes families, slots, seeds, source/data hashes, costs, cuts and falsification worlds. This is a distinct registered objective, not missing functionality and not interchangeable with the standard incremental score. Test existence or script existence is not proof of a completed campaign result.
4. `autoresearch/validation_contract.py`, `StatisticalProtocol`, already freezes finite-family testing, includes unsuccessful trials in declared budgets, uses a Bonferroni allocation, and explicitly describes its block bootstrap as diagnostic. It already reports insufficient bootstrap resolution. `genetic/nested.py`, `run_validation` and `_aggregate`, already perform whole-search null comparisons covering their declared family. They do not cover all project researcher choices.
5. `autoresearch/memory.py` already imports hash-verified, compatible development evidence across sessions. `_compatible` checks contracts, evaluator/runtime and snapshot hashes; `_records` excludes checkpoint-exposed sessions. Retrieval is capped and intentionally excludes incompatible evaluators. A memory cache is not a complete project trial registry.
6. `autoresearch/controller.py` and `controller_context.py` already support a bounded research controller. `read_research_ledger` uses read-only SQLite and `summaries.evidence`; it must not instantiate the recovering/writing `Ledger`. `extensions.py` already restricts research to small retained-strategy extensions. Structural evaluator changes require a new explicit program and must not weaken these restrictions.
7. Source search found no research evidence supersession resolver, global trial registry, deflated Sharpe implementation, or factor attribution implementation. `engineering.stage` still explicitly records `awaiting_isolated_validation` and lists unperformed execution/testing/statistical checks. Treat staged patches as proposals, not evaluated mechanisms.

## Work package M1: a versioned metric contract alongside the frozen legacy contract

Add a new module such as `amat/src/amat/metrics/contracts.py` for a frozen `MetricContract` and `series.py`/`evaluation.py` for an aligned evaluation input and v2 computation. These names are proposed, not existing APIs. Keep `summarize` and `PerformanceSummary` byte-compatible in existing frozen programs. A newly registered program explicitly chooses the new contract; archived objectives and score fields remain immutable.

Suggested shared input interface:

```text
EvaluationSeries:
  contract_hash, run_artifact_hash, calendar_hash, valuation_anchor
  session_dates[], net_returns[], risk_free_returns[]
  benchmark_returns[] (optional, requires benchmark manifest)
  factor_returns[][] (optional, requires factor manifest)
  costs/turnover/exposure series with declared denominators
```

The contract must declare calendar, measured interval, preceding valuation anchor, annualization, sample standard-deviation convention, cash-flow treatment, net/gross status, cost/execution versions, risk-free source and availability lag, benchmark identity, zero-variance policy, finite-value policy and missing-data policy. Risk diagnostics also bind target/settled/pending exposure conventions, signed Euler versus normalized positive contribution definitions, and forced-reduction policy identity. Never inner-join away a difficult or missing session. Use `validation_contract.calendar_check` and `objective.anchored_slice` conventions where appropriate. Risk-free accrual must match elapsed holding intervals, including nontrading days; do not forward-fill an annual rate and treat it as a session return without an accrual rule.

Minimum v2 fields:

- `cagr_session_annualized`, optionally `cagr_calendar_annualized`, and `cagr_over_volatility`, with explicit labels.
- `sharpe_arithmetic_excess = sqrt(A) * mean(r - rf) / std(r - rf, ddof=1)`, where A is the contract's annual session count. Mark undefined zero-variance or insufficient-sample statistics unavailable with reasons, rather than interpreting zero as measured Sharpe.
- `active_return_mean_annualized = A * mean(r - b)`, tracking error, and information ratio based on aligned arithmetic active returns. Report compounded wealth-relative performance separately; subtracting two CAGRs is not the same metric.
- Drawdown depth/duration, expected shortfall with explicit quantile interpolation and sign convention, weak-period statistics, traded-notional turnover and cost attribution. New metric definitions need not reproduce the campaign's empirical-tail definition, but must declare any difference and preserve its old results.
- Raw sample count, intervals, missing coverage and evidence class: development, selection, historical outer test, or prospective observation. Comparisons require compatible contract hashes.

Do not subtract cash yield from portfolio returns merely because cash earned interest. The portfolio return includes earned cash yield; excess-return Sharpe subtracts the independently specified risk-free benchmark once. Actual account cash yield and risk-free benchmark are separate inputs. `Program.ordered` currently requires zero account cash yield and 2 bps primary costs; changing either requires a new contract.

Integration points: `autoresearch/worker.py` (`summarize` call sites and artifact serialization), `objective.py`, `diagnostics.py`, `experiments.py` (`daily_returns`, `exposure_control`, `controls`), `contracts.py:Program`, `workspace.py:initialize/load_program`, `summaries.py:evidence`, and `telemetry.py` exporters. Reporting-only v2 sidecars should retain original artifact hashes and explicitly state that they are remeasurements, not retroactive keep decisions. Adapt the nested campaign via a new protocol revision only; do not patch a running frozen driver.

Acceptance: unchanged v5–v9 fixtures retain old digests and scores; hand-calculated excess Sharpe cases agree; nonzero rf is subtracted exactly once; zero variance, tiny samples, invalid equity, missing/reordered dates and date-anchor edge cases have defined outcomes; nested calendar versus session CAGR is labeled correctly; incompatible score versions never share a ranking. Existing `tests/unit/test_incremental_objective.py` already tests canonical old-program preservation and invalid-match failure; extend it and add focused v2 metric tests.

## Work package M2: factor attribution as a diagnostic before a selection objective

Add a proposed `amat/src/autoresearch/attribution.py` with a frozen `AttributionSpec` and factor-manifest inputs. Integrate its artifact into worker analysis metrics and reporting, not immediately into the keep score.

- Predeclare investable equity, duration, metals/commodity and dollar proxies appropriate to the universe, using total-return data and explicit currency conventions. Establish point-in-time data/source availability and release lag. Do not choose factors after examining the winning strategy.
- Fit intercept/exposures only on a trailing training window; freeze coefficients over the next evaluation block. Compute subsequent residuals using realized subsequent factor returns as ex-post attribution. Those realized factor values are never available to the prior trading decision.
- Record coefficients, standard errors or uncertainty intervals, condition number/rank, observation count, residual volatility and residual return. For correlated proxies use a prespecified regularization or unavailable fallback; no silent factor dropping selected by favorable alpha.
- Pair candidate and baseline windows; evaluate differences with synchronized block resampling. If inference includes coefficient estimation, refit within each resample of the registered pipeline or identify intervals as conditional on fixed estimates. HAC diagnostics may supplement, not erase, model-selection uncertainty.
- Keep frictionless regression diagnostics separate from executable benchmark replays with realistic funding/costs. Alpha intercepts are estimates, not standalone deployment gates.

Acceptance: synthetic known-beta/zero-alpha series recover expected exposures; injected future factor observations cannot change earlier fitted coefficients; collinearity/coverage failures are explicit; costed benchmark accounting reconciles; paired uncertainty keeps dates aligned. Historical factor residual improvement alone does not qualify a policy for promotion.

## Work package M3: append-only project trial accounting

Add a proposed `autoresearch/trial_registry.py` independent of session SQLite ledgers, with a single-writer/transactional append interface. Do not change existing `Ledger.__init__` recovery behavior or open it just to inventory research. Reserve trial IDs before evaluation; finalize immutable outcome events afterward. Freeze a registry cutoff/hash into each new Program and statistical protocol.

Minimum fields: global trial ID, program/family/campaign IDs, source/runtime/data/metric/objective hashes, candidate recipe hash, parent identity, partition/cutoffs, discovery time, mechanism/falsification, declared slot budget, evaluation kind, status, artifact hashes, retry/cache alias, and whether results were exposed to a selector or human. Code-changing experiments require code hashes in identity. Record universes, grammars, objectives and restarts as researcher choices even if individual candidate recipes repeat.

Count separately: attempted hypotheses; distinct candidate evaluations; folds/cost/scenario evaluations; repeated deterministic replays; null replicates; and selection decisions. An alias/cached rerun is not new independent evidence, while a new universe or changed evaluator is not erased as a duplicate. Interrupted or invalid experiments remain auditable. Imported historical records carry known lower-bound counts and unknown provenance where appropriate; never claim recovered completeness from the capped memory index.

Integrate registration into the new experiment dispatch path, `workspace.initialize`, genetic search/campaign entry points, and the nested script's `register/job` lifecycle through a new campaign version. `autoresearch/loop.py:selection_uncertainty` currently obtains its diagnostic trial number by counting local experiments with a parent; new programs must bind this comparison to the reserved global/family identity instead. Record legacy outputs using a read-only inventory importer; do not mutate their sqlite files. The controller consumes only a bounded registry summary filtered for development visibility, not all raw outcomes.

Retain current `paired_uncertainty` and `StatisticalProtocol` behavior for old programs. New programs freeze a family definition and alpha allocation using registry history. Do not reset discovery history by opening a new session. Do not equate nominal evaluations with independent trials. For deflated Sharpe, add a separately labeled diagnostic with declared annualization/sample/skew/kurtosis conventions and sensitivity to the estimated effective trial count; no unsupported claim that the whole adaptive search is statistically corrected. Prefer the existing whole-search null machinery for bounded declared families and rerun selection in every null world.

Resolution gate: with alpha .05 and 1,000 trials, 2,000 bootstrap samples cannot resolve a .00005 threshold; minimum attainable finite-bootstrap p is 1/2,001. `StatisticalProtocol` already reports this. New preflight must either choose a feasible registered simulation budget, reduce the prospective family before observing it, or explicitly withhold inferential eligibility. Do not spend enormous bootstrap budgets automatically or call an interpolated extreme quantile precise.

Acceptance: concurrent reservation cannot duplicate IDs; resumes link to original attempts; cached replay does not inflate independent-evidence counts; failed trials are retained; legacy import is idempotent; multiple sessions cannot silently reset a registered family; null resolution failures are explicit; controller summaries never expose excluded holdout values. Existing tests in `test_genetic_nested.py`, `test_genetic_validation.py`, `test_phase_bcd_contracts.py`, and `test_nested_strategy_campaign.py` cover foundations and need targeted registry integration tests.

## Work package M4: correction/supersession records that preserve lineage and time boundaries

Add a proposed `autoresearch/evidence.py` resolver and a small immutable correction manifest. Seed it from verified original and corrected receipts for `amat/outputs/research-control-fix/report.md`; a narrative alone cannot establish source artifact identity. The corrected September controls may supersede invalid control measurements, without turning their candidates into successful strategies.

Suggested correction fields: original receipt hash, replacement receipt hash, correction scope (e.g. control validity, measurement, report wording), affected gates, reason, correcting runtime/data hashes, corrected-at knowledge time, market-data end, evidence class, and parent correction references. Detect cycles, missing targets and conflicting active corrections. Unresolved conflicts return unavailable or conflicting evidence rather than whichever was loaded last.

Implement `resolve_evidence(receipt, market_cutoff, knowledge_cutoff, allowed_roles)` with two distinct uses:

1. Human audit: show original and corrected measurements, scope and reason, preserving old decisions.
2. Research retrieval: resolve only corrections known by the frozen research cutoff with permissible market periods and roles. A corrected evaluator may make numerical scores incompatible; its lesson can state an engineering defect, but its measured score cannot be silently ranked in the old evaluator's family.

Consumers: `memory._records/freeze_memory/related_experiments`; `controller_context.read_research_ledger`; `summaries.evidence` and failure explanations; telemetry/reports; engineering handoff context. Resolve measurements before generating prose; invalidate or mark stale summaries tied to superseded receipt hashes. Controller learning must not import historical outer-test or holdout conclusions through a correction to a development receipt. Preserve original decisions and append corrected view metadata. Read-only controller snapshots include the resolver/registry cutoff and hashes so their evidence is reproducible.

Acceptance: the known zero-fill control defect surfaces the corrected receipt and its still-failed margin; original artifacts remain unchanged; future corrections cannot change an older frozen context; heldout corrections are excluded from development; hash mismatch, cycles, dangling links and competing replacements fail closed; repeated import is idempotent; ordinary read-only inspection performs zero recovery writes. Extend `test_research_controller.py` and new resolver/memory tests.

## Recommended sequence and division of responsibility

| Step | Dependency | Deliverable and stopping criterion |
|---|---|---|
| M0 | None | Record current hashes and existing contract/score definitions; register a new engineering research program. Do not weaken `extensions.py`. |
| M1 | M0 | v2 metric sidecar contract and deterministic unit fixtures; old frozen score/digest regression passes. |
| M4 | M0 | Verified corrections resolver plus controller/memory consumers, with cutoff and compatibility tests. Can proceed alongside M1. |
| M3 | M0, M1 identity fields | Registry and idempotent legacy inventory; family accounting/visibility/resolution checks pass. |
| M2 | M1, dated factor inputs | Lagged attribution artifacts and synthetic causal tests; remains diagnostic. |
| M5 | M1–M4 | Freeze a bounded new comparison program with matched baseline/control runs and separate absolute-growth/incremental-information tracks. |

M5 must predeclare drawdown, tail, turnover and weak-period constraints for the growth track. It must predeclare the benchmark/factor/risk comparison for the incremental-information track. Do not replace the +0.05 old score threshold with a different metric under the same program version. Historical reruns support reconciliation and development only; historical folds already inspected cannot become an untouched test by renaming them. Prospective observations remain the final evidence gap.

No production strategy, ledger, frozen runtime, broker or credentials were modified or accessed for this memo. Test references are a proposed validation plan; tests and campaign backtests were not run during this read-only planning pass.
