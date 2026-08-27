from decimal import Decimal

from research.replay_events import ReplayEvent, ReplayOrder, replay


def event(event_id, moment, *, event_type="snapshot", side=None, price=None, delta=None, payload=None):
    return ReplayEvent(
        event_id=event_id,
        ticker="M",
        monotonic_ns=moment,
        sequence=int(event_id),
        event_type=event_type,
        side=side,
        price=Decimal(price) if price else None,
        delta=Decimal(delta) if delta else None,
        payload=payload or {},
    )


def order(order_id, moment, contracts="5", limit="0.50"):
    return ReplayOrder(
        client_order_id=order_id,
        ticker="M",
        outcome="yes",
        limit_price=Decimal(limit),
        contracts=Decimal(contracts),
        submitted_monotonic_ns=moment,
    )


def test_replay_never_looks_ahead_to_a_better_book():
    events = [
        event("1", 100, payload={"yes": [["0.40", "10"]], "no": [["0.40", "10"]]}),
        # This later NO bid makes YES cost 0.45, but it arrives after the order.
        event("2", 300, event_type="delta", side="no", price="0.55", delta="10"),
    ]
    fill = replay(events, [order("o", 150)], latency_ms=Decimal("0"))[0]
    assert fill.filled == 0


def test_replay_walks_depth_and_reports_partial_ioc_vwap():
    events = [
        event(
            "1",
            100,
            payload={
                "yes": [["0.30", "20"]],
                "no": [["0.55", "2"], ["0.53", "2"]],
            },
        )
    ]
    fill = replay(
        events,
        [order("o", 150, contracts="5", limit="0.48")],
        latency_ms=Decimal("0"),
    )[0]
    assert fill.filled == Decimal("4")
    assert fill.average_price == Decimal("0.46")
    assert fill.complete is False


def test_replay_consumes_depth_between_orders():
    events = [
        event("1", 100, payload={"yes": [["0.30", "20"]], "no": [["0.55", "3"]]}),
    ]
    fills = replay(
        events,
        [order("a", 150, contracts="2"), order("b", 160, contracts="2")],
        latency_ms=Decimal("0"),
    )
    assert fills[0].filled == Decimal("2")
    assert fills[1].filled == Decimal("1")
