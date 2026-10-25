# Automatic order state and minimal executor CLI

Implemented and deployed to the existing v13 container. Normal operation is
`executor run` (also the default with no command) and `executor status`.
Configuration remains environment-based; Docker reads the private `.env`.
Plans are formed automatically when the session is due. Advanced compatibility
commands remain available through `executor --advanced-help`.

Every daemon tick refreshes `STATE_ROOT/order-registry.json` using the broker
clock plus recent and outstanding order queries. Old outstanding orders are
included even if outside the most recent order-history page.

`pending_market_closed` means an order has remaining quantity and the regular
paper market is closed. Filled quantity is retained for partial orders. Broker
status remains a separate field, including `held` and `pending_cancel`; this
label does not imply completion, expiration, cancellation or permission to trade.
On reopening the registry returns to the observed broker status. Missing
observations are marked unavailable rather than assumed filled. Failed polls
leave the prior observation timestamp intact. State changes are journaled.

Daily report order rows and durable trade events carry the derived status and
the original broker status. Historical reports are not rewritten to imply a
market-clock observation that was never collected.

Corrected terminal-state handling: `done_for_day` can receive updates on the
next trading day, and `suspended` does not establish cancellation or execution.
See [Alpaca order lifecycle](https://docs.alpaca.markets/us/docs/orders-at-alpaca).

Validation: 200 executor tests passed; Ruff formatting/lint and mypy passed.
Live deployment is healthy, uses `executor run`, remains configured for v13,
and its installed source matches the checkout. The automatic registry correctly
labels the SPY pending-cancel order and two held SPY sell orders as
`pending_market_closed`, preserving their distinct broker statuses. No order
was submitted, canceled or otherwise changed for this verification.

The existing disabled/dry-run switches remain enabled while the outstanding
broker cancellation is unresolved. Evidence:
`phase-bc-evidence-2026-09-20/order-state-deployment.json`.
