"""The one-minute candle shared by the price source, the regime classifier,
and the probability model.

It lives in its own module so ``market_regime`` and ``crypto_15min_predictor``
can both use it without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class PriceCandle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
