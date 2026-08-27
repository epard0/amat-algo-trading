from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from config import KalshiSettings
from execution.kalshi_client import (
    AsyncKalshiTradingClient,
    BookQuote,
    KalshiMarket,
    OrderPlan,
    quote_from_orderbook,
)


def settings(tmp_path: Path, **overrides) -> KalshiSettings:
    base = KalshiSettings(
        environment="demo",
        rest_url="https://example.invalid/trade-api/v2",
        websocket_url="wss://example.invalid/trade-api/ws/v2",
        api_key_id=None,
        private_key_path=tmp_path / "private.pem",
        public_key_path=tmp_path / "public.pem",
        subaccount=0,
        enable_live_trading=False,
        live_capital_limit_usd=Decimal("10"),
        trade_budget_usd=Decimal("10"),
        position_fraction=Decimal("0.20"),
        paper_balance_usd=Decimal("100"),
        poll_interval_seconds=5,
        entry_window_seconds=180,
        minimum_seconds_to_close=20,
        min_probability=Decimal("0.62"),
        min_edge=Decimal("0.05"),
        max_slippage=Decimal("0.01"),
        fee_reserve_per_contract=Decimal("0.02"),
        assets=("BTC",),
        ledger_path=tmp_path / "ledger.db",
        trade_log_path=tmp_path / "trades.jsonl",
        status_check_lead_seconds=5,
        settlement_poll_seconds=2,
        # Flat sizing by default keeps these cases about budgeting, not Kelly.
        kelly_fraction=Decimal("0"),
        min_contracts=Decimal("0.01"),
    )
    return replace(base, **overrides) if overrides else base


def market() -> KalshiMarket:
    now = datetime.now(timezone.utc)
    return KalshiMarket(
        asset="BTC",
        ticker="KXBTC15M-TEST",
        event_ticker="KXBTC15M",
        title="BTC price up in next 15 mins?",
        target_price=Decimal("100000"),
        open_time=now - timedelta(minutes=10),
        close_time=now + timedelta(minutes=5),
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.60"),
        yes_bid_size=Decimal("100"),
        yes_ask_size=Decimal("100"),
        fee_multiplier=Decimal("1"),
    )


def client(tmp_path: Path, **overrides) -> AsyncKalshiTradingClient:
    """Build the client without opening a session or authenticating."""
    instance = object.__new__(AsyncKalshiTradingClient)
    instance.settings = settings(tmp_path, **overrides)
    instance.authenticated = True
    return instance


def quote(
    outcome: str = "yes",
    best: str = "0.60",
    depth: str = "1000",
    slippage: str = "0.01",
) -> BookQuote:
    best_price = Decimal(best)
    return BookQuote(
        outcome=outcome,
        best_price=best_price,
        limit_price=min(Decimal("0.99"), best_price + Decimal(slippage)),
        depth_at_limit=Decimal(depth),
    )


def test_yes_order_uses_bid_and_fixed_budget(tmp_path: Path) -> None:
    plan = client(tmp_path).build_order_plan(
        market=market(),
        quote=quote("yes"),
        available_balance=Decimal("100"),
        model_probability=Decimal("0.75"),
        maximum_budget=Decimal("10"),
    )

    assert plan.sdk_book_side == "bid"
    assert plan.sdk_yes_leg_price == Decimal("0.6100")
    assert plan.maximum_total_debit <= Decimal("10")
    assert plan.maximum_total_debit > Decimal("9.98")


def test_order_plan_preserves_market_exchange_index(tmp_path: Path) -> None:
    indexed_market = replace(market(), exchange_index=2)

    plan = client(tmp_path).build_order_plan(
        market=indexed_market,
        quote=quote("yes"),
        available_balance=Decimal("10.52"),
        model_probability=Decimal("0.75"),
    )

    assert plan.exchange_index == 2
    assert plan.maximum_total_debit <= Decimal("10.52")


def test_order_plan_reserves_multiplier_adjusted_fees(tmp_path: Path) -> None:
    high_fee_market = replace(market(), fee_multiplier=Decimal("2"))

    plan = client(tmp_path).build_order_plan(
        market=high_fee_market,
        quote=quote("yes"),
        available_balance=Decimal("10.52"),
        model_probability=Decimal("0.75"),
    )

    actual_fee_per_contract = (
        Decimal("0.07")
        * high_fee_market.fee_multiplier
        * plan.outcome_limit_price
        * (Decimal("1") - plan.outcome_limit_price)
    )
    assert plan.fee_reserve >= plan.contracts * actual_fee_per_contract
    assert plan.maximum_total_debit <= Decimal("10.52")


def test_paper_sizing_uses_the_same_fixed_budget_as_live(
    tmp_path: Path,
) -> None:
    plan = client(tmp_path).build_order_plan(
        market=market(),
        quote=quote("yes"),
        available_balance=Decimal("100"),
        model_probability=Decimal("0.75"),
        maximum_budget=None,
    )

    assert plan.maximum_total_debit <= Decimal("10")
    assert plan.maximum_total_debit > Decimal("9.98")


def test_no_order_uses_ask_and_complement_price(tmp_path: Path) -> None:
    plan = client(tmp_path).build_order_plan(
        market=market(),
        quote=quote("no", best="0.60"),
        available_balance=Decimal("25"),
        model_probability=Decimal("0.75"),
    )

    # The side changes wire encoding, never the fixed $10 risk budget.
    assert plan.sdk_book_side == "ask"
    assert plan.outcome_limit_price == Decimal("0.6100")
    assert plan.sdk_yes_leg_price == Decimal("0.3900")
    assert plan.maximum_total_debit <= Decimal("10")
    assert plan.maximum_total_debit > Decimal("9.98")


def test_resting_depth_reduces_contract_count(tmp_path: Path) -> None:
    plan = client(tmp_path).build_order_plan(
        market=market(),
        quote=quote("yes", depth="2.50"),
        available_balance=Decimal("100"),
        model_probability=Decimal("0.75"),
    )

    assert plan.contracts == Decimal("2.50")
    assert plan.maximum_cost == Decimal("1.5250")
    assert plan.maximum_total_debit == Decimal("1.5850")


def test_order_is_refused_when_depth_is_below_minimum(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="MIN_CONTRACTS"):
        client(tmp_path, min_contracts=Decimal("1")).build_order_plan(
            market=market(),
            quote=quote("yes", depth="0.40"),
            available_balance=Decimal("100"),
            model_probability=Decimal("0.75"),
        )


def test_probability_and_kelly_no_longer_scale_stake(tmp_path: Path) -> None:
    thin = client(tmp_path, kelly_fraction=Decimal("0.25")).build_order_plan(
        market=market(),
        quote=quote("yes", best="0.60"),
        available_balance=Decimal("100"),
        model_probability=Decimal("0.66"),
    )
    fat = client(tmp_path, kelly_fraction=Decimal("0.25")).build_order_plan(
        market=market(),
        quote=quote("yes", best="0.60"),
        available_balance=Decimal("100"),
        model_probability=Decimal("0.90"),
    )

    assert thin.sizing_fraction == fat.sizing_fraction == Decimal("0.10")
    assert thin.maximum_total_debit == fat.maximum_total_debit


def test_quote_from_orderbook_maps_opposite_side_and_aggregates_depth() -> None:
    # Buying NO lifts the resting YES bids: a YES bid at 0.87 is a NO ask at 0.13.
    yes_bids = [["0.87", "5.00"], ["0.86", "7.00"], ["0.80", "50.00"]]
    no_bids = [["0.12", "9.00"]]

    buy_no = quote_from_orderbook("no", yes_bids, no_bids, max_slippage=Decimal("0.01"))
    assert buy_no is not None
    assert buy_no.best_price == Decimal("0.13")
    assert buy_no.limit_price == Decimal("0.14")
    # Limit 0.14 can lift YES bids at or above 0.86 only.
    assert buy_no.depth_at_limit == Decimal("12.00")

    buy_yes = quote_from_orderbook(
        "yes", yes_bids, no_bids, max_slippage=Decimal("0.01")
    )
    assert buy_yes is not None
    assert buy_yes.best_price == Decimal("0.88")
    assert buy_yes.depth_at_limit == Decimal("9.00")


def test_quote_from_orderbook_returns_none_for_empty_side() -> None:
    assert quote_from_orderbook("no", [], [["0.10", "5"]], Decimal("0.01")) is None


async def test_place_order_uses_immediate_or_cancel(tmp_path: Path) -> None:
    captured = {}

    class Response:
        order_id = "order-123"
        client_order_id = "client-123"
        fill_count = "3.00"
        remaining_count = "0.00"
        average_fill_price = "0.6100"

    class FakeOrders:
        async def create_order_v2(self, **kwargs):
            captured.update(kwargs)
            return Response()

    instance = client(tmp_path)
    instance._orders = FakeOrders()
    plan = OrderPlan(
        ticker="KXBTC15M-TEST",
        outcome="yes",
        sdk_book_side="bid",
        sdk_yes_leg_price=Decimal("0.6100"),
        outcome_limit_price=Decimal("0.6100"),
        contracts=Decimal("3.00"),
        maximum_cost=Decimal("1.8300"),
        fee_reserve=Decimal("0.0700"),
        maximum_total_debit=Decimal("1.9000"),
        client_order_id="client-123",
        exchange_index=2,
    )

    result = await instance.place_order(plan)

    assert captured["side"] == "bid"
    assert captured["time_in_force"] == "immediate_or_cancel"
    assert captured["self_trade_prevention_type"] == "taker_at_cross"
    assert captured["subaccount"] == 0
    assert captured["exchange_index"] == 2
    assert result.fill_count == Decimal("3.00")
    assert result.filled is True


async def test_entry_is_suppressed_when_indexed_balance_changed_before_send(
    tmp_path: Path,
) -> None:
    submitted = []
    checked = []

    class FakeOrders:
        async def create_order_v2(self, **kwargs):
            submitted.append(kwargs)
            raise AssertionError("an over-balance order reached the venue")

    async def balance(exchange_index):
        checked.append(exchange_index)
        return Decimal("1.50")

    instance = client(tmp_path)
    instance._orders = FakeOrders()
    instance._portfolio = object()
    instance.get_available_balance = balance
    plan = OrderPlan(
        ticker="KXBTC15M-INDEXED",
        outcome="yes",
        sdk_book_side="bid",
        sdk_yes_leg_price=Decimal("0.6100"),
        outcome_limit_price=Decimal("0.6100"),
        contracts=Decimal("3.00"),
        maximum_cost=Decimal("1.8300"),
        fee_reserve=Decimal("0.0700"),
        maximum_total_debit=Decimal("1.9000"),
        client_order_id="client-indexed",
        exchange_index=2,
    )

    result = await instance.place_order(plan)

    assert checked == [2]
    assert submitted == []
    assert result.error_code == "insufficient_indexed_balance"


async def test_exit_order_is_ioc_and_reduce_only(tmp_path: Path) -> None:
    captured = {}

    class Response:
        order_id = "exit-123"
        client_order_id = "exit-client"
        fill_count = "3.00"
        remaining_count = "0.00"
        average_fill_price = "0.7000"

    class FakeOrders:
        async def create_order_v2(self, **kwargs):
            captured.update(kwargs)
            return Response()

    instance = client(tmp_path)
    instance._orders = FakeOrders()
    plan = OrderPlan(
        ticker="KXBTC15M-TEST",
        outcome="yes",
        sdk_book_side="ask",
        sdk_yes_leg_price=Decimal("0.7000"),
        outcome_limit_price=Decimal("0.7000"),
        contracts=Decimal("3.00"),
        maximum_cost=Decimal("2.1000"),
        fee_reserve=Decimal("0.0400"),
        maximum_total_debit=Decimal("2.1000"),
        client_order_id="exit-client",
    )

    result = await instance.place_exit_order(plan)

    assert captured["time_in_force"] == "immediate_or_cancel"
    assert captured["reduce_only"] is True
    assert captured["exchange_index"] == 0
    assert result.fill_count == Decimal("3.00")
    assert result.filled is True


async def test_resting_order_uses_current_v2_enum_values(tmp_path: Path) -> None:
    captured = {}

    class Response:
        order_id = "maker-123"
        client_order_id = "maker-client"
        fill_count = "0.00"
        remaining_count = "3.00"
        average_fill_price = None

    class FakeOrders:
        async def create_order_v2(self, **kwargs):
            captured.update(kwargs)
            return Response()

    instance = client(tmp_path)
    instance._orders = FakeOrders()
    plan = OrderPlan(
        ticker="KXBTC15M-TEST",
        outcome="yes",
        sdk_book_side="bid",
        sdk_yes_leg_price=Decimal("0.7000"),
        outcome_limit_price=Decimal("0.7000"),
        contracts=Decimal("3.00"),
        maximum_cost=Decimal("2.1000"),
        fee_reserve=Decimal("0.0400"),
        maximum_total_debit=Decimal("2.1400"),
        client_order_id="maker-client",
    )

    await instance.place_resting_order(plan)

    assert captured["time_in_force"] == "good_till_canceled"
    assert captured["self_trade_prevention_type"] == "maker"
    assert captured["post_only"] is True
    assert captured["exchange_index"] == 0


async def test_insufficient_resting_volume_is_a_no_fill_not_a_crash(
    tmp_path: Path,
) -> None:
    from kalshi_python_async import ApiException

    class FakeOrders:
        async def create_order_v2(self, **kwargs):
            error = ApiException(status=409, reason="Conflict")
            error.body = (
                '{"error":{"code":"fill_or_kill_insufficient_resting_volume",'
                '"message":"fill or kill insufficient resting volume"}}'
            )
            raise error

    instance = client(tmp_path)
    instance._orders = FakeOrders()
    plan = OrderPlan(
        ticker="KXETH15M-TEST",
        outcome="no",
        sdk_book_side="ask",
        sdk_yes_leg_price=Decimal("0.8600"),
        outcome_limit_price=Decimal("0.1400"),
        contracts=Decimal("36.00"),
        maximum_cost=Decimal("5.0400"),
        fee_reserve=Decimal("0.7300"),
        maximum_total_debit=Decimal("5.7700"),
        client_order_id="client-456",
    )

    result = await instance.place_order(plan)

    assert result.filled is False
    assert result.error_code == "fill_or_kill_insufficient_resting_volume"
