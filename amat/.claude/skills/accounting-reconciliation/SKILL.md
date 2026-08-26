---
name: accounting-reconciliation
description: Enforce AMAT cash, position, fill, fee, corporate-action, realized/unrealized P&L, mark, and equity ledger identities for every session.
user-invocable: false
---

Treat the ledger as a first-class auditable state machine.

For each session record opening state, actions, fills, fees/slippage, cash flows, marks, realized/unrealized P&L, and closing state. Enforce:

```text
equity = cash + sum(quantity * raw_mark)
delta equity = market P&L + distributions + cash yield - fees - slippage
```

Every quantity change needs fill/action provenance. Splits preserve equity apart from declared rounding. Dividends follow configured effective/ex/payable semantics and are not duplicated through adjusted returns. Missing marks or ambiguous actions fail the run.

Use exact/integer cash units or a documented numerical policy. Derive tolerances from precision, not observed discrepancy size. Include synthetic lifecycle and full-run reconciliation tests.
