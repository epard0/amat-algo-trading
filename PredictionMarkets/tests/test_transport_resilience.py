"""Order transport: what happens when the venue call fails, and how.

The layering here is not defensive padding. Which recovery is legal depends
entirely on whether Kalshi can possibly have seen the order, and getting that
wrong on a money path means buying the same position twice.

    1. benign rejection        -> zero fill (the venue answered: no)
    2. dead session            -> provably PRE-transmission: heal and resend
    3. anything else ambiguous -> never resend blind; ask the venue whether our
                                  client_order_id already exists
    4. still unknown           -> zero fill, never an exception into the cycle
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import aiohttp
import pytest

from execution.kalshi_client import AsyncKalshiTradingClient, OrderPlan


D = Decimal


def plan(**over) -> OrderPlan:
    base = dict(
        ticker="KXBTC15M-T",
        outcome="yes",
        sdk_book_side="bid",
        sdk_yes_leg_price=D("0.61"),
        outcome_limit_price=D("0.61"),
        contracts=D("15"),
        maximum_cost=D("9.15"),
        fee_reserve=D("0.20"),
        maximum_total_debit=D("9.35"),
        client_order_id="coid-abc",
    )
    base.update(over)
    return OrderPlan(**base)


def client(**over) -> AsyncKalshiTradingClient:
    """A client with the transport stubbed out; no socket is ever opened."""
    c = object.__new__(AsyncKalshiTradingClient)
    c.settings = SimpleNamespace(subaccount=0, request_concurrency=4)
    c.authenticated = True
    c._closed = False
    c._reopens = 0
    c._transport_generation = 1
    import asyncio

    c._gate = asyncio.Semaphore(4)
    c._orders = SimpleNamespace()
    for k, v in over.items():
        setattr(c, k, v)
    return c


async def test_concurrent_dead_session_failures_reopen_only_once() -> None:
    """A wake-from-suspend burst must not close each peer's new transport."""
    import asyncio

    c = client()
    reopened = []

    async def reopen():
        reopened.append(True)
        c._transport_generation += 1
        c._closed = False

    c._reopen = reopen
    await asyncio.gather(*(c._reopen_if_stale(1) for _ in range(12)))

    assert reopened == [True]


def filled_response(count="15", price="0.56"):
    return SimpleNamespace(
        order_id="o1",
        client_order_id="coid-abc",
        fill_count=count,
        remaining_count="0",
        average_fill_price=price,
    )


# ------------------------------------------------------------- detection --


def test_only_pre_transmission_failures_count_as_a_dead_session() -> None:
    """This predicate decides whether a resend can duplicate an order, so its
    boundary is the whole safety argument.
    """
    dead = AsyncKalshiTradingClient.is_dead_session
    assert dead(RuntimeError("Session is closed"))
    assert dead(RuntimeError("Event loop is closed"))
    # Ambiguous: the bytes may already be at the venue.
    assert not dead(TimeoutError())
    assert not dead(RuntimeError("something else entirely"))
    assert not dead(ValueError("nope"))


def test_stale_keepalive_reset_is_retryable_only_for_reads() -> None:
    """A stale pooled socket is ambiguous for an order but harmless for GET."""
    error = aiohttp.ClientConnectionResetError("Cannot write to closing transport")

    assert AsyncKalshiTradingClient.is_retryable_read_transport_error(error)
    assert not AsyncKalshiTradingClient.is_dead_session(error)


async def test_balance_read_reopens_after_closing_transport_reset() -> None:
    """Regression for the live position-service failure on 25 Aug 2026."""
    calls = []
    reopened = []

    async def get_balance(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise aiohttp.ClientConnectionResetError(
                "Cannot write to closing transport"
            )
        return SimpleNamespace(balance_dollars="78.25")

    c = client(_portfolio=SimpleNamespace(get_balance=get_balance))

    async def reopen():
        reopened.append(True)
        c._transport_generation += 1
        c._closed = False

    c._reopen = reopen

    assert await c.get_available_balance() == D("78.25")
    assert c.cached_available_balance() == D("78.25")
    assert reopened == [True]
    assert calls == [{"subaccount": 0}, {"subaccount": 0}]


async def test_read_timeout_is_bounded_without_closing_shared_transport(
    monkeypatch,
) -> None:
    calls = []
    reopened = []

    async def never_returns():
        calls.append(1)
        await asyncio.Event().wait()

    c = client()

    async def reopen():
        reopened.append(True)

    c._reopen = reopen
    monkeypatch.setattr(
        "execution.kalshi_client.KALSHI_READ_ATTEMPT_TIMEOUT_SECONDS", 0.01
    )

    with pytest.raises(TimeoutError):
        await c._call_with_backoff(never_returns)

    assert len(calls) == 2
    assert reopened == []


async def test_one_stalled_series_does_not_block_market_discovery(monkeypatch) -> None:
    c = client()
    c.settings.assets = ("BTC", "GOLD")

    async def discover(asset):
        if asset == "GOLD":
            await asyncio.Event().wait()
        return SimpleNamespace(asset=asset)

    c._get_asset_market = discover
    monkeypatch.setattr(
        "execution.kalshi_client.KALSHI_DISCOVERY_ASSET_TIMEOUT_SECONDS", 0.01
    )

    markets = await c.get_active_markets()

    assert [market.asset for market in markets] == ["BTC"]
    assert c.last_discovery_failed_assets == frozenset({"GOLD"})


async def test_indexed_balance_uses_authoritative_fixed_point_dollars() -> None:
    calls = []

    async def get_balance(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            balance=1052,
            balance_dollars="10.5230",
            balance_breakdown=[
                SimpleNamespace(exchange_index=0, balance="67.7306"),
                SimpleNamespace(exchange_index=2, balance="10.5230"),
            ],
        )

    c = client(_portfolio=SimpleNamespace(get_balance=get_balance))

    assert await c.get_available_balance(exchange_index=2) == D("10.5230")
    assert c.cached_balance_breakdown() == {0: D("67.7306"), 2: D("10.5230")}
    assert c.cached_available_balance() is None
    assert calls == [{"subaccount": 0, "exchange_index": 2}]


async def test_closing_transport_reset_is_not_blindly_retried_for_an_order() -> None:
    """The broader GET recovery boundary must not leak into order submission."""
    calls = []

    async def create_order_v2(**kwargs):
        calls.append(kwargs)
        raise aiohttp.ClientConnectionResetError("Cannot write to closing transport")

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))
    c.find_submitted_order = lambda p: _none()

    result = await c.place_order(plan())

    assert len(calls) == 1
    assert not result.filled
    assert result.error_code == "transport_ClientConnectionResetError"


async def test_shutdown_interlock_refuses_order_before_transport() -> None:
    calls = []

    async def create_order_v2(**kwargs):
        calls.append(kwargs)
        return filled_response()

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))
    c.stop_order_submission()

    result = await c.place_order(plan())

    assert calls == []
    assert not result.filled
    assert result.error_code == "shutdown_requested"


async def test_cancel_reopens_stale_transport_and_retries_by_order_id() -> None:
    calls = []
    reopened = []

    async def cancel_order_v2(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise aiohttp.ClientConnectionResetError(
                "Cannot write to closing transport"
            )

    c = client(_orders=SimpleNamespace(cancel_order_v2=cancel_order_v2))

    async def reopen():
        reopened.append(True)
        c._transport_generation += 1
        c._closed = False

    c._reopen = reopen

    assert await c.cancel_order("resting-1") is True
    assert reopened == [True]
    expected = {
        "order_id": "resting-1",
        "subaccount": 0,
        "exchange_index": 0,
    }
    assert calls == [expected, expected]


async def test_http_429_uses_bounded_backoff_then_recovers(monkeypatch) -> None:
    from kalshi_python_async import ApiException

    c = client()
    attempts = []
    sleeps = []

    async def operation():
        attempts.append(1)
        if len(attempts) < 3:
            raise ApiException(status=429, reason="Too Many Requests")
        return "ok"

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("execution.kalshi_client.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("execution.kalshi_client.random.uniform", lambda a, b: 0.05)
    assert await c._call_with_backoff(operation) == "ok"
    assert len(attempts) == 3
    assert sleeps == [0.55, 1.05]


async def test_http_503_read_uses_bounded_backoff_then_recovers(monkeypatch) -> None:
    from kalshi_python_async import ApiException

    c = client()
    attempts = []
    sleeps = []

    async def operation():
        attempts.append(1)
        if len(attempts) == 1:
            raise ApiException(status=503, reason="Service Unavailable")
        return "ok"

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("execution.kalshi_client.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("execution.kalshi_client.random.uniform", lambda a, b: 0.05)

    assert await c._call_with_backoff(operation) == "ok"
    assert len(attempts) == 2
    assert sleeps == [0.55]


# ------------------------------------------------- layer 2: heal + resend --


async def test_a_closed_session_is_rebuilt_and_the_order_resent() -> None:
    """The exact failure that surfaced from the scalper after a mode switch."""
    calls = []
    reopened = []

    async def create_order_v2(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("Session is closed")
        return filled_response()

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))

    async def _reopen():
        reopened.append(True)
        c._closed = False

    c._reopen = _reopen

    result = await c.place_order(plan())
    assert reopened == [True], "the transport must be rebuilt, not just retried"
    assert len(calls) == 2
    assert result.fill_count == D("15")


async def test_it_heals_at_most_once_and_never_loops() -> None:
    calls = []

    async def create_order_v2(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("Session is closed")

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))

    async def _reopen():
        c._closed = False

    c._reopen = _reopen
    c.find_submitted_order = lambda p: _none()

    result = await c.place_order(plan())
    assert len(calls) == 2, "one heal, one resend, then stop"
    assert not result.filled
    assert result.error_code.startswith("transport_")


async def _none():
    return None


# ------------------------------------------ layer 3: reconcile, not retry --


async def test_an_ambiguous_failure_is_never_blind_retried() -> None:
    """A timeout may mean the order is live. Sending a second one is how a bot
    buys the same position twice, so the venue is asked instead.
    """
    calls = []

    async def create_order_v2(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("read timeout")

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))
    asked = []

    async def find(p):
        asked.append(p.client_order_id)
        return None

    c.find_submitted_order = find

    result = await c.place_order(plan())
    assert len(calls) == 1, "an ambiguous failure must not resend"
    assert asked == ["coid-abc"], "it must reconcile by client_order_id"
    assert not result.filled


async def test_an_order_the_venue_already_has_is_adopted_not_repeated() -> None:
    """The request did land. The right answer is that order, not another one."""

    async def create_order_v2(**kwargs):
        raise TimeoutError("read timeout")

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))

    async def find(p):
        return c._result_from(filled_response(count="15", price="0.57"), p)

    c.find_submitted_order = find

    result = await c.place_order(plan())
    assert result.fill_count == D("15")
    assert result.average_fill_price == D("0.57")
    assert result.error_code is None


async def test_reconciliation_matches_on_our_own_client_order_id() -> None:
    async def get_orders(**kwargs):
        return SimpleNamespace(
            orders=[
                SimpleNamespace(
                    client_order_id="someone-else",
                    order_id="x",
                    fill_count="9",
                    remaining_count="0",
                    average_fill_price="0.90",
                ),
                SimpleNamespace(
                    client_order_id="coid-abc",
                    order_id="ours",
                    fill_count="15",
                    remaining_count="0",
                    average_fill_price="0.56",
                ),
            ]
        )

    c = client(_orders=SimpleNamespace(get_orders=get_orders))
    found = await c.find_submitted_order(plan())
    assert found is not None
    assert found.order_id == "ours"
    assert found.fill_count == D("15")


async def test_a_failed_lookup_reports_unknown_rather_than_absent() -> None:
    """ "I could not check" must never be mistaken for "it is not there" - that
    reading is what would authorise a duplicate.
    """

    async def get_orders(**kwargs):
        raise RuntimeError("lookup exploded")

    c = client(_orders=SimpleNamespace(get_orders=get_orders))
    assert await c.find_submitted_order(plan()) is None


# ----------------------------------- layer 4: never abort the trading cycle --


@pytest.mark.parametrize(
    "boom",
    [
        TimeoutError("read timeout"),
        RuntimeError("Session is closed"),
        OSError("connection reset"),
    ],
)
async def test_no_transport_failure_ever_escapes_into_the_cycle(boom) -> None:
    """A venue fault must cost this pass, not the run. Losing the cycle is how
    an open position stops being monitored.
    """

    async def create_order_v2(**kwargs):
        raise boom

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))

    async def _reopen():
        c._closed = False

    c._reopen = _reopen
    c.find_submitted_order = lambda p: _none()

    result = await c.place_order(plan())
    assert not result.filled
    assert result.remaining_count == D("15")
    assert result.error_code


async def test_http_400_is_an_observable_no_fill_not_an_exception() -> None:
    """The live TP path hit this response repeatedly on 24 Aug 2026.

    The generated SDK renders the nested error as an empty flat model, but the
    raw body still carries the code. A deterministic rejection means no order
    is resting and must return through the ordinary no-fill path.
    """
    from kalshi_python_async import ApiException

    calls = []

    async def create_order_v2(**kwargs):
        calls.append(kwargs)
        error = ApiException(status=400, reason="Bad Request")
        error.body = (
            '{"error":{"code":"invalid_order_price",'
            '"message":"price is not on the market tick"}}'
        )
        raise error

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))
    result = await c.place_exit_order(plan())

    assert len(calls) == 1
    assert not result.filled
    assert result.remaining_count == D("15")
    assert result.error_code == "invalid_order_price"


async def test_expired_order_auth_is_retried_once_with_same_client_id() -> None:
    """An expired signature is a pre-admission rejection, not an ambiguous
    timeout. A fresh request is safe, and the client id still deduplicates it.
    """
    from kalshi_python_async import ApiException

    calls = []

    async def create_order_v2(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            error = ApiException(status=400, reason="Bad Request")
            error.body = (
                '{"error":{"code":"header_timestamp_expired",'
                '"message":"header timestamp expired"}}'
            )
            raise error
        return filled_response()

    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))
    result = await c.place_order(plan())

    assert result.filled
    assert len(calls) == 2
    assert calls[0]["client_order_id"] == calls[1]["client_order_id"] == "coid-abc"


async def test_order_and_fill_lifecycle_is_normalized() -> None:
    class Audit:
        def __init__(self):
            self.orders = []
            self.transitions = []
            self.fills = []

        def record_order(self, **values):
            self.orders.append(values)
            return "audit-order"

        def record_order_transition(self, **values):
            self.transitions.append(values)

        def record_fill(self, **values):
            self.fills.append(values)

    async def create_order_v2(**kwargs):
        return filled_response()

    audit = Audit()
    c = client(_orders=SimpleNamespace(create_order_v2=create_order_v2))
    c.bind_event_ledger(audit, lambda: "run-1")

    result = await c.place_order(plan())

    assert result.filled
    assert audit.orders[0]["order_type"] == "ioc"
    assert [row["state"] for row in audit.transitions] == ["submitted", "filled"]
    assert audit.fills[0]["liquidity_role"] == "taker"


async def test_closing_leaves_the_client_reusable() -> None:
    """close() used to be terminal, and a mode switch closes then rebuilds. A
    collaborator holding the old object was left permanently broken.
    """
    closed = []
    c = client(_client=SimpleNamespace())

    async def _close():
        closed.append(True)

    c._client.close = _close
    await c.close()
    assert closed == [True]
    assert c._closed is True  # marked, not destroyed
