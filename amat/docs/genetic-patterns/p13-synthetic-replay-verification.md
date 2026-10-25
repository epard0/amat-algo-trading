# P13 synthetic replay results and verification

Completed October 2, 2026 in the isolated P13 worktree. **544 of 544 registered
runs completed, with zero failed cells:** 17 families × 2 seeds × 4 policies ×
4 one-way execution costs (2, 5, 10, 20 bps). Every path retains all **2,698
sessions from January 4, 2016 through September 25, 2026**, including warmup and
flat periods. The combined account evidence contains 1,467,712 session records.

The first 1,006 sessions use the observed prefix; the generated suffix begins
January 2, 2020. This endpoint is the last verified complete research calendar,
not a claim that all later provider data are unavailable. Later retained receipts
still lack the reconciled opening-window execution inputs required by this bundle.
Daily, 15-minute, hourly, weekly, and monthly numerical features are recomputed
inside each immutable world. Original P11 recipes and historical eligibility masks
remain frozen. No adaptive optimization or synthetic-world refitting occurred.

## Read the results

- [Paired comparison report](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-02-p13-synthetic-replay/study/report.html)
- [World equity, drawdown, market context, and associated data](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-02-p13-synthetic-replay/analysis/report.html)
- [Policy summary CSV](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-02-p13-synthetic-replay/study/policy-summary.csv)
- [Event/adoption coverage](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-10-02-p13-synthetic-replay/analysis/event-coverage.csv)
- [Implementation contract and next steps](p13-synthetic-replay.md)

The report contains 2,176 four-scope comparisons, 5,984 annual rows and 2,720
prior-close regime rows. The gallery has 34 world pages. Each links to the
canonical five-minute bars, daily/native views, actions, feature arrays and
availability metadata, policy schedules, and exact account/execution traces.
Annual and regime summaries are slices of the same continuous accounts.

Post-selection paired return differences against the same-world baseline are
shown below. Percentage points are differences in return, not relative changes.
Each row describes the finite 34-world stress set, whose families share random
numbers within seed; counts are not independent samples or probabilities.

| Policy | Median difference at 2 bps | Wins / ties / losses at 2 bps | Range at 2 bps | Median difference at 20 bps |
|---|---:|---:|---:|---:|
| Balanced | +5.00 pp | 22 / 2 / 10 | −11.62 to +19.13 pp | +5.03 pp |
| Return | +4.29 pp | 20 / 2 / 12 | −9.58 to +16.69 pp | +1.07 pp |
| Defensive | +3.38 pp | 21 / 2 / 11 | −11.47 to +15.46 pp | +3.94 pp |

At 2 bps, median absolute post-selection returns are −0.16% for baseline, +6.27%
for balanced, +0.55% for return and +3.06% for defensive. These medians do not
subtract to the median paired differences because every contrast is matched
within its world. Worst post-selection drawdowns in this stress set are −55.52%,
−38.34%, −50.96% and −44.60%, respectively.

These synthetic findings differ from the retained historical foundation. On the
historical post-selection interval at 2 bps, baseline returned **54.50%**, versus
**26.58% balanced, 39.59% return and 29.67% defensive**. The stress-set median is
therefore not a validated choice for the live paper mechanism. Whole recipes
change allocation/risk and exits as well as features; these comparisons do not
isolate a GP contribution. Matched component and risk controls remain necessary.

## Event timing limitation and next experiment

The declared crash, gap and liquidity shocks occur on **April 2, 2020**. The
liquidity halt ends April 9; recovery markers fall on May 1. All precede first
P11 adoption on January 2, 2024. They test baseline shock response and inherited
account state, not direct active-policy exposure to those transient shocks.
The correlation-break regime begins July 2, 2020 and persists through the active
period; its later exposure remains valid. Persistent distributional, bull, bear,
range and volatility scenarios also continue through the active period.

The next implementation should register shock timing inside each 2024/2025/2026
policy context, with sufficient recovery horizon, before simply multiplying seeds.
Retain every earlier session. Then expand parameter and TP/exit sensitivity using
separate development, selection and evaluation worlds. The selected methods in
this smoke are GP, illiquidity:21, momentum:63 and variance_ratio:21; the rest of
the method registry and new parameter values are not claimed as tested here.

## Verification

- **221 scoped tests:** 211 adapter/controller/report and related regression tests,
  plus 10 supplemental gallery/event-coverage tests. Owned files pass Ruff;
  scoped Mypy passes. The checks use `--follow-imports=silent` to avoid unrelated
  existing dependency typing failures.
- **544 run receipts and 1,632 exported data files** independently verified.
- **408 policy/baseline comparisons** match exact JSONL bytes over all **2,012
  pre-adoption sessions** through December 29, 2023. No selected recipe changes
  the account before activation.
- All **48 export hashes** from the 16-run bull preflight match the full campaign.
- All **32 flat-world paths** cover 1,692 generated sessions; maximum absolute
  generated market-P&L residual is **2e−23**, below the engine tolerance of 1e−8.
- Full resume verifies every run while forbidding numerical work: **zero
  backtests, feature calculations, context preparations, and synthetic-world
  loads**. All **2,493 study files** retain identical hashes and modification times.
  The instrumented serial verification uses the same controller/world resume path.
- The runtime archive matches **189 registered source files**. Of original
  generator modules, only `symbolic.py` changes: an AST proof permits exactly the
  numerical interpreter extraction while preserving its normal source guard.
- All **3,793 prior evidence files** across P11, P12, foundation, sensitivity and
  generator bundles match their retained checksums.

The archive `runtime-and-tests.tar.gz` remains the original generator runtime;
`replay-runtime-and-tests.tar.gz` contains this stage's implementation. The patch,
source list, registrations, execution logs and verification helpers are retained
in the evidence bundle. The normal CLI runs controller verification before the
report. Standalone report validation is less strict about rederiving metrics;
that and explicit complete/failed final summaries should be hardened before the
extended campaign. This smoke stops and retains an error if a cell fails.

## Measured capacity and isolation

The complete replay and primary report took **1,128.41 seconds (18.81 minutes)**
with six workers. Second-seed generation separately took **552.30 seconds**.
Across both seeds, market worlds occupy **2.62 GiB** and replay/feature/schedule
artifacts **1.95 GiB**. Linear extrapolation to 1,088 worlds / 17,408 runs is
approximately **146 GiB** of market and replay evidence and **10.03 hours** of
replay at this throughput, excluding generation, adaptive fitting, reports,
backups, retries and headroom. This is a planning estimate, not a runtime promise.
Only about **70 GiB** was free at measurement: the extension needs a storage plan.

This stage did not deploy to Docker. The executor was healthy with DRY_RUN=true
and EXECUTOR_DISABLED=true. All 81 checked `src/amat` core files match the earlier
host baseline. Separately, 21 host autoresearch/llmrouter/test files differ from
that older baseline; their authorship was not determined by the read-only audit.
It would be inaccurate to claim the entire host workspace was unchanged. This
stage's implementation and runs remain in the isolated research worktree.
