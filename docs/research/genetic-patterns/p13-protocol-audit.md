# P13 continuous-switching protocol audit

This is a research protocol proposal grounded in the retained P11/P12 receipts and current daily runner. It does not change a strategy, campaign, historical receipt, or execution authority. The periods and selected recipes have already been examined: a continuous replay would repair portfolio-state interpretation, not create an untouched holdout.

## What must change from P11/P12

P11/P12 independently replay each recipe from the beginning of the snapshot, then measure its outer period. Their starting positions, capital, entry bases, pending orders, and embargoes can differ. Concatenating those period returns would not describe a continuously executed selection policy. P13 needs one chronological ledger per policy, with scheduled changes to decision inputs and unchanged ownership of the resulting portfolio state.

The existing v4 `SwitchingStrategyConfig` blends two fixed stances using breadth. It is not an annual recipe-schedule implementation: the P11 recipes also change trend horizons, volatility estimates, target volatility, rebalance thresholds, signal contributions, risk scalar, and exit thresholds. A daily score-only splice would omit material recipe changes.

## Concrete schedule and causal availability

The retained fold validation cutoffs are 2023-12-29, 2024-12-31, and 2025-12-31. Each selection uses returns through that closing valuation. It cannot be used for an earlier decision or execution.

Recommended convention: activate at the **close of the first following exchange session**. This leaves an explicit session for calculation after validation has completed and avoids asserting zero-latency selection at the valuation cutoff. With the current one-session execution lag, the schedule is:

| Validation information complete through | First selected decision | Decision close UTC | First possible selected execution |
|---|---|---|---|
| 2023-12-29 | 2024-01-02 | 21:00 UTC | 2024-01-03 |
| 2024-12-31 | 2025-01-02 | 21:00 UTC | 2025-01-03 |
| 2025-12-31 | 2026-01-02 | 21:00 UTC | 2026-01-05 |

These sessions and closes were checked with the installed XNYS calendar. For frozen reproduction, the snapshot ends at the 2026-09-25 close. The primary report must cover the full available calendar, including explicit warm-up rows; returns after the common 2023-12-29 valuation are a secondary selection-period view. The expanded run must first reconcile and freeze the latest complete available endpoint. See [full-history requirements](p13-full-history-validation.md).

A faster alternative could authorize a new decision after the validation close, before the next execution window, giving first executions on January 2. That requires an explicit selection availability and computation-latency contract, plus an after-close decision representation. It must be a separately registered sensitivity case, not an advantageous date chosen after results. Neither alternative claims these recipes actually existed in 2023–2025: the selections are retrospective.

## Frozen recipes and aliases

| Alias | P11 recipe digest | Meaning |
|---|---|---|
| BASE | `b8efc35a55ef9f21363ac8f1a60e7079c7d87230cc79fd9d8e5001c6ea60bb8f` | Original baseline; MTF effect zero, no exits |
| ILLIQ | `fe653970e9cda67341ef76f1f3c963382769d709c1810592649b19d12fae0fc2` | illiquidity:21; effect 0.1772855421; profit-only; risk scalar 0.2925003114 |
| GP | `76d4d7d6bf6cf8104772e1d0885e3f334b4df8a401bce3718f85a2e142db9a0a` | Retained GP expression; effect 0.0521193315; stop and profit; risk scalar 0.8534231772 |
| MOM | `b056b618068691d1ad0d1b7b41712a90a2685b9a814653aeb0b864a822d477a2` | momentum:63; effect 0.1740184426; stop and profit; risk scalar 0.9722939875 |
| VAR | `afd53e00c430ab0905805216ccf10b7256fb1037dc5a7c48eebd990713927445` | variance_ratio:21; effect 0.2074164050; stop and profit; risk scalar 0.9782699768 |

| Track | Before first activation | 2024 activation | 2025 activation | 2026 activation |
|---|---|---|---|---|
| Baseline control | BASE | BASE | BASE | BASE |
| Balanced | BASE | ILLIQ | MOM | VAR |
| Return | BASE | GP | MOM | VAR |
| Defensive | BASE | ILLIQ | BASE | VAR |

Each selected track has two subsequent recipe changes and one initial departure from the baseline. There are five unique recipes including the baseline. Balanced and defensive share their 2024 recipe; balanced and return share their 2025 recipe; all three share the 2026 recipe. Shared recipes do **not** imply shared 2026 holdings: the earlier paths differ. Policy tracks are correlated counterfactuals, not independent replications.

Use the exact JSON recipes and P11 per-fold feature-eligibility masks, not rounded values from this table. The 2024 ILLIQ selection's intraday inputs were training-ineligible, so positive nominal weights do not establish active intraday exposure. The 2025 defensive baseline has inactive method/weight/exit placeholders.

## Common warm start and state ownership

For each declared cost scenario, run BASE from the snapshot's start using identical initial cash, actions, cash rates, execution windows, participation limits, and accounting. The three selected tracks and their baseline control must be identical through settlement on 2024-01-02 before its new close decision. The common reference valuation is 2023-12-29.

The continuous ledger owns cash, positions, settled fills, pending orders, carried missed fills, entry bases, and embargo expiration indices. Recipe changes do not reset these fields, scale equity to a new starting value, or force a liquidation. Continue to settle previously scheduled orders using their original decision provenance. For example, baseline orders made on 2023-12-29 still execute on 2024-01-02 before the new recipe makes a decision.

Recipe-specific causal features and risk estimates may be precomputed from historical market observations, provided future-prefix equivalence holds. They are observations, not independently simulated portfolio states. At activation, switch all relevant decision configuration fields together, including trend, volatility, target risk, rebalance thresholds, fixed MTF coefficients, risk scalar, and exits. Preserve execution, cost, cash, universe, split/dividend, and lag invariants. The current recipes share those execution invariants; verify that identity rather than relying on it implicitly.

## Entry basis and embargo contract

Current `run_backtest` calls `track_entry_bases` only when exits are enabled. A continuous implementation must track the actual adjusted entry basis during the baseline warm-up too, even though the baseline does not use it. Otherwise the first recipe cannot apply its exits consistently to positions inherited from the baseline.

Recommended primary rule:

- Track bases from settled fills throughout the entire common history. Preserve the original flat-to-long adjusted basis through resizing and recipe changes. Clear it only after the position becomes flat, following the existing accounting semantics.
- Apply the newly active recipe's stop/profit thresholds to that actual inherited basis at the first new decision. A threshold already breached at activation can cause a next-window liquidation; it must not cause a same-close or retrospective fill.
- Preserve already-earned embargo expiration dates when switching recipes. Continue to block increases while an inherited embargo is active, including when the new recipe has `exit_mode=none`.
- New breaches can extend an embargo; use the later of an existing expiration and the newly calculated expiration. Changing to a shorter embargo must not silently shorten an already-issued restriction.
- Do not cancel or reinterpret an old pending liquidation merely because the new threshold is wider or exits are disabled.

The current `apply_exit_rules` writes `decision_index + duration` on each breach and the runner skips it entirely when rules are `None`. Both details need explicit handling for the above contract. An empty threshold mapping can preserve the existing final embargo gate, but basis tracking and monotonic expiration still require deliberate implementation. Do not mutate legacy static-recipe behavior inadvertently: keep a separately versioned continuous contract and prove static replay parity where semantics coincide.

Alternative exit ownership, such as retaining the entry-time recipe's rules until each position closes, is a different strategy. Do not compare several ownership conventions and select the best on these same outcomes without registering that as additional research.

## Proposed experiment matrix

Register the schedule and transition rules before running any new paths.

1. **Primary continuous comparison:** baseline plus balanced, return, and defensive tracks, at 2, 5, 10, and 20 one-way bps: 16 paths. At each cost, all tracks share that cost's baseline warm start. Costs remain fixed throughout each path; they are not a flat subtraction from returns.
2. **Prespecified selection-delay sensitivity:** add one and five exchange sessions to every activation in each selected policy schedule, retaining the ordinary one-session execution lag: 24 additional paths (three policies × four costs × two delays). This delays recipe availability, not every subsequent daily signal. Do not select the best delay after observing results.
3. **Optional later-stage component controls:** continuous no-MTF, no-exits, and neither variants of each selected schedule across the same four costs: 36 labeled paths. Preserve all other recipe settings and the common baseline warm start. The recommended intervention changes only the post-activation schedule; pre-activation baseline decisions remain unchanged.

This gives 16 primary paths, 40 including the two delay sensitivities, and 76 if all optional component controls are also included. Do not deduplicate different policy histories merely because their latest recipe is identical. A valid no-op deduplication requires identical entire schedules, data, initial conditions, and state-transition contracts.

Calendar-verified delay sensitivities:

| Selection cycle | Extra delay | New decision date | First possible execution |
|---|---:|---|---|
| 2024 | +1 session | 2024-01-03 | 2024-01-04 |
| 2024 | +5 sessions | 2024-01-09 | 2024-01-10 |
| 2025 | +1 session | 2025-01-03 | 2025-01-06 |
| 2025 | +5 sessions | 2025-01-10 | 2025-01-13 |
| 2026 | +1 session | 2026-01-05 | 2026-01-06 |
| 2026 | +5 sessions | 2026-01-09 | 2026-01-12 |

The 2025 +5-session dates reflect the January 9 exchange closure; use exchange sessions, not business-day arithmetic.

A separately registered future diagnostic could hold the first selected recipe after its legal 2024 activation for the rest of the path. Only two unique first recipes exist, ILLIQ and GP: eight further paths across four costs. Those are legal frozen-first controls; holding MOM or VAR from 2024 would use selections unavailable at that time. These eight paths are not included in the proposed 76-path maximum above.

None of these paths selects a replacement profile. Report the annual boundary ledger audit and continuous cumulative wealth, drawdown, fees, turnover, missed fills, and transition costs, alongside the baseline. Report partial-year 2026 metrics as such.

P12 already showed component effects changing sign across periods. That evidence motivates these diagnostics; it does not authorize choosing the 2025 "neither" recipe as a new winner, switching profiles based on outer returns, or reoptimizing take-profit ratios using the replay's measured period.

## Required data and bindings

Existing P11/P12 inputs are sufficient for a daily-decision continuous replay through 2026-09-25: the verified daily/actions/execution-window snapshot, P10 native 15-minute receipt, P11 unmasked five-timeframe feature bank and fold-specific eligibility, selected recipes, and their immutable validation receipts. No new market download is necessary merely to reproduce that frozen historical portfolio. The expanded full-history study must inventory and reconcile newer receipts and acquire missing inputs before freezing its own endpoint.

Bind artifact, selected-recipe and selection-receipt hashes; P11 context, feature array and sidecar hashes; snapshot manifest and all partitions; native receipt; calendar and activation schedule; current runtime/dependencies; initial cash; cost scenarios; and the transition/exit-ownership contract. Verify each selected recipe is the one chosen by its corresponding P11 validation policy. Record that the underlying adjusted-data vintage and selection history are retrospective.

An extension to genuinely intraday decisions, intrabar stop fills, or modern prospective dates would need additional execution data, timing contracts, and separate validation. Native 15m/hourly features do not by themselves authorize execution at those bar closes.

## Decisive acceptance tests

1. **Static schedule parity:** one constant recipe over the full history reproduces the corresponding existing runner's session valuations, fills, instructions, fees, actions, and returns. BASE-only must match exactly. Repeating the same recipe in a schedule must be a no-op.
2. **Availability and lag:** poisoning a future selection or future market rows leaves every earlier decision, pending instruction, fill, and valuation unchanged. Under the proposed schedule, selected instructions cannot execute before January 3, 2024 / January 3, 2025 / January 5, 2026.
3. **Common-state identity:** every arm has identical cash, positions, pending orders, entry bases, and embargo state immediately before its first permitted decision. No hidden equity rebasing or independently replayed recipe holdings enter that state.
4. **Transition ownership:** an instruction made before a switch still settles after the switch unchanged, including split restatement and missed-window behavior. New recipe orders use the new decision configuration only after activation.
5. **Exit inheritance:** switching from no exits to exits uses the actual old entry basis; disabling exits does not erase a previously active embargo; a shorter new embargo cannot reduce an existing expiration; returning flat clears basis and reentry establishes a new one. Boundary breaches execute only after the ordinary lag.
6. **Full recipe activation:** a controlled fixture changes target volatility and rebalance thresholds independently of signal scores and confirms that both affect only post-switch decisions. A score-only implementation must fail this test.
7. **MTF and missingness:** P11 eligibility masks remain fixed by fold, latest missing bars stay unavailable, and zero/missing/removed views stay neutral without renormalization. A view that becomes observable only after training cannot acquire eligibility at the switch.
8. **Single-ledger reconciliation:** every daily equity change reconciles to market P&L, distributions, cash return, and fees. Report absolute wealth as well as normalized returns; higher-cost paths need not have monotonic measured returns because decisions can change.
9. **Deterministic checkpoint/resume:** interruption immediately before and after a switch reproduces an uninterrupted run exactly, including pending orders, basis, embargo indices, active recipe digest, and accumulated accounting. Changed source/data/schedule invalidates resume.
10. **No stitched proof:** the reported total return must come from the one ledger's consecutive valuations. Any result equal to concatenated P11 independent fold returns is insufficient unless independently reconciled, not a target to match.

Passing these tests establishes replay correctness. It does not establish trading superiority, change the suspended P7 campaign, or grant promotion/execution authority.

## Local evidence inspected

- `outputs/genetic-patterns/2026-09-29-p11/study/summary.json`
- `outputs/genetic-patterns/2026-09-29-p11/study/context.json`
- `outputs/genetic-patterns/2026-09-29-p12/performance-report.md`
- `outputs/genetic-patterns/2026-09-29-p12/results-summary.json`
- `src/amat/backtest/runner.py`
- `src/amat/backtest/exits.py`
- `src/amat/config/switching.py`
- `src/autoresearch/genetic/mtf_replay.py`
- `src/autoresearch/genetic/walkforward_study.py`
