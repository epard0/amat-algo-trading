# Linear perpetual paper wrapper

`autoresearch.perpetual_sim` provides a local, single-market collateral wallet and net-position ledger. `KalshiStylePaperAPI` accepts a supported subset of Kalshi margin order fields and delegates to this simulator. It has no HTTP transport, signing, account access, or order-submission capability.

The reference is Kalshi's **perpetual/margin API**, not its binary prediction contracts:

- [Perps overview and environments](https://docs.kalshi.com/margin)
- [Margin create-order schema](https://docs.kalshi.com/margin-rest/orders/create-order)
- [Margin market metadata](https://docs.kalshi.com/margin-rest/market/get-market)
- [Historical funding-rate interface](https://docs.kalshi.com/margin-rest/funding/get-historical-funding-rates)

Documentation checked October 19, 2026. The adapter uses `side=bid|ask`, decimal-string `count` and `price`, `client_order_id`, `time_in_force`, `self_trade_prevention_type=taker_at_cross`, and optional `reduce_only`. It supports IOC/FOK marketable limit orders only. Resting orders, order amendments, queue modeling, partial liquidity, subaccounts, and authenticated market configuration are not implemented. Unsupported payload fields fail explicitly. Response snapshots are local simulator schemas, not a promise of full Kalshi API response parity.

## Economics

Let q be signed contracts, m be the explicitly configured quote-currency P&L multiplier per price point per contract, p be mark price, and e be average entry:

- Exposure/notional: `abs(q) * m * p`.
- Unrealized P&L: `q * m * (p - e)`.
- Wallet equity: `cash + unrealized_pnl`.
- Initial margin: `notional / max_leverage`.
- Maintenance requirement: `notional * maintenance_rate`.
- Funding cash flow at a supplied funding event: `-q * m * p * rate`. Positive rates debit longs and credit shorts; negative rates reverse this.
- Fees: filled notional times the supplied fee rate. Quotes supply bid/ask spread; slippage applies adversely on top and rounds adversely to the price tick.

A buy adds positive contracts, closing a short before opening a long if it reverses the position. A sell adds negative contracts, closing a long before opening a short. Weighted entry prices are retained on additions; partial closes realize only the closed portion; reversals start the residual at the new fill price. Collateral is not treated as spending the full derivative notional.

**Multiplier and leverage are different inputs.** For fixed contracts and prices, changing leverage changes the margin requirement, not P&L. Changing m changes notional, price P&L, fees, funding, and margin. The multipliers in the demo are synthetic assumptions, not verified Kalshi contract specifications. Price units and multiplier must be configured together from an instrument's contract economics before any venue-specific claim.

## Example

From `amat/`, run the deterministic demonstration with a fresh output directory:

```sh
.venv/bin/python scripts/demo_perpetual_sim.py --output outputs/NEW-PERP-DEMO
```

Or use the wrapper directly:

```python
from autoresearch.perpetual_sim import (
    ContractSpec, PerpOrder, PerpetualSimulator, KalshiStylePaperAPI,
)

spec = ContractSpec('SIM-BTC-PERP', multiplier='0.001', max_leverage='2')
account = PerpetualSimulator(spec, collateral='10000')
api = KalshiStylePaperAPI(account)
order = PerpOrder('short-1', 'SIM-BTC-PERP', 'ask', '100', '59000')
result = api.create_order(
    order.kalshi_payload(), bid='60000', ask='60010', mark='60005', timestamp_ms=0,
)
account.apply_funding(
    event_id='funding-period-1', rate='0.0001', mark='60000', timestamp_ms=1,
)
print(api.get_positions())
```

The example sells short using a marketable limit; buying with `reduce_only=True` closes the short. Repeated accepted client IDs return the original response, and conflicting reuse fails. Repeated funding IDs with identical inputs do not charge twice. State and deduplication are in memory; persist the ledger externally if needed. IDs are not durable across simulator restarts.

## Margin and event ordering limits

Orders increasing risk must meet initial margin after fees and execution loss. Reduce-only IOC quantities cap at the position; FOK quantities exceeding it fail. Risk-reducing closes are allowed below initial margin while maintenance remains sufficient. At each supplied mark and funding event, breaching maintenance triggers full liquidation with configured adverse slippage and a liquidation fee, then halts the account. Losses are not clipped to collateral: a gap can leave a visible negative-equity deficit.

This is a deliberately simplified one-market model, not Kalshi's portfolio/risk-tier/liquidation system. It has no cross-market offsets, inverse contracts, variation-margin settlement schedule, insurance fund, ADL, or bankruptcy-price matching. Liquidation checks occur only at supplied events; to test intrabar liquidation, supply an explicit adverse mark path. OHLC close-only marks do not prove intrabar survival. Funding rates, timing, and eligible position snapshots are caller-supplied; omitted funding is not implicitly a real zero rate. For a funding tick, apply the funding event to the position held at that tick before subsequent orders; equal-timestamp sequencing is the caller's responsibility.

The risk-matched spot study remains a separate experiment. This wrapper's synthetic long/short examples do not establish a perpetual-futures strategy edge or convert spot history into historical perp fills.
