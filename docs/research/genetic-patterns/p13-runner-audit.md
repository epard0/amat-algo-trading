# P13 continuous switching: runner audit (research only)

Scope extension: [full-history validation](p13-full-history-validation.md) additionally requires ledger rows from the first source session, including warm-up. The current warm-up ledger start is not sufficient for that contract.

Date: 2026-09-29. No runtime or test implementation is proposed as completed here. This audit inspected the existing code and recommends an implementation contract; it does not authorize live or shadow execution.

## Recommended extension seam

Extend the existing authoritative `run_backtest` session loop with an **optional immutable decision schedule**. The schedule selects a validated decision context at each completed session. Preserve the existing unscheduled path exactly. Do not concatenate fold equity, call the runner separately for each annual recipe, or create a second accounting loop.

The precise seam is immediately before `_schedule_decision` in [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:962). That helper already accepts strategy, precomputed decision inputs, overlay, exit rules, and shared ledger/pending/exit state [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:404). Select those decision inputs from the active recipe, while keeping settlement, execution model, accountant, corporate actions, records, and state in the one existing loop.

A suggested immutable context contains recipe hash, selection receipt hash, selection-data cutoff, earliest permitted decision session, validated candidate configuration, causal precomputed allocation inputs, MTF-adjusted scores, overlay arrays, and exit policy. A registered schedule assigns contexts to nonoverlapping session ranges. Validate the entire schedule before running, prohibit uncovered decision ranges, and record the active context hash with every decision/transition.

P11 recipes change more than scores: `_candidate` changes trend horizons/deadband/clip, fast/slow volatility estimates and mixture, rebalance threshold, and target volatility [parameter_study.py](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/genetic/parameter_study.py:234). A single concatenated score matrix with one static strategy would therefore evaluate the wrong recipes. Build each context's allocation precomputation from its own strategy, then select its causal row at the active decision. Existing `_precompute_run_inputs` documents prefix equivalence and preserves allocation context when scores are overridden [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:711).

## State that must cross every boundary

The current runner initializes a cash-only ledger once, then keeps pending orders, pending missed instructions, adjusted entry bases, and embargo indices locally [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:895). These are the critical state components. Preserve:

- Exact Decimal cash and marked holdings, including quantities and current mark sessions.
- Pending orders and pending missed instructions, retaining their original signal/execution sessions and intended quantities. They already participate in later sizing, preventing duplicate selling or fictitious replacement buying [execution/__init__.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/execution/__init__.py:72).
- Original adjusted entry bases for held positions and absolute global-session embargo expiry indices.
- The same execution model instance. It owns a deterministic fill counter for missed-fill stress, so replacing it would reset that experiment's state [execution/__init__.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/execution/__init__.py:183).
- Global session indices, corporate-action processing, daily attribution, warnings and trace continuity. Do not reset annual return denominators inside the engine.

There is no ready-made ledger restart/snapshot input on `run_backtest`; its public interface accepts initial cash and initializes a new ledger [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:787). A streaming/checkpoint engine would require a larger explicit state contract. For this bounded research step, one uninterrupted invocation is the smaller change.

## Exit-state trap and recommended explicit policy

Entry bases are currently updated only while `exit_rules is not None` [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:952). If a scheduled recipe first enables exits after a period without them, existing holdings would otherwise lack a valid original basis. On the scheduled path, maintain entry bases continuously from all settled fills, including baseline and no-exit periods. The existing helper clears on flat and retains the first-entry adjusted basis through resizes [exits.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/exits.py:124). Never rebase carried holdings to the switch-day close; doing that invents a new entry.

Predeclare whether new thresholds govern existing positions immediately or whether old rules remain attached to each holding. The smaller recommended policy is **new recipe thresholds apply to every held position from its first eligible decision, using preserved original bases**, with ordinary next-window execution. This may legitimately generate an exit immediately after a switch.

Embargo behavior must also be explicit. Recommended conservative contract: already incurred embargoes persist until their original absolute expiry, even when the new recipe has exits disabled; new breaches use the current recipe's embargo duration. This requires applying the existing embargo mask independently of whether new exit rules are enabled. Currently the mask is inside `apply_exit_rules` [exits.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/exits.py:192). An empty per-symbol rule map supports masking existing embargoes without creating new breaches, but that convention should be explicit in the new schedule contract. Any alternative reset/waiver policy is a separate registered sensitivity, not an undocumented convenience.

## Timing and execution invariants

The loop processes splits on pending orders, settles earlier instructions, records entry bases, and only then forms the current close's decision [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:918). Retain that sequence at switch boundaries. Do not cancel old pending orders just because the recipe changes; cancellation requires a separate explicit experiment. Keep the previous recipe until the new selection is available and its declared latency has elapsed.

For P11 historical selections, the data cutoff is each fold's `validation_end`. A conservative research default is the first observed session **after** that cutoff as the new recipe's first decision session; fills then occur at that session plus the existing execution lag. Switching at the cutoff close itself requires an explicit assumption that selection finishes after that close and before the next execution window. Freeze the assumption and sensitivity before comparing results. Calendar year labels must not override actual observed-session boundaries.

Initialize the continuous account with a predeclared baseline before the first selection. Do not simulate the 2024-selected recipe throughout pre-2024 history to manufacture its starting holdings. Subsequent annual recipes inherit the actual preceding portfolio. This necessarily differs from P11/P12's separately warmed counterfactuals, so their outer-return equality is not an appropriate acceptance condition for switching runs.

## Fixed and variable configuration

Initially allow only the P11 recipe fields already mapped by `_candidate`, MTF contributions, risk scalar, and exit rules to change. Require identical universe/order, execution lag/window/pricing, participation cap, cash model, annualization convention, corporate-action inputs, fees within each cost scenario, and warmup contract across contexts. The runner constructs its execution model and constant cash rate once [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:879). Silently swapping an arbitrary full configuration would leave settlement using stale semantics.

New target volatility and rebalance thresholds apply at the next decision. Current holdings need not instantaneously equal new target constraints: orders settle later, thresholds can suppress trades, and liquidity/funding can cause misses [execution/__init__.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/execution/__init__.py:113). Report target and realized exposures separately. Do not directly overwrite holdings to enforce a target. Keep corporate actions, mark-to-market and cost reconciliation under the existing accountant [accounting.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/accounting.py:96).

## Existing nearby facilities do not implement annual recipe switching

- `StanceBlend` is a convex blend of two allocator targets, with diagnostics from the trend stance, not a schedule of arbitrary portfolio/exit configurations [runner.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/runner.py:83). `assemble_stance` constructs that blend [stances.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/stances.py:36).
- Existing `walk_forward` deliberately slices one fixed-strategy full-history run. Its shared-equity approach is useful for reporting, but it does not change recipes [walkforward.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/validation/walkforward.py:228).
- The managed v2 engine was removed; the sleeve module explicitly identifies the single remaining runner [sleeves.py](/home/supasuge/Documents/amat-workspace/amat/src/amat/backtest/sleeves.py:62). A stale protocol annotation mentioning managed results is not an available implementation.
- `replay_prepared` currently invokes `run_backtest` once for one candidate and subsequently applies measurement slicing [mtf_replay.py](/home/supasuge/Documents/amat-workspace/amat/src/autoresearch/genetic/mtf_replay.py:182). A new scheduled adapter should prepare contexts and invoke the extended runner once.

## Acceptance evidence for a later implementation

Before historical comparisons: exact single-context parity with the existing runner; identical-recipe boundary no-op parity; future schedule/data mutation invariance before activation; preserved holdings and pending orders under one- and multi-session lag; split/dividend at a transition; exit-enable with an older held position; embargo spanning no-exit and later exit-enabled contexts; insufficient-cash and participation-capped fills; missed-fill counter continuity; rejection of incompatible configuration changes; exact daily reconciliation; and sufficient transition receipts to reconstruct why each order exists.

Then compare the three predeclared P11 policy schedules and a continuously held baseline using the same starting account and cost scenarios. Include fixed full-recipe references, component-removal schedules, and an explicitly labeled fresh-start sensitivity only if registered. Report continuous equity, boundary turnover/costs, carried exposures/bases/embargoes, recipe selection timing, and transition drawdown. This remains retrospective historical research and carries no execution or promotion authority.
