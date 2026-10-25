# Executable QQQ foundation contract, October 23, 2026

The benchmark driver is `scripts/run_qqq_foundation.py`. It registers immutable
inputs before measurement and cannot promote a strategy or contact a broker.
No benchmark performance number is asserted by this contract; results come only
from the separately sealed measurements.

## Comparable portfolio definitions

The control reproduces the currently selected AMAT profile with its native
runner. It retains full 2016 indicator history, starts flat with $100,000 on
January 3, 2017, and ends marked at the October 12, 2026 close. Prices, corporate
actions and actual opening-window volume come from the verified
`alpaca-sip-core-2026-10-13-verified` snapshot. Each arm is measured at 2, 6 and
10 basis points per side. Cash earns zero.

`qqq_native` is a passive 100% QQQ target using the same native instruction
builder, simulated execution model and session accountant as AMAT. It is an
investment reference, deliberately outside AMAT's symbol, group and portfolio
risk caps. The passive target has no tactical signal or stop-loss rule. Decisions
use settled account state and the previous completed raw close. Fills use the
next observed 09:30–09:35 New York VWAP, whole-share purchases, the native 1%
opening-volume entry cap, original minimum trade notional and rebalance band.

This is an executable allocation reference, rather than a frictionless
adjusted-close return series. Initial funding includes transaction costs in the
execution model. A purchase sized at the preceding close can miss in full when
the next opening price plus fees exceeds available cash. It retries from the
next completed close while the passive target remains underfunded. The driver
does not silently clip these cash failures, synthesize liquidity, trade at an
unobserved price, or count an unfilled target as an investment.

`qqq_investor_uncapped` changes only the entry-volume cap. It provides a clearly
separate investor reference, not a matched native-cap benchmark and not an
eligible strategy candidate.

## Distribution and split treatment

Raw closes value positions. Corporate-action records credit cash distributions
once, using opening shares, on the ex-date under the current native accounting
convention. Shares and pending share quantities are restated across declared
splits. Adjusted prices are never used again to value that same dividend.

Distribution cash remains in the account. It may buy more QQQ at a later native
rebalance when the original band and minimum notional permit. This is neither a
fractional same-day dividend-reinvestment plan nor a claim about payable-date
buying power. Newly credited ex-date cash does not fund that day's opening
purchase; this matches native settlement. The simulation ends with marked
holdings and cash, without a fabricated liquidation or liquidation fee.

## Accounting and diagnostics

Every result is independently reconstructed from its native fills and corporate
actions. Both cash and NAV must equal the native session record exactly.
Per-asset market P&L, distributions, fees and net P&L must explain every session's
NAV change with no residual beyond one micro-dollar. Daily asset quantities and
market values are retained. This enables weak-month attribution to actual
holdings, losses and costs instead of inferring exposures from annual returns.

Metrics include full history, continuous-account 2020-onward returns, annual
slices, monthly returns, 2018 Q4/COVID/2022 stress windows, drawdown recovery,
turnover inputs, fees, fills, missed outcomes and exact decimal attribution.
Annual slices are chronological evaluation of one continuous account; they are
not independent annually reset accounts or untouched holdouts.

Registration seals the exact config, all current Python source hashes, driver
hash, snapshot partition identities, execution calendar and economic conventions.
Finalized measurements carry run, study, profile, protocol, effective-config and
session identities. A source or data change requires another registration.

## Verified fixtures and remaining boundary

Six offline fixtures validate dividend/split wealth preservation, single-counted
distribution attribution, native cash-gap rejection and fee accounting, observed
opening-volume caps versus the uncapped reference, missing-window misses,
rejection of tampered NAV, and future-window causality. The targeted test file is
`tests/unit/test_qqq_foundation.py`; the driver and tests pass Ruff.

These checks establish research reference accounting. They do not establish
that a broker's staged limit orders will receive the same fills as historical
opening-window VWAP, or that the live account has matching buying power,
settlement timing, spreads, borrow availability or intraday exit opportunities.
Such execution differences remain explicit promotion constraints.
