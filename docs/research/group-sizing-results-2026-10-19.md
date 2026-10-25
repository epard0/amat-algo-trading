# Group-specific inverse-volatility sizing — 2026-10-19

**Decision: reject all three candidates. All-groups + volatility-triggered cost-aware switching remains the leading strategy and verified overall default.**

## Fixed design

Each of the existing growth (SPY, QQQ, IWM, EFA, EEM), real-assets (GLD, SLV, DBC, USO), and defensive (IEF, UUP) groups uses its own equal-member geometric adjusted-price index. Each asset is normalized to its first observed close before geometric averaging, so nominal share prices cannot bias the trigger. Group membership is fixed by the current strategy, not chosen from future performance.

Three independently preregistered conditions: index below its completed-close 200-session mean; index 20-session annualized sample log-return volatility above 25% OR above 1.5 times its 60-session volatility; either condition. Conditions use only completed closes, with next-session execution.

Only selected members of a triggered group are reweighted by score/volatility. Their combined score mass is normalized back to that group’s original score mass before the existing portfolio covariance target and clipping. Other groups retain incumbent sizing. Final group dollar weights can still change through portfolio-wide risk targeting and clipping. This tests within-group composition, not an additional defensive cross-group allocation rule.

No signals, eligibility, switching, caps, participation limits or rebalance bands changed. Inactive defaults are omitted from serialization, preserving incumbent config identity. No parameter tuning followed outcomes.

## Measured results

Full history: January 3, 2017–October 12, 2026, 2016 warmup. Cumulative returns after assumed one-way costs; drawdown positive magnitude.

| Approach | Return 2 bps | Return 6 bps | Return 10 bps | Drawdown 6 bps | 2020 onward 6 bps | 2023 6 bps | Fills 6 bps |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current best · all groups + volatility switching | 290.52% | 264.58% | 226.81% | 12.77% | 198.77% | 5.79% | 1943 |
| Group sizing · own weak trend or volatility stress | 278.01% | 247.72% | 222.48% | 12.79% | 190.34% | 6.28% | 1981 |
| Group sizing · own volatility stress | 286.65% | 258.48% | 226.80% | 12.79% | 198.60% | 6.08% | 1965 |
| Group sizing · own weak trend | 280.42% | 251.01% | 218.89% | 12.77% | 189.00% | 5.98% | 1973 |

All three lose full-history return at every cost. Volatility-only comes closest: 258.48% versus 264.58% at 6bps, a 6.11 percentage-point loss. Its 2020-onward return nearly matches the parent (198.60% versus 198.77%), but full-history performance fails and drawdown is slightly worse. Weak-trend-only drawdown is nearly unchanged. All preserve 2023 at every cost, which is insufficient to pass the complete criteria.

Acceptance, registered before measurement: match/exceed current-best full-history AND 2020-onward returns at every 2/6/10bps; no worse full-history drawdown at 6bps; preserve 2023 at every cost; at least one strict return gain. No candidate passed. Rejected candidates are excluded from active selection; frozen recipes remain solely for audit/reproduction. No new strategy profile was retained.

## Stress returns at 6 bps

| Approach | 2018 Q4 | COVID window | 2022 |
|---|---:|---:|---:|
| Current best · all groups + volatility switching | -7.08% | -9.47% | 2.42% |
| Group sizing · own weak trend or volatility stress | -7.24% | -9.49% | 2.83% |
| Group sizing · own volatility stress | -7.14% | -9.49% | 2.62% |
| Group sizing · own weak trend | -7.23% | -9.47% | 2.54% |

## Activation share of completed decision days

| Condition | Shares | Real assets | Defensive assets |
|---|---:|---:|---:|
| Group sizing · own weak trend or volatility stress | 21.73% | 36.83% | 37.28% |
| Group sizing · own volatility stress | 11.07% | 12.54% | 0.81% |
| Group sizing · own weak trend | 19.05% | 28.77% | 36.47% |

## Default and dashboard

Verified guided actions, historical CLI/autoresearch defaults and executor selected profile remain `amat_group_selection_volatility.toml` (v14.3.0). This already-leading profile therefore satisfies the user instruction to use the leading strategy as default. Its raw file hash matches the configured authorization identity. Executor remains disabled and dry-run. A sealed `default-selection.json` records this decision.

The dashboard opens on the group-specific sizing study. It shows all four approaches in the visual audit, all three costs, annual/stress comparisons and nine activation meters. Rejected candidates cannot be selected as active strategies. Previous studies remain available.

## Execution and limitations

Verified Alpaca SIP eleven-ETF core snapshot; flat $100,000 January 3, 2017; full 2016 warmup; next-session observed 09:30–09:35 VWAP; dividends/splits, whole shares, native 1% opening-volume entry cap and recorded missed fills. Native exit policy unchanged. Zero cash yield. Combined spread/commission/impact assumptions 2/6/10bps per side are not measured historical quotes.

Annual 2020–2026 slices use continuing accounts, not yearly resets or freshly fitted models. All history has prior research exposure. The 2023 guard is not a holdout; paired bootstrap intervals are descriptive, unadjusted for cumulative research search. No future profitability or global optimality is established.

Native baseline reproduces every frozen parent session at all three costs. Data partition bytes and canonical seals on protocol, 12 trial records, decisions, activation and default selection are verified. Tests cover per-group isolation, score-mass preservation, no revival of unselected assets, future-price invariance, share-price-unit invariance, configuration round trips and legacy behavior.

## Reproduce

```sh
amat/.venv/bin/python amat/scripts/run_group_sizing_study.py --output /new/immutable/output
```

Evidence: `amat/outputs/analysis/2026-10-19-group-sizing/study/`. Source/config copies and conditions frozen before any measurement.
