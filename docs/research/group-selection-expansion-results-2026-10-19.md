# Group selection: broader ETFs, daily refresh and trend quality

## Outcome

The eleven-ETF group strategy with daily selection had the highest full-period
return in this fixed comparison. Expanding to the specified 38 ETFs reduced
full-period returns in all four matched group-selection combinations. The quality
filter produced a return/drawdown trade-off rather than a consistent return gain.
No strategy was promoted and no executor configuration or broker orders changed.

## Controlled design and available data

The registered design is `configs/experiments/group-selection-expansion.json`.
The source-bound autoresearch study is retained under
`outputs/analysis/2026-10-19-group-selection-expansion/`, including frozen Python
source and configurations, the protocol, all 36 checksummed run records and a
Markdown comparison. Protocol SHA-256:
`bb88cc2268c9ec293072dfad2fd9f54e6f9d524779dd267b176781d750cfdc4d`.

Eight factorial arms cross core/broader membership, five-session/daily refresh and
quality off/on. Four additional controls run v13-style and daily selection without
group selection in each universe. Every arm runs at 2/6/10 bps per side. Group
selection remains enabled in the eight factorial arms; residual ranking, the
slow/fast overlay and hysteresis remain disabled to isolate this question. The
native existing quality filter attenuates scores after selection; this experiment
does not add a new entry veto or tune its thresholds.

The broader universe was fixed by economic exposure and a complete daily-history
rule, without selecting its members using measured returns. All 38 proposed ETFs
passed. The same original cluster caps, four-position limit, two-active-group and
two-members-per-group limits, 20% volatility target, 45% symbol cap and unlevered
gross limit remain. The added real-estate cluster has a 35% cap.

| Cluster | Broader membership |
|---|---|
| US equity | SPY, QQQ, IWM, RSP, MDY, XLK, XLF, XLI, XLV, XLP, XLU, XLE, XLB, XLRE |
| International | EFA, EEM, EWJ, EWG, EWU, EWC, EWZ, FXI, INDA |
| Rates/credit | IEF, TLT, TIP, LQD, HYG, BIL |
| Metals | GLD, SLV, GDX |
| Commodities | DBC, USO, DBA, DBB |
| US dollar | UUP |
| Real estate | VNQ |

Economic group membership is separate from cluster caps. Growth contains US and
international equities except XLV/XLP/XLU; real assets contains metals, commodities
and VNQ; defensive contains the declared rates/credit instruments, UUP and those
three defensive-sector ETFs. These labels do not guarantee defensive performance.
Some exposures overlap; the study does not establish that 38 ETFs constitute 38
independent bets. Wider membership also changes group-return estimates and
covariance shrinkage, so its effect is not solely additional stock-picking choice.

All daily series cover the same 2,713 sessions from 2016-01-04 through 2026-10-16.
The expanded source's actual opening execution windows start on 2024-01-02.
Therefore **all accounts start with $100,000 cash at that boundary**, retaining
full earlier history only for signals and selection-calendar phase. An explicit
native `execution_start` option opens the flat ledger one signal lag earlier;
it does not simulate holdings or missed executions in 2016–2023. The first record
is the December 29, 2023 opening cash observation, followed by 701 execution
sessions. Every approach has the same 702-record axis.

This prevents the unfair alternative of comparing a core portfolio invested since
2016 with a broader portfolio unable to fill until 2024. The new results should
not be directly spliced into the preceding study's account paths. They also use
four additional sessions and a different provider snapshot vintage.

The broad sample contains 26,626 observed opening windows out of 26,638 eligible
asset-session cells (99.95%). DBB is missing twelve windows. Native missed-fill
rules remain in force, with no manufactured VWAP or replacement close fills.
Corporate actions, daily-history integrity and identical calendar coverage were
validated through the native loaders. Cash retains the same zero-rate assumption;
BIL distributions are credited only when that instrument is actually held.

## Full-period results: January 2024 through October 16, 2026

Net returns at 2 bps per side. Declines are positive magnitudes. Fill count covers
the complete period; it is not a count of completed round trips.

| Approach | Net return | Largest decline | Filled trades | Net return at 10 bps |
|---|---:|---:|---:|---:|
| v13, core | 56.37% | 14.07% | 398 | 50.66% |
| Daily selection, core | 71.65% | 12.81% | 711 | 57.91% |
| Groups, core | 91.17% | 12.19% | 503 | 80.31% |
| Groups + daily, core | 100.15% | 13.16% | 669 | 85.60% |
| Groups + quality, core | 90.39% | 11.67% | 595 | 80.42% |
| Groups + daily + quality, core | 98.23% | 12.53% | 768 | 82.43% |
| Groups, broader | 60.66% | 13.57% | 608 | 49.97% |
| Groups + daily, broader | 72.66% | 11.57% | 1,039 | 53.06% |
| Groups + quality, broader | 58.60% | 13.48% | 693 | 44.83% |
| Groups + daily + quality, broader | 74.37% | 11.49% | 1,079 | 53.54% |

The two additional broader-universe nongroup controls are also retained and shown
in the dashboard: v13-style returned 31.25%, and daily selection returned 26.39%.

Daily core group selection gained 8.98 percentage points over five-session core
group selection, with a 0.97-point larger drawdown. Its advantage remained 5.29
points at the highest tested cost. Adding quality to the daily core approach
reduced drawdown by 0.63 points, but also reduced return by 1.92 points and increased
fills. The filter can change sizing enough to increase trading; it should not be
assumed to reduce turnover.

Within the wider universe, combining daily refresh and quality beat the wider
five-session group approach, but still trailed the core group reference by 16.80
points and the core daily variant by 25.78 points. This rejects automatic expansion
to this particular broader membership/taxonomy on the evidence examined; it does
not establish that every possible broader ETF universe is inferior.

## Period dependence

| Core group approach | 2024 | 2025 | 2026 through October 16 |
|---|---:|---:|---:|
| Five-session refresh | 7.64% | 38.26% | 28.45% |
| Daily refresh | 18.45% | 34.80% | 25.36% |
| Five-session + quality | 8.23% | 36.73% | 28.66% |
| Daily + quality | 18.24% | 35.67% | 23.58% |

Daily selection's full-period advantage is driven substantially by 2024; it did
not win every year. This is retrospective, previously examined history with a
current curated ETF sample, not prospective proof or delisting-complete coverage.
No statistical significance or guaranteed future improvement is claimed. The
opening-window simulation also remains distinct from asynchronous broker fills.

## Dashboard and reproducibility

The dashboard defaults to **Group selection · broader ETFs**, displaying the full
common comparison period rather than selecting only the recent winning year. An
interactive eight-cell matrix isolates cadence, quality and universe effects.
Growth curves retain the core group reference, v13 and daily-selection controls.
Drawdowns, monthly returns, all twelve approaches and the three-cost table remain
available. The older studies can still be selected. Benchmark reads are bound to
the new study's own verified snapshot instead of borrowing the older core data.

Two opt-in research profiles match the registered core recipes exactly apart from
name/version metadata:

- `configs/strategy/amat_group_selection_daily.toml`
- `configs/strategy/amat_group_selection_daily_quality.toml`

Their existence does not change the active reference or grant trading authority.
For an identical historical comparison, use the registered execution start and
expanded snapshot; running a profile from a different account inception changes
its simulated history.

To reproduce all fixed arms with a new immutable output directory:

```sh
cd amat
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/run_group_study.py \
  --output outputs/analysis/group-selection-reproduction --workers 3
```

All 36 runs completed with zero runner warnings. Targeted tests cover recipe/factor
isolation, unchanged caps, group partitioning, common execution start, no earlier
fills or missed fills, multi-session signal lag, invalid start refusal, and profile
parity. Frontend checks cover use of the correct snapshot, all eight combinations,
twelve approaches and three costs, reference curves, period/cost controls, and
390/1024/1440-pixel layouts. Exact simulator/broker-event parity and prospective
validation remain separate requirements before activation.

Final verification: 154 targeted engine tests and 50 frontend API tests passed,
with one existing Starlette/httpx deprecation warning. Focused mypy checks passed
across three study/runner modules. Targeted Ruff, Git whitespace checks and the
TypeScript/Vite production build passed. Browser checks passed for the new group
study, both earlier studies, simple task flow and responsive charts. The thirty-six
native backtests are additional measurements beyond these regression-test counts.
