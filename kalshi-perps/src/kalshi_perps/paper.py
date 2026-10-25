"""Persistent portfolio paper broker with consumed depth and explicit funding events."""

import json
from copy import deepcopy

from .domain import ZERO, book, dec, digest, timestamp, toward_zero
from .leverage import margin_required
from .storage import Store


def position(state, ticker):
    return state["positions"].setdefault(ticker, {"count": "0", "entry": "0", "mark": "0"})


def equity(state):
    return dec(state["cash"]) + sum(
        (dec(p["count"]) * (dec(p["mark"]) - dec(p["entry"])) for p in state["positions"].values()),
        ZERO,
    )


def notional(state):
    return sum((abs(dec(p["count"])) * dec(p["mark"]) for p in state["positions"].values()), ZERO)


def fill(state, ticker, signed_count, price, fee_rate):
    p = position(state, ticker)
    old, entry = dec(p["count"]), dec(p["entry"])
    new = old + signed_count
    realized = ZERO
    if old * signed_count < 0:
        realized = min(abs(old), abs(signed_count)) * (price - entry) * (1 if old > 0 else -1)
    if not new:
        average = ZERO
    elif not old or old * new < 0:
        average = price
    elif old * signed_count > 0:
        average = (abs(old) * entry + abs(signed_count) * price) / abs(new)
    else:
        average = entry
    fee = abs(signed_count) * price * fee_rate
    state["cash"] = str(dec(state["cash"]) + realized - fee)
    state["fees"] = str(dec(state["fees"]) + fee)
    state["realized_pnl"] = str(dec(state["realized_pnl"]) + realized)
    p.update(count=str(new), entry=str(average))
    return {
        "ticker": ticker,
        "count": str(signed_count),
        "price": str(price),
        "fee": str(fee),
        "realized_pnl": str(realized),
    }


class PaperBroker:
    def __init__(
        self,
        path,
        instruments,
        *,
        account="paper",
        policy_hash="manual",
        collateral="10000",
        fee_rate=".0004",
        max_leverage="2",
        maintenance_rate=".05",
        liquidation_fee=".005",
        max_spread_bps="100",
        leverage_mode="fixed",
    ):
        self.instruments = instruments
        self.leverage_mode = leverage_mode
        if leverage_mode not in ("fixed", "venue_max"):
            raise ValueError("unknown paper leverage mode")
        self.fee_rate, self.max_leverage = dec(fee_rate), dec(max_leverage)
        self.maintenance_rate, self.liquidation_fee = dec(maintenance_rate), dec(liquidation_fee)
        self.max_spread_bps = dec(max_spread_bps)
        if dec(collateral) <= 0 or self.max_leverage < 1 or self.max_spread_bps <= 0:
            raise ValueError("invalid paper capital or limits")
        if not ZERO <= self.fee_rate < 1 or not ZERO <= self.liquidation_fee < 1:
            raise ValueError("invalid paper fees")
        if not ZERO < self.maintenance_rate < 1 / self.max_leverage:
            raise ValueError("maintenance must be below initial margin rate")
        identity = {
            "mode": "paper",
            "account": account,
            "policy": policy_hash,
            "instruments": {k: v.identity() for k, v in sorted(instruments.items())},
            "collateral": str(dec(collateral)),
            "fee_rate": str(self.fee_rate),
            "max_leverage": str(self.max_leverage),
            "maintenance_rate": str(self.maintenance_rate),
            "liquidation_fee": str(self.liquidation_fee),
            "max_spread_bps": str(self.max_spread_bps),
        }
        if leverage_mode != "fixed":
            identity["leverage_mode"] = leverage_mode
        self.store = Store(
            path,
            identity,
            {
                "cash": str(dec(collateral)),
                "initial": str(dec(collateral)),
                "fees": "0",
                "funding": "0",
                "realized_pnl": "0",
                "positions": {},
                "books": {},
                "orders": {},
                "funding_ids": {},
                "timestamp_ms": -1,
                "halted": False,
                "peak": str(dec(collateral)),
                "max_drawdown": "0",
            },
        )

    def snapshot(self, event_id, timestamp_ms, quotes, funding=(), settlements=()):
        timestamp(timestamp_ms)
        payload = {
            "timestamp_ms": timestamp_ms,
            "quotes": quotes,
            "funding": list(funding),
            "settlements": list(settlements),
        }

        def transition(state):
            if timestamp_ms <= state["timestamp_ms"]:
                raise ValueError("snapshot time must increase")
            held = {t for t, p in state["positions"].items() if dec(p["count"])}
            if not held <= quotes.keys():
                raise ValueError("missing held-instrument mark")
            books = {}
            if self.leverage_mode == "venue_max":
                state["leverage"] = {t: q["leverage"] for t, q in quotes.items()}
            for ticker, quote in quotes.items():
                instrument = self.instruments[ticker]
                instrument.admit()
                mark = dec(quote["mark"])
                if mark <= 0:
                    raise ValueError("invalid mark")
                levels = book(quote, instrument)
                books[ticker] = {
                    side: [[str(p), str(q)] for p, q in values] for side, values in levels.items()
                }
                position(state, ticker)["mark"] = str(mark)
            state["books"] = books
            state["timestamp_ms"] = timestamp_ms
            for event in funding:
                key = event["event_id"]
                if event["timestamp_ms"] != timestamp_ms:
                    raise ValueError("funding must be supplied at its actual timestamp")
                previous = state["funding_ids"].get(key)
                if previous:
                    if previous != digest(event):
                        raise ValueError("funding identity conflict")
                    continue
                rate, mark = dec(event["rate"]), dec(event["mark"])
                if abs(rate) >= 1 or mark <= 0:
                    raise ValueError("invalid percentage funding")
                p = position(state, event["ticker"])
                payment = -dec(p["count"]) * mark * rate
                state["cash"] = str(dec(state["cash"]) + payment)
                state["funding"] = str(dec(state["funding"]) + payment)
                state["funding_ids"][key] = digest(event)
            for ticker in settlements:
                p = position(state, ticker)
                payment = dec(p["count"]) * (dec(p["mark"]) - dec(p["entry"]))
                state["cash"] = str(dec(state["cash"]) + payment)
                state["realized_pnl"] = str(dec(state["realized_pnl"]) + payment)
                p["entry"] = p["mark"] if dec(p["count"]) else "0"
            liquidations = []
            maintenance = (
                margin_required(state, initial=False)
                if self.leverage_mode == "venue_max"
                else notional(state) * self.maintenance_rate
            )
            if notional(state) and equity(state) <= maintenance:
                # Explicit approximation: full close at adverse best quote, regardless of available depth.
                for ticker, p in state["positions"].items():
                    q = dec(p["count"])
                    if q:
                        side = "bids" if q > 0 else "asks"
                        price = dec(state["books"][ticker][side][0][0])
                        liquidations.append(
                            fill(state, ticker, -q, price, self.fee_rate + self.liquidation_fee)
                        )
                state["halted"] = True
            self._track(state)
            return {
                "equity": str(equity(state)),
                "liquidations": liquidations,
                "halted": state["halted"],
            }

        return self.store.event("snapshot:" + event_id, payload, transition)

    def submit(self, order, *, timestamp_ms, reason="strategy", action_id=None):
        order.validate(self.instruments[order.ticker])
        payload = {"order": order.payload(), "timestamp_ms": timestamp_ms}
        if reason != "strategy":
            payload["reason"] = reason
        if action_id is not None:
            payload["action_id"] = action_id

        def transition(state):
            if timestamp_ms != state["timestamp_ms"]:
                raise ValueError("order requires current snapshot timestamp")
            if state["halted"] and not order.reduce_only:
                raise ValueError("paper account halted")
            p = position(state, order.ticker)
            current = dec(p["count"])
            direction = 1 if order.side == "bid" else -1
            quantity = order.count
            if order.reduce_only:
                if current * direction >= 0:
                    raise ValueError("reduce-only would not reduce")
                if order.time_in_force == "fill_or_kill" and quantity > abs(current):
                    return {"status": "canceled", "fill_count": "0", "fills": []}
                quantity = min(quantity, abs(current))
            levels = book(
                state["books"][order.ticker],
                self.instruments[order.ticker],
                require_two_sides=False,
            )
            if not levels["bids"] or not levels["asks"]:
                return {"status": "canceled", "fill_count": "0", "fills": []}
            bid, ask = levels["bids"][0][0], levels["asks"][0][0]
            if (
                not order.reduce_only
                and (ask - bid) / ((ask + bid) / 2) * 10000 > self.max_spread_bps
            ):
                raise ValueError("spread limit exceeded")
            side = "asks" if direction > 0 else "bids"
            eligible = [
                (price, size)
                for price, size in levels[side]
                if (price <= order.price if direction > 0 else price >= order.price)
            ]
            available = sum((size for _, size in eligible), ZERO)
            if order.time_in_force == "fill_or_kill" and available < quantity:
                return {"status": "canceled", "fill_count": "0", "fills": []}
            trial = deepcopy(state)
            remaining, fills = quantity, []
            for price, size in eligible:
                count = min(size, remaining)
                if not count:
                    break
                fills.append(fill(trial, order.ticker, direction * count, price, self.fee_rate))
                remaining -= count
                # Consume liquidity so another order cannot reuse this snapshot depth.
                for level in trial["books"][order.ticker][side]:
                    if dec(level[0]) == price:
                        level[1] = str(dec(level[1]) - count)
            filled_delta = direction * (quantity - remaining)
            increases = (
                not order.reduce_only
                and bool(filled_delta)
                and (current * filled_delta >= 0 or abs(filled_delta) > abs(current))
            )
            initial = (
                margin_required(trial, initial=True)
                if self.leverage_mode == "venue_max"
                else notional(trial) / self.max_leverage
            )
            if increases and equity(trial) < initial:
                raise ValueError("initial margin insufficient")
            state.clear()
            state.update(trial)
            self._track(state)
            filled = quantity - remaining
            result = {
                "status": "filled" if filled == order.count else "canceled",
                "fill_count": str(filled),
                "canceled_count": str(order.count - filled),
                "fills": fills,
            }
            state["orders"][order.client_order_id] = result
            return result

        return self.store.event("order:" + order.client_order_id, payload, transition)

    def apply_historical_funding(self, event):
        """Backfill a published native rate using contracts held at its timestamp."""
        ticker = event["ticker"]
        self.instruments[ticker].admit()
        when = timestamp(event["timestamp_ms"])
        rate, mark = dec(event["rate"]), dec(event["mark"])
        if abs(rate) >= 1 or mark <= 0:
            raise ValueError("invalid native funding economics")

        def transition(state):
            if state["timestamp_ms"] < when:
                raise ValueError("funding event has not occurred in this paper account")
            previous = state["funding_ids"].get(event["event_id"])
            if previous:
                if previous != digest(event):
                    raise ValueError("funding identity conflict")
                return {"payment": "0", "duplicate": True}
            held = ZERO
            for key, body, result in self.store.db.execute("SELECT id,body,result FROM events"):
                if not key.startswith(("order:", "snapshot:")):
                    continue
                payload, outcome = json.loads(body), json.loads(result)
                if payload["timestamp_ms"] >= when:
                    continue
                fills = (
                    outcome.get("fills", [])
                    if key.startswith("order:")
                    else outcome.get("liquidations", [])
                )
                for item in fills:
                    if item.get("ticker", payload.get("order", {}).get("ticker")) == ticker:
                        held += dec(item["count"])
            payment = -held * mark * rate
            state["cash"] = str(dec(state["cash"]) + payment)
            state["funding"] = str(dec(state["funding"]) + payment)
            state["funding_ids"][event["event_id"]] = digest(event)
            self._track(state)
            return {"payment": str(payment), "held_contracts": str(held), "late_publication": True}

        return self.store.event("funding:" + event["event_id"], event, transition)

    def target_order(self, ticker, target_weight, *, client_order_id, max_order_notional="1000000"):
        state = self.store.state()
        instrument = self.instruments[ticker]
        p = position(state, ticker)
        desired = toward_zero(
            dec(target_weight) * max(ZERO, equity(state)) / dec(p["mark"]), instrument.step
        )
        current = dec(p["count"])
        # Reverse only after the current side has actually closed.
        delta = -current if current * desired < 0 else desired - current
        if not delta:
            return None
        side = "bid" if delta > 0 else "ask"
        levels = state["books"][ticker]["asks" if delta > 0 else "bids"]
        prices = [dec(price) for price, count in levels if dec(count) > 0]
        if not prices:
            return None
        limit = prices[0]
        quantity = min(abs(delta), toward_zero(dec(max_order_notional) / limit, instrument.step))
        if not quantity:
            return None
        from .domain import Order

        return Order(
            client_order_id,
            ticker,
            side,
            quantity,
            limit,
            instrument.version,
            reduce_only=current * delta < 0 and abs(delta) <= abs(current),
        )

    @staticmethod
    def _track(state):
        value = equity(state)
        peak = max(dec(state["peak"]), value)
        state["peak"] = str(peak)
        state["max_drawdown"] = str(min(dec(state["max_drawdown"]), value / peak - 1))

    def report(self):
        state = self.store.state()
        return dict(
            mode="paper",
            **{
                k: state[k]
                for k in (
                    "cash",
                    "fees",
                    "funding",
                    "realized_pnl",
                    "positions",
                    "halted",
                    "max_drawdown",
                )
            },
            equity=str(equity(state)),
            pnl=str(equity(state) - dec(state["initial"])),
            gross_notional=str(notional(state)),
            margin_model=(
                "published side/size margin estimates with initial-margin multiplier; no portfolio offsets"
                if self.leverage_mode == "venue_max"
                else "fixed-rate portfolio approximation; liquidation uses best quote without depth cap"
            ),
        )
