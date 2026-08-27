"""Read-only Polymarket adapters.

Nothing in this package can place an order, hold funds, or sign a transaction.
It exists to observe Polymarket's short-interval crypto up/down markets and
record them, because the single biggest constraint on improving the model is
labelled data: the Kalshi ledger holds 17 settled trades, while Polymarket runs
a 5-minute market on four assets continuously, which is roughly 1,150 settled
outcomes per asset per day at zero risk.

There is no wallet, no USDC, no EIP-712 signing, and no CLOB authentication
here on purpose. Adding an order path would make this a second live-money venue
with its own risk gates and circuit breakers; that is a separate decision.
"""

from data_sources.polymarket.client import (
    PolymarketBook,
    PolymarketMarket,
    PolymarketReadOnlyClient,
    parse_updown_slug,
)

__all__ = [
    "PolymarketBook",
    "PolymarketMarket",
    "PolymarketReadOnlyClient",
    "parse_updown_slug",
]
