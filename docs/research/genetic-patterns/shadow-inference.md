# P5: frozen diagnostic inference and shadow reports

Implemented 2026-09-28. P5 packages an observed P4 shortlist member as immutable
JSON, evaluates it on explicit completed sessions from a verified daily snapshot,
and records diagnostics in a durable observation ledger. It does not evolve,
refit, allocate a portfolio, create orders, contact a broker, schedule a daemon,
or grant promotion authority. Every artifact and observation declares
`diagnostic_only=true`, `execution_authority=none`, `promotion_authority=none`.

P4 selected no additional pattern. Packaging one of its rejected contenders is
useful for exercising inference and monitoring; it does not change that rejection.
The artifact retains the candidate's inner objective, rejection gates, whether
it was selected, method-level historical-support result, and P4 evidence hashes.
Only observed shortlists are accepted by the CLI; synthetic null selections cannot
be packaged through this path.

## Frozen artifact and parity

`shadow-pattern/1` binds the full native incumbent plus its candle/symbolic
pattern, candidate hash, actual runtime source files, numerical dependency
versions, symbolic registry when applicable, exact universe order, calibration
start/end and panel content digest, required history, and fixed monitoring policy.
A checksum envelopes the payload. It also stores the final five calibration
rows of pattern values, baseline scores and modified scores, plus reference
statistics over the final 252 calibration rows where history permits.

The native baseline must have no fitted signal adapter, auxiliary feature overlay
or entry model. Inference calls the existing research `build_signal` path and
uses the same frozen feature normalization and arithmetic. No outcome labels are
read and no transform is refit. A changed formula, source file, runtime version,
registry, universe, authority field or calibration input is refused. Existing
artifact files cannot be overwritten.

Observations must supply full history starting at the frozen calibration origin.
This preserves recursive feature state such as RSI; truncating to an arbitrary
recent window is not equivalent. Before a snapshot observation is published,
the entire calibration prefix digest, numerical probes and recomputed reference
statistics must match. This wrapper refuses changed historical data before adding
a ledger record. The lower-level inference API reports suspended neutral scores
on calibration parity failure. This conservative treatment also means a revised
historical adjusted series requires a newly reviewed artifact.

## Reported values and monitoring

Each asset's report contains the raw pattern value, native baseline score,
candidate score, effective reported score, bounded contribution and availability.
The candidate's effect remains limited by its existing `maxEffect` cap. Zero
baseline scores stay zero. Missing numerical evidence is neutral.

Monitoring compares up to 20 post-calibration sessions with frozen reference
statistics. The registered policy is:

| Check | Threshold |
|---|---:|
| Minimum available fraction | 0.80 |
| Maximum absolute mean shift in reference standard deviations | 4.0 |
| Maximum positive-prevalence shift | 0.35 |
| Maximum mean absolute one-session candidate-score change | 0.50 |

Missing latest values, absent/degenerate calibration evidence, drift, probe
mismatch or excessive score change generate explicit alerts. Any alert suspends
the pattern's **reported contribution for every asset**, reverting reported scores
to the native baseline. The unmodified candidate scores remain visible for
explanation. Completely absent candle inputs may refuse inference outright;
unavailable symbolic terminals produce neutral suspended reports.

Score change is a diagnostic proxy, not trade turnover or portfolio P&L.
Baseline-versus-candidate score differences are not evidence of realized ablation
benefit. Those measurements require a qualified pattern and subsequent portfolio
or prospective observations. The command measures inference/monitoring latency;
it is not an end-to-end market-ingestion latency benchmark.

## Durable observations

The ledger is bound to one artifact. Records contain a sequence number, prior hash,
observation hash and full report; the envelope records the count and chain head.
Writes use an exclusive lock, atomic replacement and file/directory fsync.
Corruption, inconsistent truncation, artifact switches, date rewinds and changed
same-session inputs are refused. An identical retry returns the original record;
only top-level measured latency is excluded from observation identity.

Suspension latches for the ledger. Later healthy observations cannot silently
reactivate the pattern. The writer applies this rule under its lock and neutralizes
reported contributions before storing a subsequent observation. A new reviewed
artifact/campaign is required to begin a new monitoring history. This is a
monitoring lifecycle, not an authorization to trade.

Report paths must be distinct from artifacts and ledgers. JSON and Markdown
reports can be regenerated idempotently. Hashes detect accidental modification;
these local files are not cryptographic signatures or a remote trusted log.

## Commands and scope

Run from `amat/`. Use new output paths for a new artifact/campaign:

```bash
uv run --locked autoresearch patterns shadow-freeze \
  --validation-study outputs/genetic-patterns/2026-09-28-p4 \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --fold outer-2021 --method genetic --rank 0 \
  --output outputs/genetic-patterns/2026-09-28-p5/artifact.json

uv run --locked autoresearch patterns shadow-observe \
  --artifact outputs/genetic-patterns/2026-09-28-p5/artifact.json \
  --snapshot data/snapshots/alpaca-sip-core-2026-09-14-verified \
  --as-of 2026-09-11 \
  --ledger outputs/genetic-patterns/2026-09-28-p5/ledger.json \
  --output outputs/genetic-patterns/2026-09-28-p5/2026-09-11.json
```

The freeze command verifies the complete P4 evidence manifest and confirms that
the calibration panel is exactly the corresponding P4 fit panel. The observe
command requires `as-of` after calibration, an available snapshot session, a
complete exchange calendar, and the exact universe. It reads only through that
session. It does not interpret a stale snapshot as a current live feed.

The initial implementation supports verified daily-snapshot diagnostics. Existing
live/news extensions still need complete coherent OHLC and an explicit completed-
bar contract before a live ingestion path can be connected. No recurring task,
broker integration or paper-trading process was started. Historical observations
are labeled historical and cannot substitute for time spent collecting future
paper evidence. See [P5 verification](p5-verification.md) for measured results.
