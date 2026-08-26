---
name: execution-cost-modeling
description: Model AMAT next-session execution, VWAP fills, side-adjusted fees/slippage, missed fills, participation limits, and cost-stress scenarios without same-bar assumptions.
user-invocable: false
---

Execution simulation rules:

- form targets after session `t`; execute only in the permitted window of session `t+1`;
- canonical price is raw 09:30-09:35 America/New_York VWAP;
- apply costs against the trader: buys above and sells below the reference price;
- baseline is 2 bps one-way; preserve diagnostic 0 and stress 4/6/10 bps cases;
- missing or zero-volume windows create explicit missed fills;
- enforce minimum notional and configured participation/defer policy;
- persist target, order, fill/miss, reference price, cost components, and timestamps;
- ensure increased nonnegative costs cannot improve net equity absent a separately documented behavioral effect.

Never use adjusted prices as simulated executable prices or fall back silently to prior close.
