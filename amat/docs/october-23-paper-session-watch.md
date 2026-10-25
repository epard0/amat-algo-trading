# October 23 remote paper-session watch

The VPS minute timer owns collection independently of the desktop. The explicit
watch expects zero AMAT orders from the verified October 23 hold plan, hash
`0454430804e9c8e08e8abb7c2485087fcc351391d4972c3aabe023417a1a786e`.
The plan uses October 22 data and the unchanged v14.3.0 strategy. Native staged
execution and whole-share purchases remain enabled in paper mode.

Any broker order during this pinned hold session is an explicit unexpected-order
alert. Duplicate identities, changed plan digest, strategy/account/policy mismatch,
fractional purchases and blocked portfolio reconciliation remain checked. The
monitor never submits, retries or cancels an order.

Perps' completed-hour decision readiness, target weights and exit reasons continue
to be recorded alongside native fills, fees and funding. The stale-decision alert
branch had a two-argument list.append error. It is corrected and extracted into a
pure tested alert function, so stale/unready evidence produces an alert rather
than interrupting a collection cycle. Signals remain subject to the bot's own
execution rules; monitoring does not force entries.

Both bots retain the shared fresh pre-open baseline, minute return curves,
source-reported fees/funding and frozen close comparison. Unknown AMAT fees and
unavailable spread/impact remain unavailable. Detailed source observations, daily
summaries and alert history are stored on the VPS and fetched by the dashboard.
The October 22 planning failure remains identified as a historical incident with
its repaired cause, separate from the verified October 23 hold plan.

At setup the session was still upcoming: no October 23 AMAT orders were observed,
the hold-plan digest matched, and the perps decision was ready with zero DOGE/SOL
exposure. Those are pre-session observations, not completed-session outcomes.
The timer was enabled, active and collecting successfully, with user lingering
confirmed. The dashboard displays the named watch and keeps both cost rows and
performance charts. Backend checks: 25 passed. Frontend build and actual remote
browser checks passed. Evidence: `outputs/paper-monitor/2026-10-23-watch/`.
