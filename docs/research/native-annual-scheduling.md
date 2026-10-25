# Native annual strategy and exit scheduling

Annual recipes now run through the native backtest account and instruction builder. A schedule changes decision inputs after a recorded selection cutoff. It preserves the existing account, pending instructions, settled holdings, adjusted entry bases and exit embargoes. Orders already authorized by the preceding decision settle before the new context activates.

`ScheduledDecisionContext` in `src/amat/backtest/runner.py` declares a version, context ID, activation session, selection cutoff, strategy configuration and optional causal signal, allocation scalar, risk overlay and exit policy. Version 1 permits only the searched trend/volatility, allocation, position-count and rebalance fields to change. Universe, execution, cash and accounting fields remain fixed. Unknown versions, account/policy identities, repeated IDs, noncausal cutoffs, unordered activations and incompatible execution policies fail closed. Input arrays and policy mappings are copied before measurement.

Schedules require whole-share purchases and the native cash policy. The optional `full_history` flag includes flat warm-up records without enabling early decisions. Existing runs without a schedule keep their economic behavior.

```python
from amat.backtest.exits import ExitRules
from amat.backtest.runner import ScheduledDecisionContext, run_backtest

context = ScheduledDecisionContext(
    context_id="recipe-2025",
    activation_session=first_observed_session_2025,
    selection_cutoff=last_observed_session_2024,
    strategy=selected_recipe,
    exit_rules=ExitRules(stop_loss=0.05, reentry_embargo_sessions=5),
)
result = run_backtest(
    baseline, panel, baseline.to_universe(),
    actions_by_session=corporate_actions,
    execution_start=account_start,
    decision_schedule=[context],
    whole_share_buys=True,
    max_entry_window_participation=0.01,
    one_way_bps=6,
)
```

The caller must derive recipes, masks and fitted inputs from past-only information. Declaring a cutoff does not prove a supplied model or array is causal. The frozen study records original selection, fit, feature-mask, source and data hashes, and retains the prior inference/checkpoint validation evidence.

`plan_scheduled_decision` in `src/amat/planning/scheduled.py` uses the same instruction constructor with an observed market prefix, actual settled ledger, existing pending intent, entry bases and embargoes. Its future input is an execution-session date supplied by the caller; it requires no fabricated future price or volume. The returned `ScheduledPlan` has `execution_authority="none"`. It does not create executor authorization. Native execution remains responsible for whole-share purchase rounding, cash commitments, observed-window volume caps and fills. A production integration must retain the existing versioned intent contract and authorized quantity, cumulative fills, remaining intent, pending cash, expiry and account/policy identity.

Research evidence is in `outputs/analysis/2026-10-20-native-annual-scheduling/`. The original-design recipes and their fixed transfers onto the current default are separate comparisons. The protected frontier includes historical rejected candidates. Improvements over the default alone cannot earn promotion. The closed twelve-hour campaign and its paused automation are preserved.
