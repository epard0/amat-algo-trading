# P13 foundation verification and measured historical results

The isolated implementation completed **16 historical primary runs**, each covering
all **2,698 sessions from 2016-01-04 through 2026-09-25**. Runtime source identity
binds 182 files; the complete run evidence is retained in the foundation bundle.
The source archive and scoped patch support review without changing the
Docker-mounted checkout.

## Results at 2 bps one way

| Policy | Full-history net return | Full-calendar CAGR | Full-history max drawdown | Post-selection net return |
|---|---:|---:|---:|---:|
| baseline | 253.59% | 12.50% | -15.01% | 54.50% |
| balanced | 189.68% | 10.43% | -14.95% | 26.58% |
| return | 219.46% | 11.44% | -14.44% | 39.59% |
| defensive | 196.76% | 10.67% | -14.60% | 29.67% |

The post-selection view starts from the actual December 29, 2023 equity.
The baseline earned more than all three switching profiles at **every tested
cost level (2/5/10/20 bps)**. Drawdown differences at 2 bps are modest and do not
establish an overall superior switching mechanism. These retrospective results
provide no basis for promoting one of the current switching policies over the
baseline. Component and timing controls are the next discriminating tests.

The tradable-history anchor is 2016-12-30; first actual fills occur 2017-01-04.
Full and tradable net returns coincide because this recipe's cash rate is zero
during warm-up, while their elapsed-time CAGR denominators differ. Exported
comparison.csv preserves both definitions, plus post-selection results.

## Verified invariants

- Every source session is present, including 251 cash-only warm-up rows.
- At each cost, baseline and all selected paths have identical cash/equity/exposure
  through the first activation session, before any new-recipe order can settle.
- Daily account changes reconcile to market P&L, distributions, cash yield, fees
  and slippage; exact Decimal state remains in JSONL alongside plotting floats.
- A completed resume revalidated data checksums and preserved all **64 per-run
  receipt/data files** without rewriting them.
- The unchanged default runner matched **7/7 frozen P11 recipe/fold receipts**,
  including **1,624 daily returns** and all evaluator metrics, fills and warnings.
- **33 P13 tests passed**, including input freezing, scheduled transitions,
  splits/dividends, exit basis/embargo carry, warm-up cash, metrics/export checks,
  and mixed-registration report rejection. All five changed/new runtime modules
  pass strict mypy; the scoped code and tests pass Ruff and formatting.

The broad run recorded **2,764 passed, one skipped and two initial failures**.
The failures were a controller-identity guard while new source files were still
being edited, and five missing pre-existing baseline-reference fixture files in
the isolated copy. The source was frozen, the fixtures were copied with their
original hashes verified, and both tests passed targeted reruns. Four report
tests added after initial collection also passed. In total **2,770 distinct
cases passed across the broad and targeted runs**, with one existing skip and no
unresolved failures. Full logs and the individual reruns are retained; this is
not presented as a single uninterrupted all-green test invocation.

## Data and operational boundaries

The data audit preserves original partitions, action exceptions, and native
receipts. September 28 retained daily/native overlap is consistent, but observed
raw opening-window VWAP/volume is absent; the complete endpoint remains September
25. Coverage includes explicit pre-2019 missing intraday views and two
machine-precision candle-ordering flags without changing original values.

The first integration attempt was invalidated by the runtime-change guard and
is retained in superseded-integration-attempt for audit only. It is not included
among successful final runs. No synthetic worlds, new parameter search, new
market downloads, candidate promotion, or Docker deployment occurred.
A read-only final container check found amat-executor running and healthy with
DRY_RUN=true and EXECUTOR_DISABLED=true. Host runner/exits source hashes remain
unchanged from isolation.

## Evidence

- [HTML performance report](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-foundation/study/report.html)
- [Complete comparison table](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-foundation/study/comparison.csv)
- [Data coverage audit](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-foundation/data/report.md)
- [Reproduction script](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-foundation/reproduce.sh)
- [Scoped implementation patch](/home/supasuge/Documents/amat-p13-workspace/amat/outputs/genetic-patterns/2026-09-30-p13-foundation/p13-foundation.patch)
