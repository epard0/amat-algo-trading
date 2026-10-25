# MNQ research for an Apex $50K evaluation

This workflow evaluates the $3,000 target inside independent 30-calendar-day
account windows. It is research only. Apex requires manual execution; there is
no order router, account purchase, payment, or credential access for Apex.

## Run the workflow

From the `amat` directory:

```bash
.venv/bin/python -m autoresearch.funded init --root outputs/my-mnq-study
.venv/bin/python -m autoresearch.funded fetch-sample --root outputs/my-mnq-study
.venv/bin/python -m autoresearch.funded run --root outputs/my-mnq-study
.venv/bin/python -m autoresearch.funded review --root outputs/my-mnq-study
```

`init` freezes protocol bytes and their hash. Changing the target, candidates,
costs, or model requires a new study directory. `fetch-sample` downloads only CSV
data, pinned to a Git commit; upstream executable code is never run.
`run` uses the existing prepare/research/verify LangGraph wrapper, then emits
results, data audit, code/data/protocol hashes and a readable report.
`review` reuses `OPENAI_API_KEY` through the existing model router and budget
ledger. Complex tasks use `gpt-6-sol` with high reasoning. There is no cheaper
model fallback. The request has a 60-second timeout, 3,000 output-token ceiling,
and 60,000 daily ledger reservation limit; reservations conservatively include
prompt bytes. This bounds tokens, not dollars. Missing model prices are unknown,
not zero. API request/response logging is metadata-only; the structured research
review is saved separately without secrets. Missing/failed review is explicit.
The provider may throttle requests even when a model appears in its catalog.

To use a separately acquired CSV instead of the public sample:

```bash
.venv/bin/python -m autoresearch.funded attach-data --root outputs/my-mnq-study \
  --data /absolute/path/mnq.csv --source 'vendor/export reference' --timezone UTC
```

The schema is `datetime,open,high,low,close,volume`. Timestamps must identify
minute starts; naive timestamps use the explicit timezone argument. Preserve
contract-level provenance in vendor exports: the current generic CSV importer
does not independently resolve or validate roll transitions. Supply only a
pre-audited session-level contract selection and retain the original exports.
Hashing data does not verify market provenance. All imports remain unverified
and promotion remains disabled.

## Implemented research controls

- MNQ $2/point, 0.25-point ticks, integer contract sizing.
- Three frozen opening ranges (5/15/30 minutes), risks ($50/$100/$200), and cost
  scenarios. No performance-driven search over this short sample.
- Commission/slippage per contract per side. Values are assumptions awaiting
  the selected platform's fee schedule and recorded quotes/fills.
- Breakout on a completed close; next-open entry with adverse slippage;
  opposite opening boundary stop; one entry/day; exit at 15:59 ET.
- Maximum four contracts internally; affordability includes estimated stop
  and round-trip costs. If one contract exceeds risk, abstain.
- Independent $50K reset per overlapping 30-calendar-day window; $2K floor;
  $3K target; conservative end-of-day pass recognition.
- EOD and intraday trailing compared separately. Tradovate evaluation assumed;
  floor is not capped at starting equity. No PA rule substitution.
- Intraday trail uses favorable-extreme-before-adverse-extreme OHLC ordering as
  a conservative ambiguity bound. Tick replay is needed for actual sequencing.
- Whole missing/incomplete RTH sessions abstain, never forward fill. XNYS cash
  calendar is the RTH reference, not the full CME calendar. Early closes abstain.
- Hash-locked protocol and data, bounded API calls, structured GPT-6 advice.
  Model advice cannot edit results, change targets, or promote candidates.

The EOD daily limit is $1,000; planned one-trade risk is at most $200. The stop
normally precedes the daily loss limit, and no second trade occurs. A gap may
exceed both planned risk and the daily limit and closes at the adverse open.
This implementation models evaluation only, not PA scaling/payouts/inactivity,
subscription/activation costs, margin, or a full broker liquidation engine.
Independent account resets do not make overlapping windows independent samples.

## Improvements to prioritize

1. **Contract and quote provenance.** Obtain several years of expiry-preserving
   MNQ and BBO/tick data, plus the exact fee schedule. Confirm bar timestamps,
   historical roll selection, session completeness, and daylight-saving shifts.
   This is the main blocker to credible conclusions.
2. **Pre-registered walk-forward research.** Freeze training/validation/holdout
   dates, candidate count and acceptance thresholds before viewing outcomes.
   Log rejected trials and use deflated Sharpe/multiple-testing controls.
3. **Improve affordability without simply raising risk.** Test volatility-scaled
   pullback entries or stops tied to invalidation of the signal rather than the
   opposite full opening range. Require positive net expectancy; tighter stops
   can increase churn and lose the original trend edge.
4. **Predict favorable trading conditions.** Test causal opening-volume, gap,
   volatility and scheduled-event filters. Compare filtered/unfiltered systems
   on identical future periods, including every abstention. Avoid post-hoc
   calendar exclusion of losing days.
5. **Test execution uncertainty.** Replay tick paths and bid/ask fills; stress
   manual-entry latency, skipped signals, gaps and outages. Compare EOD and
   intraday account products with their exact current rules.
6. **Optimize economic outcomes.** Evaluate pass/expiry/failure, purchase and
   activation costs, funded-stage payout eligibility, and net rewards. Do not
   optimize annual return as a substitute for 30-day account economics.
7. **GPT-6 proposal and adversarial review rounds.** Keep hypothesis design,
   mechanism critique and failure diagnosis separate from deterministic
   implementation. Every accepted proposal needs explicit parameters and a
   falsification test, then another frozen run. Model confidence is not evidence.
8. **Forward observation.** Run read-only signals on current market data and
   reconcile hypothetical fills with timestamped human actions before any
   evaluation purchase. Alerts must not touch Apex order interfaces.

## Evidence and limits

The initial sample is a public January–April 2026 MNQ export with no per-row
contract IDs. Its upstream updater strips UTC indicators and switches front
month IDs manually. UTC is inferred from that source code; expiry transitions
and vendor accuracy are not independently verified. The sample is suitable for
an exploratory engineering run, not an independent deployment decision.

- Public sample: https://github.com/axb0306/cme-futures-ohlc
- Apex EOD evaluation: https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-evaluations/
- Apex drawdown: https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-drawdown-explained/
- Apex automation agreement: https://dashboard.apextraderfunding.com/agreement/user-agreement
- Counterevidence for simple MNQ signals: https://arxiv.org/abs/2605.04004
- Contract-level data option: https://databento.com/docs/getting-started/build-first-app

No strategy is guaranteed consistently profitable. A research run must be able
to conclude that the target is not supported under the tested risk constraints.

## Adaptive twenty-batch controller

The adaptive controller uses Google's `gemini-4-argon`, verified against the
Google model catalog and the authenticated API model list on 2026-10-14.
This replaces GPT-6 for the adaptive campaign; legacy frozen study definitions
are preserved. It reuses the existing Google API credential without copying it.

```bash
.venv/bin/python -m autoresearch.funded_batches \
  --root outputs/new-adaptive-campaign \
  --source outputs/mnq-apex-research-2026-10-14 --batches 20
```

Each batch freezes its plan before execution, measures up to three bounded
policies, updates an incumbent using training results only, and asks Gemini for
failure diagnosis, workflow advice and next-batch proposals. Allowed changes:
opening range, planned risk <=$200, stop fraction, take-profit R multiple,
breakout buffer, entry cutoff and direction. No model-generated code is executed.
The $3K target, $2K drawdown, 30-day horizon, cost scenarios and contract cap stay
fixed. The score is the worst base/adverse EOD/intraday mean window P&L minus
$2K times its failure fraction. It is a research ranking, not expected rewards.

After each batch the implemented efficiency update removes already-tested
proposals, reuses exact in-campaign policy results and substitutes untested
single-factor ablations where necessary. Free-text model suggestions are
advisory; only validated policy parameters and this fixed controller logic can
change subsequent experiments. The initial candidate and every unsuccessful
candidate remain in the campaign cache.

Training ends before 2026-03-02. Validation begins on that date and is supplied
only once to the final baseline/winner comparison after the last batch. The
public sample has already been inspected, so this is not a pristine holdout.
Many adaptive trials and few independent market observations prevent a robust
profitability claim. Rates from overlapping windows are dependent diagnostics.

`campaign.json` freezes data/code hashes, search space, costs, scoring and limits.
Each `batch-NN/` retains the pre-execution plan, measurements and model status.
`progress.json` is written after every batch; `summary.json` and `REPORT.md`
contain the final selected-policy and validation comparison. Use a fresh output
folder for a new campaign. This controller retains completed batches on failure
but currently has no resume command.

The model router bounds retries per task and charges the token ledger; there
are at most 20 model-review tasks, with a 1,800-token output cap per attempt.
Rate-limit/quota failures trigger a minimum 60-second application backoff and
recorded deterministic proposals; they never become successful model reviews.
No provider/model substitution or trading occurs.

Model source: https://ai.google.dev/gemini-api/docs/models/gemini-4-argon

Adaptive controller update: `--resume` now reconstructs cached candidates and
incumbents from completed batches, retains interrupted attempts, verifies fixed
simulation dependencies, and records controller revisions. Timeout/auth/transport
failures now also trigger provider backoff. When single-axis neighbors are
exhausted, bounded seeded two-axis diversification produces new trials; cached
verification rounds do not count as new research.

## Additional firm metrics

`python -m autoresearch.funded_metrics --campaign PATH --source PATH` reports
consistency concentration/shortfall, adjusted targets, qualifying days, payout
buffer shortfall, profit factor, net expectancy, win rate, EOD drawdown and floor
capacity. Discovery uses the supplied PropFirmRated comparison; rule diagnostics
use primary Apex, Topstep and Tradeify documents. Firm/product/stage are separate
profiles. Tradeify's commission-excluded basis remains missing when only net P&L
is available. Evaluation paths do not establish actual PA payout eligibility;
reward/fee economics and tick-level drawdown remain unknown. Metrics added after
a campaign do not retroactively change its selection objective.

## Stateful best-run history

The controller now syncs every completed batch and final summary into
`outputs/funded-research-state.sqlite3`. Best-run events and final snapshots are
append-only, retain source hashes and evidence bytes, and remain separated by
campaign. `workflow-state.json` is a regenerable projection. Repeated syncs are
idempotent; rewritten policies/scores and changed campaign definitions fail.

```bash
.venv/bin/python -m autoresearch.funded_state \
  --campaign outputs/mnq-adaptive-20-batches-2026-10-14 \
  --visual-output /absolute/path/funded-research-workflow.html
```

The workflow view reads a saved snapshot. Future batches automatically update
the durable database; rerun the command to refresh the visual snapshot. The
selected historical best and workflow stage are remembered by the conversation
host. Historical intermediates have no invented validation result.

Google diagnostic on 2026-10-14: the active key's Gemini 4 Argon free-tier request
and input-token quotas are zero (HTTP 429 RESOURCE_EXHAUSTED). The two recorded
Interactions review calls timed out at 45 seconds. No completed Google review
exists in the campaign. Model-list availability did not establish usable quota.


### Saved-policy robustness audit

`python -m autoresearch.funded_robustness --root outputs/NEW-AUDIT --source outputs/mnq-apex-research-2026-10-14 --campaign outputs/mnq-adaptive-20-batches-2026-10-14 --campaign outputs/mnq-100-iterations-2026-10-14`

Uses six scenarios (three cost assumptions × one/two-minute entry delays), three chronological training blocks, and a seeded five-session moving-block bootstrap. Requires at least ten training entries in every scenario. Selection is frozen before validation, which remains previously inspected and exploratory. Scores are daily diagnostics; selected and prior best policies also receive separate 30-day EOD/intraday account replay. All inputs, hashes, policy trials, ranking, selection, comparisons and sizing sensitivity persist in a fresh output directory. This audit does not replace historical campaign best events or claim statistical confidence after adaptive selection.

Latest completed audit: `outputs/mnq-robustness-v2-2026-10-14/REPORT.md`. No active policy passed the positive robustness gate.
