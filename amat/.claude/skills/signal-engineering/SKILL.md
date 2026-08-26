---
name: signal-engineering
description: Implement AMAT's causal 21/63/252 trend signal, EWMA volatility normalization, clipping, tanh combination, deadband, and warm-up conventions in framework-neutral code.
user-invocable: false
---

For AMAT signal work:

1. Use adjusted completed-session closes and log returns.
2. Freeze EWMA semantics: half-life, bias/adjust behavior, minimum periods, null policy, annualization, and index alignment.
3. Compute 21/63/252 horizon returns using data available through decision session `t`.
4. Normalize by annualized volatility and horizon scale; clip each horizon to `[-2, 2]`.
5. Average, apply `tanh`, dead-band absolute scores below `0.20`, and floor negative scores to zero for v1.
6. Record feature timestamp and decision timestamp separately.
7. Keep scalar/reference and vectorized paths numerically equivalent.

Required tests: constant/rising/falling paths, clip/deadband boundaries, warm-up, missing observations, future mutation, and next-session execution linkage. Do not tune parameters while implementing.
