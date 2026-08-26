---
name: portfolio-construction
description: Implement AMAT's Ledoit-Wolf covariance, inverse-volatility exposure, 8% target, symbol/cluster/gross constraints, cash residual, rebalance threshold, and risk attribution.
user-invocable: false
---

Portfolio construction must be deterministic and causal:

- estimate annualized covariance from the prior 120 completed sessions only;
- compute raw exposure as positive score divided by volatility;
- scale toward 8% forecast volatility when finite;
- apply configured symbol caps, cluster caps, risk limits, then 100% gross cap in a frozen order;
- recalculate risk after clipping and leave unallocated weight in cash;
- never re-leverage above 100% gross to hit the target;
- generate trades only for threshold/transition/constraint/action/fill-recovery conditions;
- persist target weights, post-constraint weights, cash, forecast risk, and component contributions.

Property tests must cover nonnegative weights, gross/caps, all-cash behavior, finite failure handling, determinism, and no future covariance rows.
