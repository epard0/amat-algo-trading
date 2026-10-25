# Minute refitting: experimental contract and current evidence gap

This is a separate experiment from the registered daily nested study. The daily
study fits fresh GP programs inside annual folds, but cannot establish the effect
of refitting every 250 completed one-minute bars. Do not promote a daily outcome
as evidence for that minute policy.

## Data scope awaiting the user's choice

The inspected local archives provide:

| Archive | Universe | Declared interval | Relevant limit |
|---|---|---|---|
| P13 adjusted minute receipt | All 11 v13 assets | 2026-06-01 through 2026-09-28 | 557,838 records including extended hours; requires regular-session validation and missing-minute audit |
| Existing minute parquet archive | SPY, QQQ, IWM, GLD | 2022–2026 | Longer history but a different portfolio universe |
| P13 native 15-minute archive | All 11 v13 assets | 2019–2026 | Its metadata explicitly says coverage research only, not an inference input; it is not a minute dataset |

The choices presented to the user are: build and validate longer full-universe
minute history; run a recent full-universe mechanism pilot; or run a longer
four-asset research pilot. No answer has been inferred from elapsed time.

Archive date ranges are not claims of complete minute coverage. An adjusted
historical bar receipt also does not reconstruct the original arrival times and
revisions that a live model would have seen. The experiment must state whether
it assumes finalized bars with an availability delay or uses actual versioned
arrival records.

## Required comparison

Use four named arms with the same universe, capital, costs, entry clock, exit
clock, fill model, and risk policy:

1. The baseline with its original daily signal horizon preserved.
2. Conventional minute features with frozen parameters.
3. GP fitted once on the training prefix, then frozen.
4. The same GP fitting procedure refreshed every 250 qualified minute timestamps.

The adaptation contrast is arm 4 versus arm 3. Arm 4 versus the baseline also
contains the effect of adding GP. Compare to arm 2 to distinguish GP from a
simpler feature model. A separate cadence factorial may vary the shared decision
clock, but it must not vary that clock only for the adaptive arm.

## Five independent clocks

| Clock | Event | State and evidence |
|---|---|---|
| Ingestion | A qualified completed minute becomes available | Bar key, event timestamp, arrival/version timestamp, data watermark |
| Inference | A newly admitted completed bar reaches the configured decision interval | Latest accepted model ID, complete input cutoff, target decision ID |
| Risk checks | A mark/account event or the configured heartbeat | Realized positions, outstanding orders, exposure and applicable limits |
| Order management | Fill/cancel/reject events, with reconciliation heartbeat | Idempotent order IDs and portfolio/account checkpoint |
| Training | 250 new qualified global minute timestamps since the previous trigger | Frozen prefix, label maturity boundary, challenger artifact, fit timing and acceptance result |

Count one shared minute timestamp, not one observation per asset. Eleven assets
must not make the counter advance eleven times faster. Duplicate deliveries and
revisions of an already counted timestamp do not increment it. A revision may
invalidate an affected artifact or trigger a separately recorded correction, but
must not silently generate a second trading decision for the original bar.

The clock crosses session boundaries and persists across process restarts. It
does not count closed-market wall time. Missing-bar eligibility must depend only
on information already available at that timestamp; rejecting a whole session
because a later minute is missing would leak future data.

## Training and activation contract

- The 250-bar interval is the trigger cadence, not the training-window length.
  Declare the rolling training window, label horizon, grammar, compute budget,
  warm-start policy and acceptance criterion separately before selection.
- Freeze a data prefix at trigger time. Exclude each training example until its
  complete forward label is observable, including any execution lag.
- Train a challenger asynchronously while inference uses the last accepted model.
  Do not retroactively apply the challenger to bars that arrived while it trained.
- Permit one outstanding fit per strategy/data version. Coalesce later triggers
  into an explicitly recorded next job rather than accumulating stale workers.
- Acceptance uses only pre-activation evidence. Swap model and preprocessing
  atomically at a subsequent eligible decision boundary. Store the old artifact
  for replay and rollback.
- If fitting fails, finishes too late, lacks enough mature labels, or fails its
  acceptance gate, retain the incumbent and record the reason. Do not silently
  substitute a better random seed or suppress the unsuccessful fit from counts.

The artifact must retain trigger bar count, training start/end, last mature label,
data and source digests, random seed, parent model ID, fit start/end time,
activation timestamp and the first decision that actually used it.

## Required checks before interpreting minute returns

Replay duplicate and late bars; revise already counted bars; restart between
249 and 250; simulate slow training, failed fitting and overlapping triggers;
confirm label maturity and activation order; and replace unseen future data to
verify identical earlier signals and fills. Preserve position and pending-order
state across model changes. Reconcile fees, cash, corporate actions and holdings.

The existing daily runner cannot be converted by labeling minute timestamps as
daily sessions. That would change volatility annualization, horizon units,
calendar semantics and execution timing. The minute adapter must reuse the
accounting components while making those units and timing contracts explicit.

For inference frequency experiments, report accepted decisions, attempted orders,
fills, turnover and net incremental growth separately. More polls are not more
independent information; more trades are not evidence of more predictive value.
