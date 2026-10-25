"""User-authorized ADX paper forward account; no signed transport or venue writes."""

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .domain import HOUR, ZERO, Order, book, dec, digest, toward_zero
from .leverage import maximum_weights
from .operations import diagnostic, report_alert
from .paper import equity


def require_fresh_execution(event):
    deadline = event.get("execution_deadline_ms")
    if deadline is not None and int(time.time() * 1000) > deadline:
        raise ValueError("stale snapshot before execution")


def execute_targets(runner, event, decision):
    broker = runner.broker
    store = broker.store
    now = event["timestamp_ms"]
    state = store.state()
    if runner.config.mode != "paper":
        raise ValueError("ADX execution is paper only")
    if runner.config.raw["paper"].get("leverage_mode") != "venue_max":
        raise ValueError("ADX forward account requires published maximum leverage mode")
    start = state.setdefault("forward_start_ms", now)
    meta = state.setdefault("adx_execution", {})
    state.setdefault("adx_completed_actions", {})
    policies = decision["exit_policy"]
    levels = {}
    entries = {}
    ignored = {}
    if set(policies) != set(runner.symbols):
        raise ValueError("ADX policy instrument mismatch")
    for t, p in policies.items():
        level = dec(p["level"])
        entry = p["entry_ms"]
        if not ZERO <= level <= dec(".98"):
            raise ValueError("invalid ADX remaining level")
        if level and (type(entry) is not int or entry > now):
            raise ValueError("invalid ADX episode timestamp")
        ignored[t] = bool(level and entry < start)
        levels[t] = ZERO if ignored[t] else level
        entries[t] = entry
    allocations = {leg["ticker"]: leg["allocation"] for leg in runner.strategy.profile.legs}
    weights, limits = maximum_weights(levels, allocations, event["quotes"], equity(state))
    gross = sum(weights.values(), ZERO)
    cap = dec(runner.risk["max_gross"])
    if gross > cap:
        weights = {t: w * cap / gross for t, w in weights.items()}
    orders = []
    targets = {}
    for t in runner.symbols:
        inst = broker.instruments[t]
        q = dec(state["positions"].get(t, {}).get("count", "0"))
        if q < 0:
            raise ValueError("ADX paper account is long only")
        m = meta.setdefault(t, dict(episode=None, level="0"))
        level = levels[t]
        episode = entries[t]
        old_level = dec(m["level"])
        pending = m.get("pending")
        desired = toward_zero(
            weights[t] * max(ZERO, equity(state)) / dec(event["quotes"][t]["mark"]), inst.step
        )
        desired = toward_zero(desired * level / dec(".98"), inst.step) if level else ZERO
        if not level:
            reason = policies[t]["reason"]
            if reason in ("hold", "flat", "entry"):
                reason = "baseline_exit"
            action = f"{t}:{m.get('episode')}:exit"
            if pending and dec(pending["level"]) == 0:
                reason = pending["reason"]
                action = pending["action_id"]
            pending = dict(
                target="0", level="0", episode=m.get("episode"), reason=reason, action_id=action
            )
        elif episode != m.get("episode"):
            if q:
                pending = dict(
                    target="0",
                    level="0",
                    episode=m.get("episode"),
                    reason="missed_episode_exit",
                    action_id=f"{t}:{m.get('episode')}:exit",
                )
            else:
                m["episode"] = episode
                m["level"] = "0"
                old_level = ZERO
                pending = dict(
                    target=str(desired if level == dec(".98") else ZERO),
                    level=str(level),
                    episode=episode,
                    reason="entry" if level == dec(".98") else "skipped_missed_entry",
                    action_id=f"{t}:{episode}:entry",
                )
        elif pending and pending["reason"] == "partial_tp" and level <= dec(pending["level"]):
            # Frozen absolute residual survives partial IOC fills and process restarts.
            if level < dec(pending["level"]):
                pending["target"] = str(
                    toward_zero(dec(pending["target"]) * level / dec(pending["level"]), inst.step)
                )
                pending["level"] = str(level)
                pending["action_id"] = f"{t}:{episode}:partial:{level}"
            pending["target"] = str(min(q, dec(pending["target"])))
        elif pending and pending["reason"] == "entry" and level < dec(pending["level"]):
            target = toward_zero(q * level / dec(pending["level"]), inst.step)
            pending = dict(
                target=str(target),
                level=str(level),
                episode=episode,
                reason="partial_tp",
                action_id=f"{t}:{episode}:partial:{level}",
            )
        elif level < old_level:
            target = toward_zero(q * level / old_level, inst.step)
            pending = dict(
                target=str(target),
                level=str(level),
                episode=episode,
                reason="partial_tp",
                action_id=f"{t}:{episode}:partial:{level}",
            )
        else:
            if level < dec(".98") and old_level > 0:
                desired = min(q, desired)
            pending = dict(
                target=str(desired),
                level=str(level),
                episode=episode,
                reason="entry" if not old_level else "rebalance",
                action_id=f"{t}:{episode}:resize:{now}",
            )
        m["pending"] = pending
        targets[t] = dec(pending["target"])
    # Commit the complete intent before individual mutations; Runner.process wraps the whole step atomically.
    store.save(state)
    for reducing in (True, False):
        for t in runner.symbols:
            state = store.state()
            m = state["adx_execution"][t]
            pending = m["pending"]
            inst = broker.instruments[t]
            current = dec(state["positions"].get(t, {}).get("count", "0"))
            target = targets[t]
            delta = target - current
            if not delta or (delta < 0) != reducing:
                continue
            levels_book = book(event["quotes"][t], inst)
            bid, ask = levels_book["bids"][0][0], levels_book["asks"][0][0]
            mid = (bid + ask) / 2
            if delta > 0 and (ask - bid) / mid * 10000 > dec(runner.risk["max_spread_bps"]):
                continue
            # IOC walks actual sorted, consumed depth within a fixed total cost bound.
            slippage = (
                dec(runner.risk.get("max_execution_cost_bps", "30")) - broker.fee_rate * 10000
            ) / 10000
            if slippage <= 0:
                raise ValueError("execution cost cap must exceed taker fee")
            limit = toward_zero(mid * (1 + slippage), inst.tick)
            # toward_zero on negative is not a ceiling; explicitly round adverse sell bound upward.
            if delta < 0:
                from decimal import ROUND_CEILING

                limit = (mid * (1 - slippage) / inst.tick).to_integral_value(
                    rounding=ROUND_CEILING
                ) * inst.tick
            quantity = min(
                abs(delta), toward_zero(dec(runner.risk["max_order_notional"]) / limit, inst.step)
            )
            if not quantity:
                continue
            order = Order(
                digest(
                    dict(
                        policy=runner.strategy.identity,
                        event=event["event_id"],
                        ticker=t,
                        reduce=reducing,
                    )
                )[:32],
                t,
                "bid" if delta > 0 else "ask",
                quantity,
                limit,
                inst.version,
                reduce_only=delta < 0,
            )
            try:
                require_fresh_execution(event)
                result = broker.submit(
                    order,
                    timestamp_ms=now,
                    reason=pending["reason"],
                    action_id=pending["action_id"],
                )
            except ValueError as exc:
                if "initial margin" not in str(exc):
                    raise
                result = dict(status="rejected", reason="initial_margin")
            orders.append(
                dict(
                    order=order.payload(),
                    action_id=pending["action_id"],
                    reason=pending["reason"],
                    result=result,
                )
            )
    state = store.state()
    for t in runner.symbols:
        m = state["adx_execution"][t]
        p = m["pending"]
        actual = dec(state["positions"].get(t, {}).get("count", "0"))
        if actual == dec(p["target"]):
            m["episode"] = p["episode"]
            m["level"] = p["level"]
            m.pop("pending")
            if p["reason"] in ("partial_tp", "take_profit"):
                state["adx_completed_actions"].setdefault(
                    p["action_id"], dict(ticker=t, reason=p["reason"], timestamp_ms=now)
                )
    state["strategy_profile"] = "doge-sol-adx14"
    state["last_adx_decision_ms"] = decision["as_of_ms"]
    store.save(state)
    return dict(
        state="running",
        decision=decision,
        orders=orders,
        leverage_limits=limits,
        ignored_carried_signals=ignored,
        report=broker.report(),
    )


def account_metrics(store, start_ms, now_ms):
    from .comparison_metrics import metrics

    result = metrics(store, start_ms, now_ms)
    state = store.state()
    from .ledger import ordered_events

    q = {}
    pool = {}
    actions = {}
    reductions = []
    episode_net = {}
    closed = []
    for when, _, key, p, r in ordered_events(store.db, liquidations=True):
        if key.startswith("funding:"):
            t = p["ticker"]
            pool[t] = pool.get(t, ZERO) + dec(r["payment"])
            continue
        fills = r.get("fills", []) if key.startswith("order:") else r.get("liquidations", [])
        for f in fills:
            t = f["ticker"]
            delta = dec(f["count"])
            old = q.get(t, ZERO)
            fee = dec(f["fee"])
            reason = p.get("reason", "liquidation" if key.startswith("snapshot:") else "unknown")
            if delta > 0:
                if not old:
                    episode_net[t] = ZERO
                pool[t] = pool.get(t, ZERO) - fee
            elif old > 0:
                allocated = pool.get(t, ZERO) * (-delta) / old
                net = dec(f["realized_pnl"]) - fee + allocated
                pool[t] = pool.get(t, ZERO) - allocated
                episode_net[t] = episode_net.get(t, ZERO) + net
                reduction = dict(ticker=t, timestamp_ms=when, net_pnl=str(net), reason=reason)
                reductions.append(reduction)
                if reason in ("partial_tp", "take_profit"):
                    aid = p.get("action_id", key)
                    action = actions.setdefault(
                        aid, dict(ticker=t, reason=reason, net=ZERO, contracts=ZERO)
                    )
                    action["net"] += net
                    action["contracts"] -= delta
                if old + delta == 0:
                    closed.append(
                        dict(ticker=t, net_pnl=str(episode_net[t]), reason=reason, closed_ms=when)
                    )
            q[t] = old + delta
    days = max(0, (now_ms - start_ms) / 86400000)
    result.update(
        take_profit_actions=len(actions),
        profitable_take_profit_actions=sum(a["net"] > 0 for a in actions.values()),
        partial_take_profit_actions=sum(a["reason"] == "partial_tp" for a in actions.values()),
        take_profit_actions_completed=sum(
            k in actions for k in state.get("adx_completed_actions", {})
        ),
        take_profit_actions_per_30d=len(actions) / days * 30 if days >= 1 else None,
        natural_completed_episodes=sum(
            c["reason"] not in ("liquidation", "rebalance", "manual_flatten") for c in closed
        ),
        closed_episode_details=closed,
        withdrawals_enabled=False,
        take_profit_action_details={
            k: dict(
                ticker=a["ticker"],
                reason=a["reason"],
                net_pnl=str(a["net"]),
                contracts=str(a["contracts"]),
            )
            for k, a in actions.items()
        },
    )
    return result


def write_report(config, summary, broker):
    now = broker.store.state()["timestamp_ms"]
    start = broker.store.state().get("forward_start_ms", now)
    report = dict(
        mode="paper",
        live_enabled=False,
        strategy="doge-sol-adx14",
        state=summary["state"],
        **{
            k: summary.get(k) for k in ("error_type", "error_stage", "error_reason", "error_frames")
        },
        data_flow=summary.get("data_flow"),
        signal_history=broker.store.state().get("signal_history"),
        updated_at=datetime.now(ZoneInfo("America/Detroit")).isoformat(),
        timestamp_ms=now,
        start_ms=start,
        leverage_mode="venue_max",
        metrics=account_metrics(broker.store, start, now),
        leverage_limits=summary.get("leverage_limits"),
        ignored_carried_signals=summary.get("ignored_carried_signals"),
        funding_coverage="Native published funding at actual event timestamps; late publication backfilled against historical holdings",
        withdrawals_enabled=False,
    )
    directory = config.database.parent / "adx-forward"
    directory.mkdir(exist_ok=True)
    target = directory / "status.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2))
    tmp.replace(target)
    m = report["metrics"]
    lines = [
        "# Default ADX DOGE/SOL paper account",
        "",
        f"Updated: {report['updated_at']}",
        f"State: {report['state']} | Maximum published initial leverage | Paper only",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Net P&L | {dec(m['net_pnl']):,.2f} |",
        f"| Equity | {dec(m['equity']):,.2f} |",
        f"| Max sampled drawdown | {dec(m['max_drawdown']):.2%} |",
        f"| Funding P&L | {dec(m['funding']):,.4f} |",
        f"| Fees | {dec(m['fees']):,.4f} |",
        f"| Take-profit actions with actual fills | {m['take_profit_actions']} |",
        f"| Profitable take-profit actions | {m['profitable_take_profit_actions']} |",
        f"| Partial take-profit actions | {m['partial_take_profit_actions']} |",
        f"| Take-profit actions/30days | {m['take_profit_actions_per_30d']} |",
        f"| Fill records | {m['fills']} |",
        "",
        "All P&L includes realized/unrealized mark changes, filled-side fees and published funding applied so far. Funding can revise P&L when rates publish late. Actual sorted native book depth is consumed within IOC bounds; orders may partially fill or remain unfilled.4bps modeled taker fee;30bps maximum total execution cost per side. Maximum sizing retains2% initial-margin reserve; published side/size curves refresh each observation. Partial take-profits are never bought back. Startup skips carried signals and waits for a new entry. Frequency counts distinct filled profit-taking actions, not each IOC fill; normalization starts after one observed day. Drawdown is sampled, not every intrabar trough. No withdrawals. Historical robustness gate failed; the user explicitly authorized this forward paper experiment.",
    ]
    target = directory / "status.md"
    tmp = target.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(target)
    report_alert(config.database.parent, report)
    return report


def run_forward(config, once=False):
    from .comparison import CachedStrategy
    from .history import SignalHistory
    from .marketdata import Feed
    from .runtime import Runner, build, lease
    from .strategy import HistoryUnavailable
    from .venue import Transport

    if config.mode != "paper" or config.raw.get("execution_enabled", False):
        raise ValueError("ADX account is paper-only; venue writes must remain disabled")
    with lease(config.database):
        stop = Path(str(config.database) + ".stop")
        stop.unlink(missing_ok=True)
        engine, broker = build(config)
        runner = Runner(config, CachedStrategy(engine), broker)
        transport = Transport("production", writable=False)
        feed = Feed(transport, broker.instruments, include_leverage=True)
        data = config.raw["data"]
        history = SignalHistory(
            config.resolve(data["cache"]), broker.instruments, days=data.get("history_days", 90)
        )
        state = broker.store.state()
        state.setdefault("forward_start_ms", transport.clock())
        state["strategy_profile"] = "doge-sol-adx14"
        broker.store.save(state)
        boundary = None
        candles = None
        try:
            while True:
                stage = "stop-check"
                try:
                    if stop.exists():
                        instruction = json.loads(stop.read_text())
                        if instruction.get("flatten"):
                            event = feed.snapshot(int(config.raw["risk"]["quote_max_age_ms"]))
                            broker.snapshot(
                                event["event_id"], event["timestamp_ms"], event["quotes"]
                            )
                            runner.flatten(instruction["id"])
                        return write_report(config, dict(state="stopped"), broker)
                    stage = "signal-history"
                    current = transport.clock() // HOUR * HOUR
                    if current != boundary:
                        candles, audit = history.load(current)
                        boundary = current
                    stage = "native-funding"
                    funding_through = transport.clock()
                    rates = feed.funding(broker.store.state()["forward_start_ms"], funding_through)
                    stage = "native-snapshot"
                    event = feed.snapshot(int(config.raw["risk"]["quote_max_age_ms"]))
                    event["execution_deadline_ms"] = min(
                        min(q["mark_ts_ms"], q["book_received_ms"])
                        for q in event["quotes"].values()
                    ) + int(config.raw["risk"]["quote_max_age_ms"])
                    stage = "paper-accounting"
                    with broker.store.transaction():
                        broker.snapshot(event["event_id"], event["timestamp_ms"], event["quotes"])
                        for rate in rates:
                            broker.apply_historical_funding(rate)
                        state = broker.store.state()
                        state["signal_history"] = audit
                        broker.store.save(state)
                    stage = "strategy"
                    basis = {
                        t: abs(dec(candles[t][-1]["close"]) / dec(event["quotes"][t]["mark"]) - 1)
                        * 10000
                        for t in broker.instruments
                    }
                    if any(v > dec(data.get("max_basis_bps", "500")) for v in basis.values()):
                        summary = dict(state="signal_basis_blocked")
                    else:
                        summary = runner.process(event, candles)
                    summary["data_flow"] = dict(
                        snapshot_ms=event["timestamp_ms"],
                        collection_duration_ms=event["collection_duration_ms"],
                        quote_max_age_ms=int(config.raw["risk"]["quote_max_age_ms"]),
                        completed_hour_boundary_ms=current,
                        basis_bps={t: str(v) for t, v in basis.items()},
                        funding_checked_through_ms=funding_through,
                        quotes={
                            t: {k: q[k] for k in ("mark", "mark_ts_ms", "book_received_ms")}
                            for t, q in event["quotes"].items()
                        },
                    )
                    result = write_report(config, summary, broker)
                    if once:
                        return result
                    print(
                        json.dumps(
                            dict(
                                state=result["state"],
                                updated_at=result["updated_at"],
                                net_pnl=result["metrics"]["net_pnl"],
                                fills=result["metrics"]["fills"],
                            )
                        ),
                        flush=True,
                    )
                except (ValueError, RuntimeError, httpx.HTTPError, HistoryUnavailable) as exc:
                    result = write_report(
                        config, dict(state="data_blocked", **diagnostic(exc, stage)), broker
                    )
                    print(
                        json.dumps(dict(state="data_blocked", **diagnostic(exc, stage))),
                        flush=True,
                    )
                    if once:
                        raise
                time.sleep(30)
        finally:
            history.close()
            transport.client.close()
            broker.store.close()
