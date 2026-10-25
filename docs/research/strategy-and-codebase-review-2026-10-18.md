# Strategy screening and codebase review — October 18, 2026

## Evidence and interpretation

The retained promoted daily reference is `configs/strategy/amat_v13_return_target.toml`, configuration digest `fd08c1d114a4ee563944a8fd9e6fcc7afa6d47a5de4722f6676f9665227c5792`. It ranks eleven ETFs using 63/126/252-session volatility-normalized trends, admits up to four assets with at least two positive horizons, refreshes relative membership every five sessions, and checks absolute eligibility daily. It is long-only and unlevered. Its configured 20% volatility target and legacy risk fields are not guaranteed loss ceilings.

The most relevant evidence is newer than the original October 13 proposal:

- [Daily fixed-arm comparison](../../amat/outputs/analysis/2026-10-14-strategy-implementation/daily-study/report.md): all 27 registered arm/cost runs completed.
- [Intraday full walk-forward](../../amat/outputs/analysis/2026-10-14-intraday-full-walkforward/report.md): all 92 registered paths completed, failing advancement.
- [YTD validation](../../amat/outputs/analysis/2026-10-13-ytd-leading-strategy-validation/report.md) and [concentration diagnosis](../../amat/outputs/analysis/2026-10-13-strategy-improvement-review/report.md).
- [Separate QQQ opening-range study](../../amat/outputs/prop-candidate-improvements-2026-10-14/REPORT.md): a different strategy/mandate; no joint portfolio evidence or deployment authority.

These are existing retrospective results, not newly rerun performance experiments. All historical periods have prior research exposure. Daily results end October 12; the minute study ends October 13. No October 18 market observations are inferred.

| Daily policy, 2 bps per side | 2026 YTD return | Maximum drawdown magnitude | Fills | Recent return |
|---|---:|---:|---:|---:|
| v13 reference | 11.39% | 15.01% | 136 | -1.61% |
| B1: daily ranking refresh | 17.12% | 13.40% | 258 | -0.88% |
| A1: metals cap 30% | 9.53% | 11.97% | 132 | -1.47% |
| A3: inverse-volatility allocation | 10.82% | 12.48% | 131 | -0.18% |
| C2: 10/21/63-session trends | 12.12% | 12.80% | 302 | -1.67% |

Daily refresh is the strongest YTD challenger in this table, retaining 14.04% return at 10 bps versus 10.65% for the reference. Almost doubling fills is a material tradeoff. Recent returns remain negative. Neither this table nor the already-inspected YTD period justifies automatic replacement. Lower metals exposure improves observed downside at a return cost. Narrowing the rebalance band and the intermediate shorter horizon were unpromising in these runs. Cash-budget feasibility should be judged as an execution invariant even when the reserve reduces historical return.

The intraday GP refitting arm returned -10.00% in the continuous base-cost account and -85.40% in the compounded quarterly walk-forward index, with 0/19 profitable folds. Across M3 comparisons 11,292 fits produced one activation. Risk halts censor the continuous account's activity; a terminal loss near 10% is not evidence of a profitable or robust strategy. More refits are a low-priority next experiment.

## Is strategy-specific asset screening useful?

Yes, as a hypothesis and as an explanation/monitoring tool. An eleven-ETF universe constrains the opportunities the strategy can choose. Searching for stronger eligible trends may help selection, but increasing the number of correlated equity ETFs can simply concentrate the same underlying risk. Strong trend scores describe past conditions, not expected return estimates.

The code already provides `amat scan CONFIG --snapshot SNAPSHOT --all-snapshot-symbols --json`. It uses the strategy's own features and only the prefix through the requested session. This review repaired its divergence from five-session membership rules. Current rank and retained membership can legitimately differ between refreshes. Selected means signal membership before covariance sizing, caps, structure/risk overlays, cash constraints and actual fills; it is not a holding or order instruction.

Freshly executed scans are retained in [review artifacts](../../amat/outputs/analysis/2026-10-18-codebase-review):

| Scan | Actual final session | Assets | Signal-eligible |
|---|---|---:|---:|
| Verified core | 2026-10-12 | 11 | 8 |
| Core columns from wide snapshot | 2026-08-10 | 11 | 8 |
| All columns from the same wide snapshot | 2026-08-10 | 92 | 57 |

The wide snapshot's directory name ends August 20, but its usable final session is August 10. Read timestamps from the data, never the directory name. On that shared August 10 observation, the core scan retained IWM/SPY/UUP/EFA. The wide scan retained IWD/RSP/VLUE/IJR. Its strongest current ranks included IWD/RSP/EWC/QUAL; the difference illustrates retained scheduled membership. This demonstrates additional opportunities and a different selection, not improved realized performance. The wider universe's unmapped instruments are explicitly labeled outside the strategy universe. They need declared clusters and portfolio constraints before a trading strategy can use them.

A useful screening pipeline has three stages:

1. **Point-in-time tradability:** listing/delisting dates, completed-session coverage, sufficient trailing history, adjusted-price/corporate-action integrity, permitted instrument type, broker tradability/fractionability, lagged dollar volume and estimated spread/participation capacity. Start with unlevered ETFs. Individual stocks require earnings/event risk, delisting coverage and survivorship-aware membership.
2. **Strategy fit:** the existing deadband, positive horizon agreement, trend rank and usable volatility. Investigate persistence, reversal/chop and gap sensitivity as separate registered hypotheses. Avoid a growing collection of thresholds fitted on 2026 outcomes.
3. **Portfolio usefulness:** sector/factor overlap, covariance, cluster concentration, stress loss and marginal contribution relative to current holdings. An asset that looks ideal alone may duplicate the existing portfolio.

Keep discovery separate from execution authorization. A daily scanner must not silently expand the broker allowlist or strategy configuration. For historical stock universes, include securities that later disappeared: [QuantConnect's survivorship example](https://github.com/QuantConnect/Documentation/blob/master/03%20Writing%20Algorithms/01%20Key%20Concepts/10%20Research%20Guide/08%20Survivorship%20Bias.html) explains why today's constituents bias historical tests.

## Next experiments, ordered by information value

1. **Validate daily refresh first.** Examine retained 2024/2025 paths, turnover and cost sensitivity, prefix invariance and prospective observations under a frozen policy. Keep the original reference. Select an objective before comparing growth against downside protection.
2. **Evaluate a fixed wider ETF universe.** Freeze membership rules, cluster mapping and data/cost checks before results. Compare core versus wider universe with identical engine, risk target, start state and execution policy. Include random-K controls with comparable turnover and an appropriate diversified benchmark. Report exposure, factor concentration and drawdown as well as net return. A wider universe changes covariance shrinkage even if only four positions are selected.
3. **Separate concentration and weighting experiments.** Test daily refresh plus the previously specified metals cap or inverse-volatility weighting only after isolated comparisons. Count combinations as additional trials. Include a constant-exposure or risk-matched control calibrated using earlier data to distinguish timing skill from lower average risk.
4. **Improve execution evidence.** Capture decision/arrival/fill prices, partial fills, pending commitments, spread, rejected quantity and implementation shortfall. Calibrate on prior/prospective observations. Stress delayed entries and account size; charge costs once.
5. **Treat minute and opening-range research as separate mandates.** Reject the failed GP campaign for advancement. The QQQ structure-stop result is a research lead, with small dollar gains and unresolved short-borrow/OHLC/execution assumptions; it is not evidence for combining live portfolios.

Use chronological folds with fitting and screening inside each fold, mature labels and purged boundaries, shared-index block uncertainty estimates, and a retained trial ledger including failures. New screening thresholds count as research trials. Previously inspected 2026 dates cannot become a fresh holdout by renaming them.

## Implemented engineering changes

- `amat.scanner`: reuse scheduled selection, the same usable-volatility mask, and cached horizon features. Report cadence and refresh session. Keep current eligibility rank distinct from scheduled membership.
- Scanner CLI: explain that admitted signals precede sizing/caps; remove the unsupported claim that no currently eligible signal means the real account is already 100% cash.
- `amat.portfolio.cross_section`: explicitly reject malformed horizon rows and misaligned score/volatility/horizon panels with domain errors rather than accidental indexing/broadcasting failures.
- `amat.execution.budget`: validate price finiteness before Decimal comparisons, so NaN inputs raise a deliberate validation error rather than `decimal.InvalidOperation`.
- `executor.portfolio_monitor`: remove its duplicate fixed-temp-file writer and reuse `safety.atomic_json`, which uses unique temporary files, rejects nonfinite JSON and syncs both file and parent-directory metadata.
- Remove duplicate server archive `amat-server-20261008T084131Z-662933.tar.xz`. Its bytes matched the retained `amat-server-20261008T084011Z-660795.tar.xz`; both SHA-256 values were `8e69bd5f5a2ac22146e5a3ff07e6d85c933414d3bb8387ab2f37bde61895882f`. About 4.1 MiB reclaimed. No references to the removed filename were found in the searched documentation/application directories.

Existing uncommitted work was preserved. No strategy parameters, promotion identities, broker allowlists or running deployment were changed. Historical frozen source copies and research artifacts are reproducibility inputs and should not be treated as dead source merely because the current package does not import them.

## Remaining design work

**Execution parity was the most consequential remaining gap at review time.** The subsequent [October 19 implementation](strategy-engine-update-2026-10-19.md) adds the versioned executor contract and fault-injected tests described below. Exact event-level simulator/broker fill parity remains unproven.

Original review finding: `budget_buys` exists in AMAT, while the standard paper submit path submits plan intents in sequence. The daemon refuses unrelated open orders, but this does not establish confirmed sale proceeds or a staged cash-feasible buy batch. Broker acceptance is not a fill confirmation. [Alpaca documents that open orders consume buying power](https://docs.alpaca.markets/us/docs/orders-at-alpaca). Implement a versioned contract carrying original authorized quantity, cumulative fills, remaining intent, pending cash commitments, expiration and account/policy identity. Refresh account state after sale confirmation and rebudget only the authorized buys. Partial sales, reconnects and restarts must not create new authority or duplicate sales. Test with fault-injected broker states before paper activation.

**Make enforced policy explicit.** Separate legacy strategy risk knobs from the active risk-policy identity in plans/reports. Verify each exposed control affects its intended decision path; unsupported controls should be labeled or rejected, not read as protection. Persist actual-account drawdown and external flows, and report target concentration separately from marked holdings drift.

**Keep the executor boundary small.** Preserve its standard-library runtime and versioned subprocess/plan boundary. Share durable-writing behavior within the executor rather than importing the strategy's dependency graph. Consolidating the writer is a completed example.

**Split the CLI by cohesive concern incrementally.** The large `amat.cli.main` contains market data, scanning, research and reporting dispatch. Move parser/handler pairs into command modules with stable entry points and CLI contract tests when changing those areas. Avoid a wholesale relocation while the workspace contains extensive active changes.

**Make research artifacts a first-class boundary.** Keep protocol/config/data/source hashes and immutable source snapshots together; distinguish current source, frozen runtime and generated evidence in tooling. Add an explicit archive/retention command with a dry-run reference report before removing outputs. Import searches alone cannot establish that an operator script, frozen runtime or historical report is unused.

**Repository hygiene:** the large amount of uncommitted and untracked source makes Git SHA alone inadequate provenance. Preserve source hashes for experiments and separate reviewable commits by engine, executor and research when ready. Do not broadly delete scripts, schema files or frozen outputs without checking CLI/subprocess/document references. The verified duplicate archive was the clear removal candidate in this pass.

## Verification

- Scanner, selection schedule and cash/risk policy suites: 76 passed.
- Portfolio, simulation, allocation and minute-improvement suites: 62 passed.
- Complete executor suite: 331 passed.
- Targeted Ruff checks passed; targeted mypy checks passed for scanner, cross-section selection and executor portfolio monitor. Mypy reported unused configuration-section notes in the narrowed AMAT invocation.
- Three real-snapshot CLI scans completed and their JSON outputs are retained. Scheduled membership regression exercises changing ranks between refreshes; existing future-mutation causality test passes.

An initial test invocation from the workspace root exposed two relative-path test failures; rerunning from the documented AMAT project directory resolved them. Two newly added test fixtures were also corrected to construct a valid enabled selector. Final results above are from the corrected project-directory runs. The entire AMAT/frontend suite and live broker integration were not run. No new alpha claim or broader-universe performance comparison is made by these engineering checks.
