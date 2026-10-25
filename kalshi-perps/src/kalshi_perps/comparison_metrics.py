"""Account-level and episode-level metrics from durable observed paper fills."""

from datetime import UTC, datetime

from .domain import ZERO, dec
from .paper import equity, notional


def metrics(store, start_ms, now_ms):
    state = store.state()
    from .ledger import ordered_events

    active = {}
    closed = []
    fills = 0
    for when, _, key, payload, outcome in ordered_events(store.db):
        if key.startswith("funding:"):
            episode = active.get(payload["ticker"])
            if episode:
                episode["net"] += dec(outcome["payment"])
            continue
        ticker = payload["order"]["ticker"]
        reason = payload.get("reason", "unknown")
        for fill in outcome.get("fills", []):
            fills += 1
            delta, fee, realized = dec(fill["count"]), dec(fill["fee"]), dec(fill["realized_pnl"])
            episode = active.setdefault(ticker, dict(count=ZERO, net=ZERO, opened_ms=when))
            old = episode["count"]
            if not old or old * delta > 0:
                episode["net"] -= fee
                episode["count"] += delta
                continue
            closing = min(abs(old), abs(delta))
            if reason in ("take_profit", "channel_exit", "time_exit", "manual_flatten"):
                episode["exit_reason"] = reason
            closing_fee = fee * closing / abs(delta)
            episode["net"] += realized - closing_fee
            episode["count"] += delta
            if abs(delta) >= abs(old):
                closed.append(
                    dict(
                        ticker=ticker,
                        opened_ms=episode["opened_ms"],
                        closed_ms=when,
                        net_pnl=str(episode["net"]),
                        reason=episode.get("exit_reason", reason),
                    )
                )
                if episode["count"]:
                    active[ticker] = dict(
                        count=episode["count"], net=-(fee - closing_fee), opened_ms=when
                    )
                else:
                    del active[ticker]
    natural = [t for t in closed if t["reason"] not in ("manual_flatten", "liquidation")]
    positive = [t for t in natural if dec(t["net_pnl"]) > 0]
    take_profits = [t for t in natural if t["reason"] == "take_profit"]
    profitable_tps = [t for t in take_profits if dec(t["net_pnl"]) > 0]
    days = max(0, (now_ms - start_ms) / 86400000)
    value = equity(state)
    return dict(
        equity=str(value),
        net_pnl=str(value - dec(state["initial"])),
        net_return=str(value / dec(state["initial"]) - 1),
        max_drawdown=str(state["max_drawdown"]),
        fees=str(state["fees"]),
        funding=str(state["funding"]),
        realized_price_pnl=str(state["realized_pnl"]),
        unrealized_pnl=str(value - dec(state["cash"])),
        gross_notional=str(notional(state)),
        effective_leverage=str(notional(state) / value) if value > 0 else None,
        fills=fills,
        closed_trades=len(natural),
        profitable_closes=len(positive),
        take_profit_closes=len(take_profits),
        profitable_take_profit_closes=len(profitable_tps),
        take_profit_closes_per_30d=len(take_profits) / days * 30 if days >= 1 else None,
        profitable_close_days=len(
            {datetime.fromtimestamp(t["closed_ms"] / 1000, UTC).date() for t in positive}
        ),
        unattributed_closes=sum(t["reason"] in ("unknown", "strategy") for t in natural),
        observed_days=days,
        halted=state["halted"],
        positions=state["positions"],
        closed_episodes=closed,
    )
