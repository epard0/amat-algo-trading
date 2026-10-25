"""Local linear-perpetual accounting and a Kalshi-margin-shaped paper interface.

No network transport. One collateral wallet and one net position per simulator.
Multiplier is quote-currency P&L per contract per price point, not leverage.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Any

ZERO = Decimal('0')


def decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError('boolean is not a decimal')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid decimal') from exc
    if not result.is_finite():
        raise ValueError('decimal must be finite')
    return result


@dataclass(frozen=True)
class ContractSpec:
    ticker: str
    multiplier: Decimal = Decimal('0.001')
    quantity_step: Decimal = Decimal('1')
    price_tick: Decimal = Decimal('0.01')
    max_leverage: Decimal = Decimal('2')
    maintenance_rate: Decimal = Decimal('0.05')
    fee_rate: Decimal = Decimal('0.001')
    slippage_rate: Decimal = Decimal('0.0005')
    liquidation_fee_rate: Decimal = Decimal('0.002')

    def __post_init__(self):
        if not isinstance(self.ticker, str) or not self.ticker.strip():
            raise ValueError('ticker is required')
        for field in self.__dataclass_fields__:
            if field != 'ticker':
                object.__setattr__(self, field, decimal(getattr(self, field)))
        if min(self.multiplier, self.quantity_step, self.price_tick) <= 0:
            raise ValueError('multiplier and increments must be positive')
        if self.max_leverage < 1:
            raise ValueError('max leverage must be >= 1')
        if not ZERO < self.maintenance_rate < 1 / self.max_leverage:
            raise ValueError('maintenance must be positive and below initial margin rate')
        if not all(ZERO <= r < 1 for r in (self.fee_rate, self.slippage_rate, self.liquidation_fee_rate)):
            raise ValueError('cost rates must lie in [0,1)')


@dataclass(frozen=True)
class PerpOrder:
    client_order_id: str
    ticker: str
    side: str
    count: Decimal
    price: Decimal
    time_in_force: str = 'immediate_or_cancel'
    reduce_only: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'count', decimal(self.count))
        object.__setattr__(self, 'price', decimal(self.price))
        if (not isinstance(self.client_order_id, str) or not self.client_order_id.strip()
                or not isinstance(self.ticker, str) or not self.ticker.strip()):
            raise ValueError('order id and ticker required')
        if self.side not in ('bid', 'ask'):
            raise ValueError('side must be bid (buy) or ask (sell)')
        if self.count <= 0 or self.price <= 0:
            raise ValueError('quantity and limit price must be positive')
        if self.time_in_force not in ('immediate_or_cancel', 'fill_or_kill'):
            raise ValueError('paper wrapper supports IOC/FOK only; no resting book')
        if type(self.reduce_only) is not bool:
            raise ValueError('reduce_only must be boolean')

    def kalshi_payload(self) -> dict:
        """Documented /margin/orders field subset; no credentials or HTTP request."""
        return dict(client_order_id=self.client_order_id, ticker=self.ticker,
                    side=self.side, count=str(self.count), price=str(self.price),
                    time_in_force=self.time_in_force,
                    self_trade_prevention_type='taker_at_cross', reduce_only=self.reduce_only)


class PerpetualSimulator:
    def __init__(self, spec: ContractSpec, collateral: Any):
        self.spec = spec
        self.initial_collateral = decimal(collateral)
        if self.initial_collateral <= 0:
            raise ValueError('collateral must be positive')
        self.cash = self.initial_collateral
        self.contracts = ZERO
        self.average_entry = ZERO
        self.mark = ZERO
        self.realized_pnl = ZERO
        self.fees = ZERO
        self.funding_pnl = ZERO
        self.halted = False
        self.ledger: list[dict] = []
        self._orders: dict[str, tuple[PerpOrder, dict]] = {}
        self._funding: dict[str, tuple[tuple, Decimal]] = {}
        self._time_ms = -1

    def _time(self, timestamp_ms: int):
        if type(timestamp_ms) is not int or timestamp_ms < self._time_ms or timestamp_ms < 0:
            raise ValueError('timestamps must be nonnegative, monotonic integer milliseconds')
        self._time_ms = timestamp_ms

    @property
    def unrealized_pnl(self):
        return self.contracts * self.spec.multiplier * (self.mark - self.average_entry)

    @property
    def equity(self):
        return self.cash + self.unrealized_pnl

    @property
    def notional(self):
        return abs(self.contracts) * self.spec.multiplier * self.mark

    def position(self) -> dict:
        return {k: str(v) for k, v in dict(ticker=self.spec.ticker,
                contracts=self.contracts, multiplier=self.spec.multiplier,
                average_entry=self.average_entry, mark=self.mark, cash=self.cash,
                equity=self.equity, unrealized_pnl=self.unrealized_pnl,
                realized_pnl=self.realized_pnl, funding_pnl=self.funding_pnl,
                fees=self.fees, notional=self.notional,
                initial_margin=self.notional/self.spec.max_leverage,
                maintenance_margin=self.notional*self.spec.maintenance_rate,
                available_margin=self.equity-self.notional/self.spec.max_leverage,
                deficit=max(ZERO, -self.equity)).items()} | {'halted': self.halted}

    def _trade_result(self, delta, price):
        q = self.contracts; new_q = q + delta; realized = ZERO
        if q == 0 or q * delta > 0:
            average = ((abs(q)*self.average_entry + abs(delta)*price) / abs(new_q))
        else:
            closed = min(abs(q), abs(delta))
            realized = closed * self.spec.multiplier * (price-self.average_entry) * (1 if q>0 else -1)
            average = ZERO if new_q == 0 else (self.average_entry if q*new_q>0 else price)
        return new_q, average, realized

    def _fill(self, delta, price, fee_rate, reason):
        new_q, average, realized = self._trade_result(delta, price)
        fee = abs(delta) * self.spec.multiplier * price * fee_rate
        self.cash += realized - fee
        self.realized_pnl += realized; self.fees += fee
        self.contracts = new_q; self.average_entry = average
        self.ledger.append(dict(event=reason, timestamp_ms=self._time_ms,
                                contracts=str(delta), fill_price=str(price),
                                realized_pnl=str(realized), fee=str(fee), equity=str(self.equity)))
        return fee

    def _execution_price(self, reference, direction):
        raw = reference * (1 + direction*self.spec.slippage_rate)
        rounding = ROUND_CEILING if direction > 0 else ROUND_FLOOR
        return (raw/self.spec.price_tick).to_integral_value(rounding=rounding)*self.spec.price_tick

    def _liquidate_if_needed(self):
        # Simplified full liquidation at supplied mark with adverse slippage.
        # No floor at zero: gap losses remain a visible deficit.
        if self.contracts and self.equity <= self.notional * self.spec.maintenance_rate:
            direction = -1 if self.contracts > 0 else 1
            price = self._execution_price(self.mark, direction)
            self._fill(-self.contracts, price,
                       self.spec.fee_rate+self.spec.liquidation_fee_rate, 'liquidation')
            self.halted = True

    def mark_to_market(self, price: Any, timestamp_ms: int, *, snapshot: bool = True):
        price = decimal(price)
        if price <= 0:
            raise ValueError('mark must be positive')
        self._time(timestamp_ms); self.mark = price
        self._liquidate_if_needed()
        return self.position() if snapshot else None

    def submit(self, order: PerpOrder, *, bid: Any, ask: Any, mark: Any, timestamp_ms: int):
        if order.client_order_id in self._orders:
            old, result = self._orders[order.client_order_id]
            if order != old:
                raise ValueError('client_order_id reused for different order')
            return dict(result)
        if order.ticker != self.spec.ticker:
            raise ValueError('wrong contract')
        if order.count % self.spec.quantity_step or order.price % self.spec.price_tick:
            raise ValueError('quantity/limit violates contract increments')
        bid, ask, mark = decimal(bid), decimal(ask), decimal(mark)
        if bid <= 0 or ask < bid or mark <= 0:
            raise ValueError('invalid bid/ask/mark')
        self.mark_to_market(mark, timestamp_ms, snapshot=False)
        if self.halted:
            raise ValueError('account halted after liquidation')
        sign = 1 if order.side == 'bid' else -1
        quantity = order.count
        if order.reduce_only:
            if self.contracts * sign >= 0:
                raise ValueError('reduce_only order must oppose an existing position')
            if quantity > abs(self.contracts) and order.time_in_force == 'fill_or_kill':
                raise ValueError('FOK reduce_only quantity exceeds position')
            quantity = min(quantity, abs(self.contracts))
        fill_price = self._execution_price(ask if sign>0 else bid, sign)
        crosses = fill_price <= order.price if sign>0 else fill_price >= order.price
        result = dict(order_id=f'sim-{order.client_order_id}', client_order_id=order.client_order_id,
                      fill_count='0', remaining_count='0', canceled_count=str(order.count), status='canceled',
                      average_fill_price=None, fee_paid='0', execution_authority='simulation_only')
        if crosses:
            delta = sign * quantity
            new_q, average, realized = self._trade_result(delta, fill_price)
            fee = quantity * self.spec.multiplier * fill_price * self.spec.fee_rate
            equity_after = self.cash + realized - fee + new_q*self.spec.multiplier*(mark-average)
            risk_reducing = self.contracts*new_q >= 0 and abs(new_q) < abs(self.contracts)
            required = abs(new_q) * self.spec.multiplier * mark / self.spec.max_leverage
            if not risk_reducing and equity_after < required:
                raise ValueError('insufficient initial margin including fees/slippage')
            paid = self._fill(delta, fill_price, self.spec.fee_rate, 'fill')
            self._liquidate_if_needed()
            result.update(fill_count=str(quantity), canceled_count=str(order.count-quantity), status='executed',
                          average_fill_price=str(fill_price), fee_paid=str(paid))
        self._orders[order.client_order_id] = order, dict(result)
        self.ledger.append(dict(event='order', timestamp_ms=timestamp_ms,
                                request=order.kalshi_payload(), response=dict(result)))
        return dict(result)

    def apply_funding(self, *, event_id: str, rate: Any, mark: Any, timestamp_ms: int):
        rate, mark = decimal(rate), decimal(mark)
        if not isinstance(event_id, str) or not event_id or abs(rate) >= 1 or mark <= 0:
            raise ValueError('funding requires id, positive mark, and fractional rate in (-1,1)')
        signature = rate, mark, timestamp_ms
        if event_id in self._funding:
            old, payment = self._funding[event_id]
            if signature != old:
                raise ValueError('funding id reused with different inputs')
            return payment
        self.mark_to_market(mark, timestamp_ms, snapshot=False)
        payment = -self.contracts * self.spec.multiplier * mark * rate
        self.cash += payment; self.funding_pnl += payment
        self._funding[event_id] = signature, payment
        self.ledger.append(dict(event='funding', event_id=event_id, timestamp_ms=timestamp_ms,
                                rate=str(rate), payment=str(payment)))
        self._liquidate_if_needed()
        return payment


class KalshiStylePaperAPI:
    """Local subset of margin create-order/positions conventions, without HTTP.

    Contract/tick/fee/margin configuration is supplied by the caller. This class
    does not infer live Kalshi contract economics or authenticate to an account.
    """
    def __init__(self, simulator: PerpetualSimulator):
        self.simulator = simulator

    def create_order(self, payload: dict, *, bid: Any, ask: Any, mark: Any, timestamp_ms: int):
        required = {'client_order_id', 'ticker', 'side', 'count', 'price',
                    'time_in_force', 'self_trade_prevention_type'}
        allowed = required | {'reduce_only'}
        if set(payload) - allowed or required - set(payload):
            raise ValueError('unsupported or missing margin order fields')
        if payload['self_trade_prevention_type'] != 'taker_at_cross':
            raise ValueError('only taker_at_cross is supported by paper adapter')
        order = PerpOrder(**{k: v for k, v in payload.items() if k != 'self_trade_prevention_type'})
        return self.simulator.submit(order, bid=bid, ask=ask, mark=mark, timestamp_ms=timestamp_ms)

    def get_positions(self):
        return {'positions': [self.simulator.position()], 'execution_authority': 'simulation_only'}
