"""One execution flow for recordings and REST-polling forward operation."""

import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path

from .api import create_strategy, describe_strategy
from .domain import HOUR, ZERO, Order, book, canonical, dec, digest, registry, toward_zero
from .history import SignalHistory
from .leverage import maximum_weights
from .marketdata import Feed
from .paper import PaperBroker, equity
from .strategy import HistoryUnavailable
from .venue import KalshiBroker, Transport


@contextmanager
def lease(database):
    path = Path(str(database) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("a runner already owns this account database") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def build(config, *, with_strategy=True):
    profile, symbols, policy_id = describe_strategy(config)
    if (
        profile
        and profile.evidence.get("execution_status") == "paper_authorized"
        and config.mode != "paper"
    ):
        raise ValueError("ADX authorization is paper only")
    if profile and profile.evidence.get("execution_status") == "blocked":
        raise ValueError("default ADX research candidate is gated: no robust walk-forward winner")
    available = registry(json.loads(config.resolve(config.raw["instruments"]).read_text()))
    instruments = {t: available[t] for t in symbols}
    for instrument in instruments.values():
        instrument.admit()
    strategy = create_strategy(config) if with_strategy else None
    identity = digest({"config": config.identity, "strategy": policy_id})
    if config.mode == "paper":
        broker = PaperBroker(
            config.database,
            instruments,
            account=config.raw["account"],
            policy_hash=identity,
            **config.raw["paper"],
            max_spread_bps=config.raw["risk"]["max_spread_bps"],
        )
    else:
        transport = Transport.configured(config, writable=True)
        broker = KalshiBroker(
            config.database,
            instruments,
            transport,
            mode=config.mode,
            account=config.raw["account"],
            policy_hash=identity,
            max_gross=config.raw["risk"]["max_gross"],
            max_order_notional=config.raw["risk"]["max_order_notional"],
            max_loss_fraction=config.raw["risk"]["max_loss_fraction"],
            subaccount=config.raw["venue"].get("subaccount", 0),
            expected_key_id=config.raw["venue"].get("expected_key_id"),
        )
    return strategy, broker


class Runner:
    def __init__(self, config, strategy, broker):
        self.config, self.strategy, self.broker = config, strategy, broker
        self.risk = config.raw["risk"]
        self.symbols = tuple(broker.instruments)

    def process(self, event, candles):
        if self.config.mode != "paper":
            return self._process(event, candles)
        # Snapshot, orders, accounting, and cadence commit together. A process crash
        # cannot leave a half-applied paper step that changes a retry's intent.
        key = "step:" + event["event_id"]
        payload = {"event": event, "candles_hash": digest(candles)}
        with self.broker.store.transaction():
            old = self.broker.store.db.execute(
                "SELECT hash,result FROM events WHERE id=?", (key,)
            ).fetchone()
            if old:
                if old[0] != digest(payload):
                    raise ValueError("paper step identity conflict")
                return json.loads(old[1])
            result = self._process(event, candles)
            self.broker.store.event(key, payload, lambda state: result)
            return result

    def _process(self, event, candles):
        now = event["timestamp_ms"]
        if self.config.mode == "paper":
            self.broker.snapshot(
                event["event_id"],
                now,
                event["quotes"],
                event.get("funding", []),
                event.get("settlements", []),
            )
            state = self.broker.store.state()
            if equity(state) <= dec(state["initial"]) * (1 - dec(self.risk["max_loss_fraction"])):
                with self.broker.store.transaction():
                    state["halted"] = True
                    self.broker.store.save(state)
        else:
            self.broker.reconcile()
        if self.broker.store.state()["halted"]:
            return {"state": "halted", "report": self.broker.report()}
        boundary = now // HOUR * HOUR
        history = {
            t: [r for r in candles[t] if r["timestamp_ms"] + HOUR <= boundary] for t in self.symbols
        }
        if not all(history.values()):
            return {"state": "warming_up", "report": self.broker.report()}
        try:
            decision = self.strategy.decide(history, boundary)
        except HistoryUnavailable as exc:
            return {"state": "warming_up", "reason": str(exc), "report": self.broker.report()}
        self.broker.store.event(
            "decision:" + decision["input_hash"], decision, lambda state: decision
        )
        if not decision["ready"]:
            return {"state": "warming_up", "decision": decision, "report": self.broker.report()}
        if decision.get("signed_exit_policy") is not None:
            from .shadow_forward import execute_targets

            return execute_targets(self, event, decision)
        if decision.get("exit_policy") is not None:
            from .adx_forward import execute_targets

            return execute_targets(self, event, decision)
        weights = {t: dec(w) for t, w in decision["weights"].items()}
        leverage_limits = None
        dynamic_leverage = (
            self.config.mode == "paper"
            and self.config.raw.get("paper", {}).get("leverage_mode") == "venue_max"
        )
        if dynamic_leverage:
            profile = self.strategy.profile
            allocations = {leg["ticker"]: leg["allocation"] for leg in profile.legs}
            weights, leverage_limits = maximum_weights(
                weights, allocations, event["quotes"], equity(self.broker.store.state())
            )
        gross = sum(map(abs, weights.values()), ZERO)
        cap = dec(self.risk["max_gross"])
        if gross > cap:
            weights = {t: w * cap / gross for t, w in weights.items()}
        results = []
        # Reductions precede additions. Match the reference sign-change / UTC-midnight cadence.
        for reducing in (True, False):
            for ticker in self.symbols:
                if self.config.mode != "paper" and ticker not in self.config.raw["venue"].get(
                    "trade_tickers", self.symbols
                ):
                    continue
                quote_data = event["quotes"][ticker]
                levels = book(quote_data, self.broker.instruments[ticker])
                bid, ask = levels["bids"][0][0], levels["asks"][0][0]
                if (ask - bid) / ((ask + bid) / 2) * 10000 > dec(self.risk["max_spread_bps"]):
                    continue
                state = self.broker.store.state()
                if self.config.mode == "paper":
                    current = dec(state["positions"].get(ticker, {}).get("count", "0"))
                    value = equity(state)
                else:
                    current = next(
                        (
                            dec(p["position"])
                            for p in state["venue"]["positions"]
                            if p["market_ticker"] == ticker
                        ),
                        ZERO,
                    )
                    value = dec(state["venue"]["balance"]["account_equity"])
                meta = state.get("strategy_execution", {}).get(ticker, {})
                sign = 1 if weights[ticker] > 0 else -1 if weights[ticker] < 0 else 0
                day = boundary // (24 * HOUR)
                if not dynamic_leverage and meta.get("sign") == sign and meta.get("day") == day:
                    continue
                instrument = self.broker.instruments[ticker]
                target = toward_zero(
                    weights[ticker] * max(ZERO, value) / dec(quote_data["mark"]), instrument.step
                )
                delta = -current if current * target < 0 else target - current
                if not delta:
                    self._executed(ticker, sign, day)
                    continue
                is_reduction = current * delta < 0 and abs(delta) <= abs(current)
                if is_reduction != reducing:
                    continue
                limit = ask if delta > 0 else bid
                count = min(
                    abs(delta),
                    toward_zero(dec(self.risk["max_order_notional"]) / limit, instrument.step),
                )
                if not count:
                    continue
                order = Order(
                    digest(
                        {
                            "policy": self.strategy.identity,
                            "event": event["event_id"],
                            "ticker": ticker,
                            "reducing": reducing,
                        }
                    )[:32],
                    ticker,
                    "bid" if delta > 0 else "ask",
                    count,
                    limit,
                    instrument.version,
                    reduce_only=is_reduction,
                )
                try:
                    if self.config.mode == "paper":
                        result = self.broker.submit(
                            order,
                            timestamp_ms=now,
                            reason=decision.get("reasons", {}).get(ticker, "strategy"),
                        )
                    else:
                        result = self.broker.submit(order, timestamp_ms=now)
                except ValueError as exc:
                    if self.config.mode != "paper" or "initial margin" not in str(exc):
                        raise
                    result = {"status": "rejected", "reason": "initial_margin"}
                results.append({"order": order.payload(), "result": result})
                if self.config.mode != "paper":
                    self.broker.reconcile()
                # A partial fill is not a completed rebalance: retry residual at a fresh book.
                after = self.broker.store.state()
                actual = (
                    dec(after["positions"].get(ticker, {}).get("count", "0"))
                    if self.config.mode == "paper"
                    else next(
                        (
                            dec(p["position"])
                            for p in after["venue"]["positions"]
                            if p["market_ticker"] == ticker
                        ),
                        ZERO,
                    )
                )
                if abs(actual - target) < instrument.step:
                    self._executed(ticker, sign, day)
        return {
            "state": "running",
            "decision": decision,
            "admitted_weights": {t: str(w) for t, w in weights.items()},
            "leverage_limits": leverage_limits,
            "orders": results,
            "report": self.broker.report(),
        }

    def _executed(self, ticker, sign, day):
        with self.broker.store.transaction():
            state = self.broker.store.state()
            state.setdefault("strategy_execution", {})[ticker] = {"sign": sign, "day": day}
            self.broker.store.save(state)

    def flatten(self, event_id, quotes=None, now=None):
        if self.config.mode != "paper":
            return self._flatten_once(event_id, quotes, now)
        results = []
        # Respect each order cap while closing all available local depth. Stop
        # when flat or no further fill is possible; never replenish a paper book.
        for attempt in range(100):
            before = {t: p["count"] for t, p in self.broker.store.state()["positions"].items()}
            if not any(dec(q) for q in before.values()):
                break
            step = self._flatten_once(f"{event_id}:{attempt}", quotes, now)
            results.extend(step["results"])
            after = {t: p["count"] for t, p in self.broker.store.state()["positions"].items()}
            if before == after:
                break
        return {
            "results": results,
            "report": self.broker.report(),
            "note": "Inspect residual positions; unavailable depth can prevent a full close.",
        }

    def _flatten_once(self, event_id, quotes=None, now=None):
        if self.config.mode != "paper":
            self.broker.cancel_all()
        results = []
        for ticker in self.symbols:
            state = self.broker.store.state()
            if self.config.mode == "paper":
                count = dec(state["positions"].get(ticker, {}).get("count", "0"))
                now = state["timestamp_ms"]
                levels = state["books"].get(ticker)
            else:
                self.broker.reconcile()
                state = self.broker.store.state()
                count = next(
                    (
                        dec(p["position"])
                        for p in state["venue"]["positions"]
                        if p["market_ticker"] == ticker
                    ),
                    ZERO,
                )
                levels = quotes[ticker]
            if not count:
                continue
            side = "bids" if count > 0 else "asks"
            available = [(dec(p), dec(q)) for p, q in levels[side] if dec(q) > 0]
            if not available:
                continue
            price = available[0][0]
            instrument = self.broker.instruments[ticker]
            quantity = min(
                abs(count),
                toward_zero(dec(self.risk["max_order_notional"]) / price, instrument.step),
            )
            if quantity:
                order = Order(
                    digest({"stop": event_id, "ticker": ticker})[:32],
                    ticker,
                    "ask" if count > 0 else "bid",
                    quantity,
                    price,
                    instrument.version,
                    True,
                )
                if self.config.mode == "paper":
                    results.append(
                        self.broker.submit(order, timestamp_ms=now, reason="manual_flatten")
                    )
                else:
                    results.append(self.broker.submit(order, timestamp_ms=now))
        return {
            "results": results,
            "report": self.broker.report(),
            "note": "Inspect residual positions; bounded closes may be partial.",
        }


def run(config, recording=None, once=False):
    profile, _, _ = describe_strategy(config)
    if profile and profile.name == "doge-sol-trend-shadow":
        if recording is not None:
            raise ValueError("shadow account accepts fresh observations only")
        from .shadow_forward import run_forward

        return run_forward(config, once=once)
    if (
        profile
        and profile.name == "doge-sol-adx14"
        and profile.evidence.get("execution_status") == "paper_authorized"
    ):
        if recording is not None:
            raise ValueError("ADX forward account accepts fresh public observations only")
        from .adx_forward import run_forward

        return run_forward(config, once=once)
    if profile and profile.evidence.get("execution_status") == "blocked":
        return {
            "mode": config.mode,
            "state": "research_gated",
            "leverage_mode": config.raw.get("paper", {}).get("leverage_mode", "fixed"),
            "strategy": profile.name,
            "reason": "No candidate passed frozen walk-forward robustness criteria",
            "paper_strategy_fills": 0,
            "withdrawals_enabled": False,
        }
    with lease(config.database):
        stop_path = Path(str(config.database) + ".stop")
        if stop_path.exists():
            # Invoking run is the explicit resume action; no fourth CLI command.
            stop_path.unlink()
        strategy, broker = build(config)
        runner = Runner(config, strategy, broker)
        external = None
        try:
            source_identity = (
                {"kind": "recording", "sha256": digest(json.loads(Path(recording).read_text()))}
                if recording
                else {
                    "kind": "forward",
                    "environment": config.raw["venue"]["environment"],
                    "signal_provider": config.raw.get("data", {}).get("provider", "kalshi"),
                }
            )
            with broker.store.transaction():
                state = broker.store.state()
                if state.get("data_source", source_identity) != source_identity:
                    raise ValueError(
                        "account database cannot mix recordings and native market data"
                    )
                state["data_source"] = source_identity
                broker.store.save(state)
            if recording:
                if config.mode != "paper":
                    raise ValueError("recordings are paper-only")
                data = json.loads(Path(recording).read_text())
                if data["schema"] != "kalshi-perps-recording/1":
                    raise ValueError("unsupported recording schema")
                if data["price_unit"] != "usd_per_contract":
                    raise ValueError("recording price units must be USD per contract")
                summary = None
                for event in data["events"]:
                    if stop_path.exists():
                        instruction = json.loads(stop_path.read_text())
                        if instruction.get("flatten"):
                            runner.flatten(instruction["id"])
                        break
                    # Whole step checkpoint makes restart replay skip orders and cadence already applied.
                    key = "completed-step:" + event["event_id"]
                    payload_hash = digest({"event": event, "candles": data["candles"]})
                    old = broker.store.db.execute(
                        "SELECT hash,result FROM events WHERE id=?", (key,)
                    ).fetchone()
                    if old:
                        if old[0] != digest(payload_hash):
                            raise ValueError("recording step changed")
                        summary = json.loads(old[1])
                        continue
                    summary = runner.process(event, data["candles"])
                    broker.store.event(key, payload_hash, lambda state, result=summary: result)
                return {
                    "result": summary,
                    "report": broker.report(),
                    "data_source": data.get("source", "unspecified"),
                    "funding_coverage": data.get("funding_coverage", "unknown"),
                }
            feed_transport = (
                Transport.configured(config) if config.mode == "paper" else broker.transport
            )
            # Even in live, feed operations only call GET; execution remains journaled in the broker.
            feed = Feed(feed_transport, broker.instruments)
            cached_boundary, candles = None, broker.store.state().get("candles")
            data_config = config.raw.get("data", {})
            if data_config.get("provider") == "kraken-perpetuals":
                external = SignalHistory(
                    config.resolve(data_config.get("cache", "../runtime/history.sqlite")),
                    broker.instruments,
                    days=data_config.get("history_days", 90),
                )
            history_report = None
            while True:
                if stop_path.exists():
                    instruction = json.loads(stop_path.read_text())
                    if instruction.get("flatten"):
                        event = feed.snapshot(int(config.raw["risk"]["quote_max_age_ms"]))
                        if config.mode == "paper":
                            broker.snapshot(
                                event["event_id"], event["timestamp_ms"], event["quotes"]
                            )
                        result = runner.flatten(
                            instruction["id"], event["quotes"], event["timestamp_ms"]
                        )
                    else:
                        if config.mode != "paper":
                            broker.cancel_all()
                        result = broker.report()
                    return result
                boundary = feed_transport.clock() // HOUR * HOUR
                if boundary != cached_boundary:
                    signal_file = config.raw["strategy"].get("history_file")
                    if external:
                        try:
                            candles, history_report = external.load(boundary)
                        except HistoryUnavailable as exc:
                            if once:
                                return {
                                    "state": "warming_up",
                                    "reason": str(exc),
                                    "report": broker.report(),
                                }
                            print(
                                canonical({"state": "warming_up", "reason": str(exc)}), flush=True
                            )
                            time.sleep(5)
                            continue
                    elif signal_file:
                        supplied = json.loads(config.resolve(signal_file).read_text())
                        if (
                            supplied.get("schema") != "crypto-perps-signal-history/1"
                            or supplied.get("price_unit") != "usd_per_contract"
                            or not supplied.get("source")
                        ):
                            raise ValueError(
                                "signal history requires schema, source and explicit API price units"
                            )
                        incoming = supplied["candles"]
                        # Existing completed observations are immutable within an account.
                        for ticker, old_rows in (candles or {}).items():
                            by_time = {r["timestamp_ms"]: r for r in incoming[ticker]}
                            if any(by_time.get(r["timestamp_ms"]) != r for r in old_rows):
                                raise ValueError(
                                    "signal history rewrote or removed completed observations"
                                )
                        candles = incoming
                    else:
                        candles = feed.candles(boundary, candles)
                    with broker.store.transaction():
                        state = broker.store.state()
                        state["candles"] = candles
                        broker.store.save(state)
                    cached_boundary = boundary
                event = feed.snapshot(int(config.raw["risk"]["quote_max_age_ms"]))
                if config.mode == "paper":
                    # Funding may publish after the event. Recover the held quantity
                    # from durable fills rather than charging the current position.
                    state = broker.store.state()
                    start = state.get("funding_start_ms", event["timestamp_ms"])
                    rates = feed.funding(start, event["timestamp_ms"])
                    with broker.store.transaction():
                        state = broker.store.state()
                        state["funding_start_ms"] = start
                        broker.store.save(state)
                        broker.snapshot(event["event_id"], event["timestamp_ms"], event["quotes"])
                        for rate in rates:
                            broker.apply_historical_funding(rate)
                basis = {
                    t: str(
                        abs(dec(candles[t][-1]["close"]) / dec(event["quotes"][t]["mark"]) - 1)
                        * 10000
                    )
                    for t in candles
                }
                limit = dec(data_config.get("max_basis_bps", "500"))
                if external and any(dec(v) > limit for v in basis.values()):
                    summary = {
                        "state": "signal_basis_blocked",
                        "reason": "external signal/execution price divergence",
                        "basis_bps": basis,
                        "report": broker.report(),
                    }
                else:
                    summary = runner.process(event, candles)
                summary["funding_coverage"] = (
                    "published native funding history; late rates backfilled using historical holdings"
                    if config.mode == "paper"
                    else "venue authoritative"
                )
                if history_report:
                    summary["signal_history"] = history_report
                    with broker.store.transaction():
                        state = broker.store.state()
                        state["signal_history"] = history_report
                        state["strategy_profile"] = (
                            strategy.profile.name if strategy.profile else "btc-eth-reference"
                        )
                        broker.store.save(state)
                if once:
                    return summary
                print(
                    canonical(
                        {
                            "state": summary["state"],
                            "mode": config.mode,
                            "timestamp_ms": event["timestamp_ms"],
                            "reason": summary.get("reason"),
                        }
                    ),
                    flush=True,
                )
                time.sleep(5)
        finally:
            if external:
                external.close()
            broker.store.close()
