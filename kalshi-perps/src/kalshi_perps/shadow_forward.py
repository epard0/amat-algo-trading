"""Isolated, frozen signed 1x forward experiment; public read-only transport."""

import json
import time
from datetime import UTC, datetime
from decimal import ROUND_CEILING
from pathlib import Path

from .adx_forward import require_fresh_execution
from .domain import ZERO, Order, book, dec, digest, toward_zero
from .paper import equity

NAME = "doge-sol-trend-shadow"


def execute_targets(runner, event, decision):
    if runner.config.mode != "paper" or runner.config.raw.get("execution_enabled", False):
        raise ValueError("shadow is paper only")
    broker = runner.broker
    store = broker.store
    state = store.state()
    now = event["timestamp_ms"]
    start = state["forward_start_ms"]
    policies = decision["signed_exit_policy"]
    if set(policies) != set(runner.symbols):
        raise ValueError("shadow policy mismatch")
    levels = {}
    ignored = {}
    for t, p in policies.items():
        level = dec(p["level"])
        direction = p["direction"]
        entry = p["entry_ms"]
        if (
            not ZERO <= level <= dec(".98")
            or direction not in (-1, 0, 1)
            or bool(level) != bool(direction)
        ):
            raise ValueError("invalid signed policy")
        if level and (type(entry) is not int or entry > now):
            raise ValueError("invalid entry timestamp")
        ignored[t] = bool(level and entry < start)
        levels[t] = ZERO if ignored[t] else level * direction
    allocations = {leg["ticker"]: dec(leg["allocation"]) for leg in runner.strategy.profile.legs}
    total = sum((allocations[t] for t, v in levels.items() if v), ZERO)
    meta = state.setdefault("shadow_execution", {})
    state.setdefault("adx_completed_actions", {})
    active_changed = any(
        (1 if levels[t] > 0 else -1 if levels[t] < 0 else 0) != meta.get(t, {}).get("direction", 0)
        for t in runner.symbols
    )
    targets = {}
    for t in runner.symbols:
        p = policies[t]
        level = abs(levels[t])
        direction = 1 if levels[t] > 0 else -1 if levels[t] < 0 else 0
        m = meta.setdefault(t, dict(episode=None, level="0", direction=0, day=None))
        q = dec(state["positions"].get(t, {}).get("count", "0"))
        old = dec(m["level"])
        pending = m.get("pending")
        inst = broker.instruments[t]
        desired = (
            toward_zero(
                direction
                * max(ZERO, equity(state))
                * dec(".98")
                * allocations[t]
                / total
                * level
                / dec(".98")
                / dec(event["quotes"][t]["mark"]),
                inst.step,
            )
            if level
            else ZERO
        )
        if not level:
            reason = (
                p["reason"] if p["reason"] not in ("hold", "flat", "entry") else "baseline_exit"
            )
            pending = (
                dict(
                    target="0",
                    level="0",
                    episode=m["episode"],
                    direction=0,
                    reason=reason,
                    action_id=f"{t}:{m['episode']}:exit",
                )
                if not pending or dec(pending["level"])
                else pending
            )
        elif p["entry_ms"] != m["episode"] or direction != m["direction"]:
            if q:
                pending = dict(
                    target="0",
                    level="0",
                    episode=m["episode"],
                    direction=0,
                    reason="missed_episode_exit",
                    action_id=f"{t}:{m['episode']}:exit",
                )
            else:
                m.update(episode=p["entry_ms"], level="0", direction=direction)
                pending = dict(
                    target=str(desired if level == dec(".98") else ZERO),
                    level=str(level),
                    episode=p["entry_ms"],
                    direction=direction,
                    reason="entry" if level == dec(".98") else "skipped_missed_entry",
                    action_id=f"{t}:{p['entry_ms']}:entry",
                )
        elif pending and pending["reason"] == "partial_tp" and level <= dec(pending["level"]):
            if level < dec(pending["level"]):
                pending["target"] = str(
                    toward_zero(dec(pending["target"]) * level / dec(pending["level"]), inst.step)
                )
                pending["level"] = str(level)
                pending["action_id"] = f"{t}:{p['entry_ms']}:partial:{level}"
            pending["target"] = str(direction * min(abs(q), abs(dec(pending["target"]))))
        elif (
            pending and pending["reason"] == "entry" and level < dec(pending["level"])
        ) or level < old:
            basis = dec(pending["level"]) if pending and pending["reason"] == "entry" else old
            pending = dict(
                target=str(toward_zero(q * level / basis, inst.step)),
                level=str(level),
                episode=p["entry_ms"],
                direction=direction,
                reason="partial_tp",
                action_id=f"{t}:{p['entry_ms']}:partial:{level}",
            )
        elif pending and pending["reason"] == "entry":
            pass  # Keep absolute entry intent across partial IOC fills/restarts.
        elif not active_changed and m.get("day") == now // 86400000:
            continue
        else:
            if level < dec(".98") and old:
                desired = direction * min(abs(q), abs(desired))
            pending = dict(
                target=str(desired),
                level=str(level),
                episode=p["entry_ms"],
                direction=direction,
                reason="rebalance",
                action_id=f"{t}:{p['entry_ms']}:resize:{now}",
            )
        m["pending"] = pending
        targets[t] = dec(pending["target"])
    store.save(state)
    orders = []
    for reducing in (True, False):
        for t, target in targets.items():
            state = store.state()
            pending = state["shadow_execution"][t]["pending"]
            inst = broker.instruments[t]
            current = dec(state["positions"].get(t, {}).get("count", "0"))
            delta = target - current
            reduction = bool(current and current * delta < 0)
            if not delta or reduction != reducing:
                continue
            if reduction:
                delta = (-1 if current > 0 else 1) * min(abs(delta), abs(current))
            depth = book(event["quotes"][t], inst)
            bid, ask = depth["bids"][0][0], depth["asks"][0][0]
            mid = (bid + ask) / 2
            if not reduction and (ask - bid) / mid * 10000 > dec(runner.risk["max_spread_bps"]):
                continue
            slip = (
                dec(runner.risk.get("max_execution_cost_bps", "30")) - broker.fee_rate * 10000
            ) / 10000
            if slip <= 0:
                raise ValueError("invalid execution cap")
            limit = (
                toward_zero(mid * (1 + slip), inst.tick)
                if delta > 0
                else (mid * (1 - slip) / inst.tick).to_integral_value(rounding=ROUND_CEILING)
                * inst.tick
            )
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
                reduce_only=reduction,
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
                    result=result,
                    reason=pending["reason"],
                    action_id=pending["action_id"],
                )
            )
    state = store.state()
    for t in targets:
        m = state["shadow_execution"][t]
        p = m["pending"]
        q = dec(state["positions"].get(t, {}).get("count", "0"))
        if q == dec(p["target"]):
            m.update(
                episode=p["episode"],
                level=p["level"],
                direction=p["direction"],
                day=now // 86400000,
            )
            m.pop("pending")
            if p["reason"] in ("partial_tp", "take_profit"):
                state["adx_completed_actions"].setdefault(
                    p["action_id"], dict(ticker=t, reason=p["reason"], timestamp_ms=now)
                )
    state["strategy_profile"] = NAME
    state["last_adx_decision_ms"] = decision["as_of_ms"]
    store.save(state)
    return dict(
        state="running",
        decision=decision,
        orders=orders,
        ignored_carried_signals=ignored,
        report=broker.report(),
    )


def account_metrics(store, start_ms, now_ms):
    from .comparison_metrics import metrics

    result = metrics(store, start_ms, now_ms)
    events = []
    for seq, key, body, outcome in store.db.execute(
        "SELECT rowid,id,body,result FROM events WHERE id GLOB 'order:*' OR id GLOB 'funding:*' OR id GLOB 'snapshot:*'"
    ):
        p = json.loads(body)
        r = json.loads(outcome)
        events.append((p["timestamp_ms"], 0 if key.startswith("funding:") else 1, seq, key, p, r))
    counts = {}
    pools = {}
    actions = {}
    episode = {}
    closed = []
    for when, _, _, key, p, r in sorted(events):
        if key.startswith("funding:"):
            t = p["ticker"]
            pools[t] = pools.get(t, ZERO) + dec(r["payment"])
            continue
        for f in r.get("fills", []) if key.startswith("order:") else r.get("liquidations", []):
            t = f["ticker"]
            delta = dec(f["count"])
            old = counts.get(t, ZERO)
            fee = dec(f["fee"])
            reason = p.get("reason", "liquidation" if key.startswith("snapshot:") else "unknown")
            if not old or old * delta > 0:
                if not old:
                    episode[t] = ZERO
                pools[t] = pools.get(t, ZERO) - fee
            else:
                if abs(delta) > abs(old):
                    raise ValueError("shadow fill crossed zero")
                allocation = pools.get(t, ZERO) * abs(delta) / abs(old)
                net = dec(f["realized_pnl"]) - fee + allocation
                pools[t] = pools.get(t, ZERO) - allocation
                episode[t] = episode.get(t, ZERO) + net
                if reason in ("partial_tp", "take_profit"):
                    aid = p.get("action_id", key)
                    a = actions.setdefault(aid, dict(ticker=t, reason=reason, net=ZERO))
                    a["net"] += net
                if old + delta == 0:
                    closed.append(
                        dict(ticker=t, net_pnl=str(episode[t]), reason=reason, closed_ms=when)
                    )
            counts[t] = old + delta
    days = max(0, (now_ms - start_ms) / 86400000)
    result.update(
        take_profit_actions=len(actions),
        profitable_take_profit_actions=sum(a["net"] > 0 for a in actions.values()),
        partial_take_profit_actions=sum(a["reason"] == "partial_tp" for a in actions.values()),
        take_profit_actions_per_30d=len(actions) / days * 30 if days >= 1 else None,
        closed_episode_details=closed,
        withdrawals_enabled=False,
    )
    return result


def comparison(config, report):
    """Same-start mark-to-market baseline; never touches the default database."""
    source = Path(config.raw["shadow"]["default_report"])
    dest = config.database.with_suffix(".comparison-baseline.json")
    try:
        default = json.loads(source.read_text())
        stamp = datetime.fromisoformat(default["updated_at"]).timestamp()
        age = time.time() - stamp
        if not 0 <= age < 180:
            raise ValueError("default report stale")
        m = default["metrics"]
        now = report["timestamp_ms"]
        if dest.exists():
            base = json.loads(dest.read_text())
        else:
            base = dict(
                start_ms=report["start_ms"],
                default_observation_ms=default["timestamp_ms"],
                equity=m["equity"],
                funding=m["funding"],
                fees=m["fees"],
                tp=m["take_profit_actions"],
                peak=m["equity"],
                max_drawdown="0",
            )
        value = dec(m["equity"])
        peak = max(dec(base["peak"]), value)
        dd = min(dec(base["max_drawdown"]), value / peak - 1)
        base.update(peak=str(peak), max_drawdown=str(dd))
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(base, indent=2))
        tmp.replace(dest)
        days = (now - base["start_ms"]) / 86400000
        tp = m["take_profit_actions"] - base["tp"]
        default_metrics = dict(
            net_pnl=str(value - dec(base["equity"])),
            funding=str(dec(m["funding"]) - dec(base["funding"])),
            fees=str(dec(m["fees"]) - dec(base["fees"])),
            max_drawdown=str(dd),
            take_profit_actions=tp,
            take_profit_actions_per_30d=tp / days * 30 if days >= 1 else None,
            positions=m["positions"],
        )
        return dict(
            state="observing",
            comparison_start_ms=base["start_ms"],
            default_baseline_observation_ms=base["default_observation_ms"],
            default_updated_at=default["updated_at"],
            default=default_metrics,
            shadow=report["metrics"],
            note="Default retains existing positions and capital. P&L/funding/fees/TP deltas begin at baseline; drawdown samples begin at that equity. Shadow starts flat and skips carried signals; leverage and DOGE EMA differ.",
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(state="default_comparison_unavailable", reason=str(exc))


def write_report(config, summary, broker):
    state = broker.store.state()
    now = state["timestamp_ms"]
    start = state["forward_start_ms"]
    report = dict(
        mode="paper",
        live_enabled=False,
        strategy=NAME,
        state=summary["state"],
        updated_at=datetime.now(UTC).isoformat(),
        timestamp_ms=now,
        start_ms=start,
        leverage_mode="fixed_1x",
        settings=dict(
            adx=30,
            DOGE_ema_bars=25,
            SOL_ema_bars=50,
            ema_slope_lag_bars=3,
            allocation={"DOGE": 0.25, "SOL": 0.75},
        ),
        policy_hash=state.get("shadow_policy_hash"),
        metrics=account_metrics(broker.store, start, now),
        data_flow=summary.get("data_flow"),
        signal_history=state.get("signal_history"),
        ignored_carried_signals=summary.get("ignored_carried_signals"),
        withdrawals_enabled=False,
        **{
            k: summary.get(k) for k in ("error_type", "error_stage", "error_reason", "error_frames")
        },
    )
    report["comparison"] = comparison(config, report)
    directory = config.database.parent / "adx-forward"
    directory.mkdir(exist_ok=True)
    tmp = directory / "status.tmp"
    tmp.write_text(json.dumps(report, indent=2))
    tmp.replace(directory / "status.json")
    lines = [
        "# Frozen DOGE/SOL trend shadow",
        "",
        f"Updated: {report['updated_at']} | State: {report['state']}",
        "",
        "DOGE EMA25, SOL EMA50; ADX30; 1x target; $10,000 start. Public data, simulated native-book fills and funding. Default account unchanged.",
        "",
        "| Fresh comparison | Shadow | Current default |",
        "|---|---:|---:|",
    ]
    c = report["comparison"]
    if c["state"] == "observing":
        for key in (
            "net_pnl",
            "max_drawdown",
            "funding",
            "fees",
            "take_profit_actions",
            "take_profit_actions_per_30d",
        ):
            lines.append(f"| {key} | {report['metrics'][key]} | {c['default'][key]} |")
    else:
        lines.append(c["reason"])
    (directory / "status.md").write_text("\n".join(lines) + "\n")
    return report


def run_forward(config, once=False):
    import httpx

    from .comparison import CachedStrategy
    from .domain import HOUR
    from .history import SignalHistory
    from .marketdata import Feed
    from .operations import diagnostic
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
        state["strategy_profile"] = NAME
        state["shadow_policy_hash"] = engine.identity
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
