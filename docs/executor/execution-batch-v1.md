# Execution batch contract, version 1

`execution-batch/1` is the durable continuation contract for a frozen AMAT plan.
The paper executor implements it in `src/executor/batch.py`. The scheduler uses
it only when `EXECUTION_MODE=staged-v1`; the default remains `legacy`. This change
does not enable a running daemon, change its environment, or submit orders.

The inert AMAT plan remains the original authorization. A batch cannot increase
that authorization, move it to another account or session, or extend its expiry.
Each continuation validates the checkpoint against the archived original plan.

| Field | Meaning |
|---|---|
| `schema`, `batch_id`, `plan_sha256` | Version and identity of the original frozen authorization |
| `account_id`, `config_sha256`, `policy_id`, `policy` | Account binding, strategy identity, and all execution-policy settings |
| `execution_session`, `expires_at`, `submission_expires_at` | Session and immutable authorization cutoff, bounded by the execution window |
| `intents[].original_authorized_quantity` | Original quantity; never replaced with a smaller submitted quantity |
| `intents[].cumulative_filled_quantity` | Observed broker fills; monotonically increasing and capped by authorized/submitted quantity |
| `intents[].remaining_intent_quantity` | Original quantity less cumulative observed fills |
| `intents[].order` | Exact broker/client order identity, submitted quantity, status, and bounded buy limit |
| `cash_observation`, `pending_cash_commitments` | Last cash evidence and the unfilled tracked buy commitments still reserved |
| `sha256` | Integrity digest of the stored checkpoint; original-plan comparisons additionally protect immutable fields |

Checkpoints live at `STATE_ROOT/batches/YYYY-MM-DD.json`. The caller holds the
existing submission lock. Each order authorization is written before the single
POST. Existing receipts and client IDs preserve restart idempotency. Operations
reporting attributes resized orders through their matching archived original
plan and batch; ticker similarity is never sufficient evidence.

## Continuation rules

1. Reconcile every known order by exact identity. Missing broker evidence or an
   uncertain acknowledgment stays unresolved and never authorizes another POST.
2. Send eligible sales first. Nonterminal sales defer the buy stage. A partially
   filled, canceled sale can contribute only cash actually observed afterward.
3. Freeze proportional buy budgets from fresh cash and available buying power,
   with the policy cash reserve, existing tracked buy commitments, price buffer,
   fee allowance, and native notional/order limits applied. Do not subtract the
   same commitments twice from broker buying power.
4. Recheck cash before each buy. Submit DAY limit buys at a tick-rounded price
   bounded by the original reference plus the policy buffer. Sales remain market
   orders. Existing native preflight and submission interlocks still apply.
5. Observe partial fills without creating replacement orders. Terminal orders
   with unfilled original intent finish as `complete_with_residual`; a new plan
   requires separate authorization. Remaining intent is accounting, not permission
   to replace, top up, or retry an order.
6. At the immutable cutoff, request cancellation of known open orders once and
   keep their cash committed until the broker confirms a terminal state. A cancel
   request is not proof of cancellation. Late fills remain reconcilable.

The scheduler polls pending batches without consuming its phase failure budget.
An existing unresolved batch may be read for reconciliation while submissions
are paused; the inner submission checks continue blocking new orders.

## Policy and limits

`confirmed-cash-policy/1` currently defaults to a 1% equity cash reserve, 0.2%
price buffer and 0.02% fee allowance. All settings contribute to policy identity.
The broker's reported cash and available buying power bound admission; the
contract does not independently reconstruct regulatory settlement or predict
actual fees. Policy allowances are assumptions, not guarantees about broker costs.

This implements authorization, cash, and restart consistency in the executor.
It does **not** establish identical fill paths between the historical simulator
and a broker. The historical daily experiment uses an aggregate opening-window
proxy, while the executor now uses bounded limit buys, actual asynchronous fills,
and cancel acknowledgments. Exact simulation parity still requires an event-level
replay adapter using this same contract and matched broker-event fixtures.

## Verification

Fake-broker regression tests cover staged sales, partial fills, canceled residuals,
acknowledgment loss, missing broker evidence, restart behavior, account/policy/plan
identity changes, original-quantity tampering, expiry and cancellation races,
cash commitments, and symbol/session caps. These tests place no real orders.

## Whole-share paper deployment

`EXECUTION_MODE=staged-v1` with `WHOLE_SHARE_BUYS=true` rounds every new purchase
and cash recheck down to integral shares, including fractionable ETFs. The flag
is part of the frozen policy identity. Fractional defaults keep their old policy
identity so this change cannot silently reinterpret existing batches. Original
plan authorization is retained; unspent rounding residuals are reported, never
topped up automatically. Sales retain the existing native exit behavior so legacy
fractional holdings can be reduced or closed without stranding dust.

Decision parity is checked against the native backtest scheduler at identical
holdings, decision marks and cost settings. This does not claim identical fills:
asynchronous cash confirmation and bounded broker limits differ from observed
historical 09:30–09:35 VWAP and its 1% volume cap. Monitor actual outcomes separately.
