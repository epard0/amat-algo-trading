# QQQ total-return objective reference

`scripts/run_qqq_total_return_reference.py` adds a distinct fractional investor
reference alongside the executable native-cap QQQ allocation benchmark. It has
no promotion authority and is never an eligible AMAT candidate.

The initial investment is $100,000 on January 3, 2017. Fractional units are
`100000 / (observed initial opening VWAP × (1 + one-way cost))`. Their initial
closing value is the units multiplied by that day's raw closing price. Later
wealth equals this initial closing value multiplied by the provider's adjusted
close on the later date divided by its adjusted close on January 3. Each cost
scenario uses 2, 6 or 10 basis points. There is no terminal sale or assumed
liquidation fee.

This reference does not enforce AMAT's entry-volume cap, allocation caps,
whole-share rule or rebalance band. It approximates fractional reinvestment
through provider-adjusted price ratios; it does not claim to replay actual
dividend payable dates or dividend-reinvestment prices. Corporate-action cash
is not separately credited, which would count distributions twice. The observed
initial opening window and positive volume are required; there is no fallback
to a closing price if that window is missing.

The adjusted-price approach avoids an arbitrary cash drag from small dividends
remaining inside a native rebalance band. It therefore expresses the user's
QQQ total-return ambition separately from the more restrictive executable
native comparison. Both belong in the dashboard with their definitions shown.
AMAT's net return can be compared with both, while its execution and accounting
promotion checks continue to use matched native evidence.

Registration references the existing sealed native foundation protocol and
retains its source, data partitions and exact session calendar. The new driver
itself is sealed. Final outputs carry their own study, run, protocol, foundation
and session identities. Measurements remain exposed to previously researched
history and are not untouched out-of-sample evidence.

Seven offline tests pass, covering the three entry-cost scenarios, initial raw
VWAP usage, missing-window rejection, no distribution double count, source and
partition identity checks, and invariance when a future corporate-action
adjustment multiplies all earlier adjusted prices by the same factor. That
common factor cancels in the ratio; the test does not establish that every
provider adjustment or data revision is point-in-time historical data.
