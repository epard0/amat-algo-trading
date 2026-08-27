from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.strategy_brain.strategies.orderbook import analyse
from core.strategy_brain.strategies.salvage import OutcomeBook
from core.strategy_brain.strategies.take_profit import decide_take_profit
from execution.kalshi_client import OrderResult
from execution.position_book import PositionBook
from execution.take_profit import TakeProfitExit
from execution.trade_ledger import TradeLedger


D = Decimal
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def book(*, bid="0.70", ask="0.72", depth="100") -> OutcomeBook:
    return OutcomeBook(
        outcome="yes",
        bid=D(bid),
        ask=D(ask),
        depth=D(depth),
        yes_leg_price=D(bid),
        book_side="ask",
    )


def decide(**overrides):
    values = {
        "book": book(),
        "contracts": D("10"),
        "total_debit": D("6.20"),
        "exit_fee_per_contract": D("0.02"),
        "minimum_gain_per_contract": D("0.05"),
        "maximum_spread": D("0.03"),
        "seconds_to_close": D("120"),
        "minimum_seconds_to_close": D("30"),
    }
    values.update(overrides)
    return decide_take_profit(**values)


def test_executable_net_gain_triggers_a_complete_exit() -> None:
    result = decide()
    assert result.should_exit
    assert result.gain_per_contract == D("0.06")


def test_unrealized_price_move_below_fees_and_threshold_is_held() -> None:
    result = decide(book=book(bid="0.68", ask="0.70"))
    assert not result.should_exit
    assert result.gate == "capture"


def test_small_silver_gain_is_held_until_most_settlement_upside_is_captured() -> None:
    result = decide(
        book=book(bid="0.80", ask="0.83"),
        contracts=D("2"),
        total_debit=D("1.47"),
        exit_fee_per_contract=D("0.0056"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.84"),
        confidence_margin=D("0.02"),
        seconds_to_close=D("401"),
    )
    assert not result.should_exit
    assert result.gate == "capture"
    assert result.gain_per_contract == D("0.0594")
    assert result.required_gain_per_contract == D("0.19875")


def test_adaptive_exit_fires_after_capturing_upside_and_clearing_confidence() -> None:
    result = decide(
        book=book(bid="0.95", ask="0.97"),
        contracts=D("2"),
        total_debit=D("1.47"),
        exit_fee_per_contract=D("0.004"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.925"),
        confidence_margin=D("0.02"),
    )
    assert result.should_exit
    assert result.gain_per_contract == D("0.211")


def test_model_confidence_can_hold_after_capture_target_is_met() -> None:
    result = decide(
        book=book(bid="0.80", ask="0.82"),
        total_debit=D("7.0"),
        minimum_capture_fraction=D("0"),
        model_probability=D("0.85"),
        confidence_margin=D("0.02"),
    )
    assert not result.should_exit
    assert result.gate == "confidence"


@pytest.mark.parametrize(
    ("bid", "probability", "seconds", "contracts", "total_debit"),
    [
        ("0.965", "0.7008", "212.8", "13", "9.93"),
        ("0.939", "0.8730", "107.7", "12", "8.35"),
    ],
)
def test_recent_btc_winners_are_explicitly_held(
    bid: str,
    probability: str,
    seconds: str,
    contracts: str,
    total_debit: str,
) -> None:
    result = decide(
        book=book(bid=bid, ask=str(D(bid) + D("0.02"))),
        contracts=D(contracts),
        total_debit=D(total_debit),
        minimum_capture_fraction=D("0.75"),
        model_probability=D(probability),
        confidence_margin=D("0.02"),
        seconds_to_close=D(seconds),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.90"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("300"),
    )
    assert not result.should_exit
    assert result.gate == "winning_hold"


def test_winning_hold_releases_on_confirmed_probability_reversal() -> None:
    result = decide(
        book=book(bid="0.945", ask="0.965"),
        total_debit=D("7.00"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.49"),
        confidence_margin=D("0.02"),
        seconds_to_close=D("120"),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.90"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("300"),
    )
    assert result.should_exit
    assert result.gate == "take_profit"


def test_zero_winning_hold_window_protects_the_entire_contract_lifetime() -> None:
    result = decide(
        book=book(bid="0.945", ask="0.965"),
        total_debit=D("7.00"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.80"),
        confidence_margin=D("0.02"),
        seconds_to_close=D("700"),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.90"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("0"),
    )
    assert not result.should_exit
    assert result.gate == "winning_hold"


def test_positive_winning_hold_window_remains_an_explicit_opt_out() -> None:
    result = decide(
        book=book(bid="0.945", ask="0.965"),
        total_debit=D("7.00"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.80"),
        confidence_margin=D("0.02"),
        seconds_to_close=D("301"),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.90"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("300"),
    )
    assert result.should_exit
    assert result.gate == "take_profit"


def test_strong_executable_book_holds_when_model_lags() -> None:
    result = decide(
        book=book(bid="0.96", ask="0.98"),
        total_debit=D("7.00"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.40"),
        confidence_margin=D("0.02"),
        seconds_to_close=D("600"),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.90"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("0"),
    )
    assert not result.should_exit
    assert result.gate == "winning_hold"
    assert "strong executable bid" in result.reason


def test_eighty_five_cent_book_holds_only_with_model_confirmation() -> None:
    result = decide(
        book=book(bid="0.86", ask="0.88"),
        total_debit=D("7.00"),
        minimum_capture_fraction=D("0"),
        model_probability=D("0.70"),
        confidence_margin=D("0.02"),
        seconds_to_close=D("600"),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.85"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("0"),
    )
    assert not result.should_exit
    assert result.gate == "winning_hold"
    assert "and model" in result.reason


@pytest.mark.parametrize(
    ("asset", "bid", "probability", "seconds", "contracts", "total_debit", "fee"),
    [
        ("GOLD", "0.965", "0.791465", "315.769", "37", "30.73", "0.0024"),
        ("SILVER", "0.934", "0.845629", "315.617", "23", "16.44", "0.0044"),
    ],
)
def test_recent_commodity_winners_are_held_instead_of_clipped(
    asset: str,
    bid: str,
    probability: str,
    seconds: str,
    contracts: str,
    total_debit: str,
    fee: str,
) -> None:
    result = decide(
        book=book(bid=bid, ask=str(D(bid) + D("0.02"))),
        contracts=D(contracts),
        total_debit=D(total_debit),
        exit_fee_per_contract=D(fee),
        minimum_capture_fraction=D("0.75"),
        model_probability=D(probability),
        confidence_margin=D("0.02"),
        seconds_to_close=D(seconds),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.90"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("0"),
    )
    assert not result.should_exit, asset
    assert result.gate == "winning_hold", asset


def test_winning_hold_cannot_exit_while_waiting_for_second_confirmation() -> None:
    first = decide(
        book=book(bid="0.95", ask="0.97"),
        total_debit=D("7.00"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.80"),
        seconds_to_close=D("120"),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.90"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("300"),
        winning_hold_confirmation_count=1,
        winning_hold_required_confirmations=2,
    )
    second = decide(
        book=book(bid="0.95", ask="0.97"),
        total_debit=D("7.00"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.80"),
        seconds_to_close=D("120"),
        winning_hold_enabled=True,
        winning_hold_min_bid=D("0.90"),
        winning_hold_min_probability=D("0.65"),
        winning_hold_strong_book_bid=D("0.95"),
        winning_hold_max_seconds_to_close=D("300"),
        winning_hold_confirmation_count=2,
        winning_hold_required_confirmations=2,
    )
    assert (first.should_exit, first.gate) == (False, "winning_confirmation")
    assert (second.should_exit, second.gate) == (False, "winning_hold")


def test_confirmed_reversal_cannot_exit_below_cost_basis() -> None:
    result = decide(
        book=book(bid="0.40", ask="0.42"),
        total_debit=D("6.20"),
        model_probability=D("0.40"),
        confirmed_reversal=True,
        confirmed_reversal_reason="4/5 signals confirmed twice",
    )
    assert not result.should_exit
    assert result.gate == "principal_protection"
    assert result.gain_per_contract < 0


def test_silver_incident_is_held_despite_confirmed_reversal() -> None:
    """Regression for KXSILVER15M-26AUG251400-00 on 2026-08-25.

    The old override sold 35 YES at 0.49, realizing -$11.8625.  Kalshi later
    finalized YES.  The exact executable economics must now be a hard hold.
    """
    result = decide(
        book=book(bid="0.49", ask="0.50", depth="2379.97"),
        contracts=D("35"),
        position_contracts=D("35"),
        total_debit=D("28.40"),
        exit_fee_per_contract=D("0.0175"),
        minimum_gain_per_contract=D("0.05"),
        minimum_capture_fraction=D("0.75"),
        model_probability=D("0.47690123686935243"),
        seconds_to_close=D("379.643"),
        confirmed_reversal=True,
        confirmed_reversal_reason=(
            "4/5 deterioration signals confirmed 2/2 times"
        ),
    )
    assert not result.should_exit
    assert result.gate == "principal_protection"
    assert result.gain_per_contract == D("-0.3389285714285714285714285714")
    assert result.gain_per_contract * D("35") == D("-11.8625")


def test_partial_position_uses_original_all_in_cost_basis() -> None:
    result = decide(
        book=book(bid="0.75", ask="0.77"),
        contracts=D("6"),
        position_contracts=D("10"),
        total_debit=D("6.20"),
        exit_fee_per_contract=D("0.02"),
        minimum_capture_fraction=D("0"),
        confidence_margin=D("1"),
    )
    assert result.should_exit
    assert result.cost_basis == D("0.62")
    assert result.gain_per_contract == D("0.11")


def test_wide_books_are_blocked_but_shallow_books_allow_residual_ioc() -> None:
    assert decide(book=book(ask="0.75")).gate == "spread"
    assert decide(book=book(depth="9.99")).should_exit
    assert decide(book=book(depth="0")).gate == "depth"


async def test_partial_ioc_exit_keeps_residual_open_and_releases_capital(
    ledger,
) -> None:
    positions = PositionBook(max_concurrent={"predictor": 1})
    trade = open_runtime_trade(ledger)
    reservation = await positions.reserve(
        strategy=trade.strategy, ticker=trade.ticker, amount=trade.total_debit
    )
    assert reservation is not None
    await positions.confirm(
        reservation, trade_id=trade.trade_id, actual_amount=trade.total_debit
    )
    partial = OrderResult(
        order_id="partial",
        client_order_id="partial-client",
        fill_count=D("4"),
        remaining_count=D("6"),
        average_fill_price=D("0.70"),
    )
    runtime = TakeProfitExit(
        settings=ImmediateSettings(),
        client=Client(runtime_book(), partial),
        ledger=ledger,
        decisions=Decisions(),
        positions=positions,
        mode="live",
        live=True,
        now=lambda: NOW,
    )

    assert not await runtime.evaluate(trade)
    remaining = ledger.get_trade(trade.trade_id)
    assert remaining.status == "OPEN"
    assert remaining.remaining_contracts == D("6")
    assert remaining.exit_contracts == D("4")
    assert positions.open_count() == 1
    assert positions.committed() == trade.total_debit * D("0.6")


def test_too_late_to_reliably_exit_is_held() -> None:
    result = decide(seconds_to_close=D("20"))
    assert not result.should_exit
    assert result.gate == "time"


class Settings:
    environment = "production"
    take_profit_per_contract = D("0.05")
    take_profit_min_capture_fraction = D("0.75")
    take_profit_confidence_margin = D("0.02")
    take_profit_winning_hold_enabled = True
    take_profit_winning_hold_min_bid = D("0.85")
    take_profit_winning_hold_min_probability = D("0.65")
    take_profit_winning_hold_strong_book_bid = D("0.95")
    take_profit_winning_hold_max_seconds_to_close = 0
    confirmed_reversal_enabled = False
    take_profit_max_spread = D("0.03")
    take_profit_min_seconds_to_close = 30
    take_profit_check_interval_seconds = 15
    take_profit_fast_window_seconds = 120
    take_profit_fast_check_seconds = 5


class ImmediateSettings(Settings):
    """Isolate partial-fill/accounting tests from adaptive policy thresholds."""

    take_profit_min_capture_fraction = D("0")
    take_profit_confidence_margin = D("1")
    take_profit_winning_hold_enabled = False


class ReversalDiagnosticSettings(Settings):
    confirmed_reversal_enabled = True
    confirmed_reversal_confirmations = 2
    confirmed_reversal_min_signals = 3
    confirmed_reversal_probability_max = D("0.48")
    confirmed_reversal_peak_drawdown = D("0.15")


class Decisions:
    def __init__(self) -> None:
        self.events = []

    def record_event(self, name, **fields) -> None:
        self.events.append((name, fields))


class Client:
    def __init__(self, analysis, fill=None) -> None:
        self.analysis = analysis
        self.fill = fill
        self.exit_orders = []

    async def get_book_analysis(self, ticker: str):
        return self.analysis

    async def place_exit_order(self, plan):
        self.exit_orders.append(plan)
        return self.fill or OrderResult(
            order_id="exit",
            client_order_id=plan.client_order_id,
            fill_count=plan.contracts,
            remaining_count=D("0"),
            average_fill_price=plan.sdk_yes_leg_price,
        )


@pytest.fixture
def ledger(tmp_path: Path) -> TradeLedger:
    return TradeLedger(tmp_path / "take-profit.db", D("100"))


def runtime_book(yes_bid="0.70", yes_ask="0.72"):
    return analyse(
        [(D(yes_bid), D("100"))],
        [(D("1") - D(yes_ask), D("100"))],
    )


def open_runtime_trade(ledger: TradeLedger, outcome="yes", strategy="predictor"):
    return ledger.record_open_trade(
        mode="paper",
        environment="production",
        strategy=strategy,
        asset="BTC",
        ticker="KXBTC15M-PROFIT",
        event_ticker="KXBTC15M",
        order_id="entry",
        client_order_id="entry-client",
        outcome=outcome,
        placed_at=NOW - timedelta(minutes=5),
        market_open_time=NOW - timedelta(minutes=10),
        market_close_time=NOW + timedelta(minutes=3),
        status_check_time=NOW + timedelta(minutes=3),
        contracts=D("10"),
        entry_price=D("0.60"),
        entry_cost=D("6"),
        fees_paid=D("0.20"),
        total_debit=D("6.20"),
        target_price=D("100000"),
        model_probability=D("0.72"),
        model_edge=D("0.10"),
        metadata={"fee_multiplier": "1"},
    )


async def test_standard_scalp_take_profit_records_realized_exit_and_frees_position(
    ledger,
) -> None:
    positions = PositionBook(max_concurrent={"scalper": 1})
    trade = open_runtime_trade(ledger, strategy="scalper")
    reservation = await positions.reserve(
        strategy=trade.strategy, ticker=trade.ticker, amount=trade.total_debit
    )
    assert reservation is not None
    await positions.confirm(
        reservation, trade_id=trade.trade_id, actual_amount=trade.total_debit
    )
    decisions = Decisions()
    runtime = TakeProfitExit(
        settings=ImmediateSettings(),
        client=Client(runtime_book("0.93", "0.95")),
        ledger=ledger,
        decisions=decisions,
        positions=positions,
        mode="paper",
        live=False,
        now=lambda: NOW,
    )

    assert await runtime.run([trade]) == 1

    exited = ledger.get_trade(trade.trade_id)
    assert exited.status == "EXITED"
    assert exited.exchange_status == "take_profit"
    assert exited.exit_price == D("0.93")
    assert exited.pnl > 0
    assert positions.open_count() == 0
    assert decisions.events[-1][0] == "take_profit_exit"


async def test_live_no_take_profit_maps_yes_leg_fill_to_no_price(ledger) -> None:
    trade = open_runtime_trade(ledger, outcome="no")
    fill = OrderResult(
        order_id="exit",
        client_order_id="exit-client",
        fill_count=D("10"),
        remaining_count=D("0"),
        average_fill_price=D("0.30"),
    )
    client = Client(runtime_book(yes_bid="0.28", yes_ask="0.30"), fill)
    runtime = TakeProfitExit(
        settings=ImmediateSettings(),
        client=client,
        ledger=ledger,
        decisions=Decisions(),
        positions=PositionBook(),
        mode="live",
        live=True,
        now=lambda: NOW,
    )

    assert await runtime.run([trade]) == 1
    assert client.exit_orders[0].sdk_book_side == "bid"
    assert ledger.get_trade(trade.trade_id).exit_price == D("0.70")


async def test_open_position_is_reassessed_on_slow_then_fast_cadence(ledger) -> None:
    trade = open_runtime_trade(ledger)
    clock = [NOW]
    runtime = TakeProfitExit(
        settings=Settings(),
        client=Client(runtime_book("0.63", "0.65")),
        ledger=ledger,
        decisions=Decisions(),
        positions=PositionBook(),
        mode="paper",
        live=False,
        now=lambda: clock[0],
    )

    await runtime.run([trade])
    await runtime.run([trade])
    assert runtime.evaluations == 1
    clock[0] += timedelta(seconds=14)
    await runtime.run([trade])
    assert runtime.evaluations == 1
    clock[0] += timedelta(seconds=1)
    await runtime.run([trade])
    assert runtime.evaluations == 2

    # Move into the final 120 seconds: checks tighten from 15s to 5s.
    clock[0] = trade.market_close_time - timedelta(seconds=100)
    await runtime.run([trade])
    clock[0] += timedelta(seconds=5)
    await runtime.run([trade])
    assert runtime.evaluations == 4


async def test_runtime_reversal_requires_sub_50_executable_bid(ledger) -> None:
    trade = open_runtime_trade(ledger)
    runtime = TakeProfitExit(
        settings=Settings(),
        client=Client(runtime_book("0.70", "0.72")),
        ledger=ledger,
        decisions=Decisions(),
        positions=PositionBook(),
        mode="paper",
        live=False,
        now=lambda: NOW,
    )

    # Establish a 70c peak, then create four adverse model/technical signals.
    assert not await runtime.evaluate(
        trade,
        analysis=runtime_book("0.70", "0.72"),
        model_probability=D("0.80"),
    )
    runtime._predictions[trade.ticker] = SimpleNamespace(
        distance_z=-0.5,
        raw_momentum_return=-0.002,
    )
    for _ in range(2):
        assert not await runtime.evaluate(
            trade,
            analysis=runtime_book("0.55", "0.57"),
            model_probability=D("0.40"),
        )

    assert ledger.get_trade(trade.trade_id).status == "OPEN"
    assert runtime._reversal_streak[trade.trade_id] == 0


async def test_runtime_reversal_never_submits_a_loss_making_order(ledger) -> None:
    trade = open_runtime_trade(ledger, strategy="scalper")
    client = Client(runtime_book("0.84", "0.85"))
    runtime = TakeProfitExit(
        settings=ReversalDiagnosticSettings(),
        client=client,
        ledger=ledger,
        decisions=Decisions(),
        positions=PositionBook(),
        mode="live",
        live=True,
        now=lambda: NOW,
    )
    runtime._predictions[trade.ticker] = SimpleNamespace(
        distance_z=-D("0.5"),
        raw_momentum_return=-D("0.002"),
    )

    # Establish the peak, then repeat the exact adverse observation enough to
    # satisfy the old 2/2 reversal override.
    assert not await runtime.evaluate(
        trade,
        analysis=runtime_book("0.84", "0.85"),
        model_probability=D("0.80"),
    )
    for _ in range(2):
        assert not await runtime.evaluate(
            trade,
            analysis=runtime_book("0.49", "0.50"),
            model_probability=D("0.4769"),
        )

    assert runtime._reversal_streak[trade.trade_id] == 2
    assert ledger.get_trade(trade.trade_id).status == "OPEN"
    assert client.exit_orders == []
    assert runtime.blocks["principal_protection"] >= 1
