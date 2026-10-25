"""Contract prices are USD per contract, never unscaled underlying prices."""

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

ZERO = Decimal(0)
HOUR = 3_600_000


def dec(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite():
        raise ValueError("nonfinite decimal")
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value):
    if type(value) is not int or value < 0:
        raise ValueError("expected nonnegative epoch milliseconds")
    return value


def toward_zero(value, step):
    return (dec(value) / step).to_integral_value(rounding=ROUND_DOWN) * step


@dataclass(frozen=True)
class Instrument:
    ticker: str
    contract_size: Decimal
    underlying_multiplier: Decimal
    tick: Decimal
    step: Decimal
    version: int
    status: str

    @classmethod
    def parse(cls, row):
        if row.get("asset_class") != "Crypto":
            raise ValueError("crypto instruments only")
        fractional = row["fractional_trading_enabled"]
        if type(fractional) is not bool:
            raise ValueError("invalid fractional flag")
        result = cls(
            row["ticker"],
            dec(row["contract_size"]),
            dec(row["underlying_multiplier"]),
            dec(row["tick_size"]),
            dec(".01" if fractional else "1"),
            row["market_version"],
            row["status"],
        )
        if min(result.contract_size, result.underlying_multiplier, result.tick) <= 0:
            raise ValueError("invalid instrument economics")
        if type(result.version) is not int or result.version <= 0:
            raise ValueError("market version required")
        return result

    def admit(self):
        if self.status != "active" or self.underlying_multiplier != 1:
            raise ValueError("inactive instrument or unverified underlying normalization")

    def api_price(self, underlying_price):
        self.admit()
        return dec(underlying_price) * self.contract_size

    def identity(self):
        return {k: str(v) for k, v in vars(self).items()}


@dataclass(frozen=True)
class Order:
    client_order_id: str
    ticker: str
    side: str
    count: Decimal
    price: Decimal
    market_version: int
    reduce_only: bool = False
    time_in_force: str = "immediate_or_cancel"

    def __post_init__(self):
        object.__setattr__(self, "count", dec(self.count))
        object.__setattr__(self, "price", dec(self.price))
        if not self.client_order_id or not self.ticker or self.side not in ("bid", "ask"):
            raise ValueError("invalid order identity or side")
        if (
            min(self.count, self.price) <= 0
            or type(self.market_version) is not int
            or self.market_version <= 0
        ):
            raise ValueError("positive count, price and market version required")
        if type(self.reduce_only) is not bool:
            raise ValueError("reduce_only must be boolean")
        if self.time_in_force not in ("immediate_or_cancel", "fill_or_kill"):
            raise ValueError("only bounded IOC/FOK supported")

    def payload(self):
        return {
            "client_order_id": self.client_order_id,
            "ticker": self.ticker,
            "side": self.side,
            "count": str(self.count),
            "price": str(self.price),
            "market_version": self.market_version,
            "reduce_only": self.reduce_only,
            "time_in_force": self.time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }

    def validate(self, instrument):
        instrument.admit()
        if self.ticker != instrument.ticker or self.market_version != instrument.version:
            raise ValueError("instrument version mismatch")
        if self.count % instrument.step or self.price % instrument.tick:
            raise ValueError("quantity or price off tick")


def registry(raw):
    instruments = [Instrument.parse(r) for r in raw["markets"] if r["asset_class"] == "Crypto"]
    result = {i.ticker: i for i in instruments}
    if len(result) != len(instruments):
        raise ValueError("duplicate instrument")
    return result


def book(raw, instrument, *, require_two_sides=True):
    result = {}
    for side in ("bids", "asks"):
        levels = [[dec(p), dec(q)] for p, q in raw[side]]
        if any(p <= 0 or q < 0 or p % instrument.tick or q % instrument.step for p, q in levels):
            raise ValueError("invalid book level")
        if len({p for p, _ in levels}) != len(levels):
            raise ValueError("duplicate book price")
        result[side] = sorted([v for v in levels if v[1]], reverse=side == "bids")
    if require_two_sides and (not result["bids"] or not result["asks"]):
        raise ValueError("two-sided executable book required")
    if result["bids"] and result["asks"] and result["bids"][0][0] >= result["asks"][0][0]:
        raise ValueError("crossed book")
    return result
