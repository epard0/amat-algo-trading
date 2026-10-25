"""Simulated execution, missed-fill policy, and the accounting identity."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from amat.backtest.accounting import SessionAccountant
from amat.config.models import CostConfig, ExecutionConfig, RebalanceConfig
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.bars import ExecutionWindowBar
from amat.domain.fills import Fill, MissedFillReason
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.orders import SimulatedOrder, TradeReason
from amat.domain.positions import Position
from amat.domain.types import Side
from amat.errors import AccountingError, CausalityError
from amat.execution import SimulatedExecutionModel, build_instructions, one_way_cost

SIGNAL = dt.date(2020, 1, 2)
EXECUTION = dt.date(2020, 1, 3)


def window(
    symbol: str = "SPY", *, vwap: str = "100", volume: str = "10000", session: dt.date = EXECUTION
) -> ExecutionWindowBar:
    return ExecutionWindowBar(
        symbol=symbol,
        session_date=session,
        window_start=dt.datetime.combine(session, dt.time(14, 30), tzinfo=dt.UTC),
        window_end=dt.datetime.combine(session, dt.time(14, 35), tzinfo=dt.UTC),
        vwap=Decimal(vwap),
        volume=Decimal(volume),
        source="test",
    )


def instruction(side: Side = Side.BUY, quantity: str = "10", symbol: str = "SPY") -> SimulatedOrder:
    return SimulatedOrder(
        symbol=symbol,
        signal_session=SIGNAL,
        execution_session=EXECUTION,
        side=side,
        quantity=Decimal(quantity),
        reason=TradeReason.WEIGHT_DELTA,
    )


@pytest.fixture
def model() -> SimulatedExecutionModel:
    return SimulatedExecutionModel(ExecutionConfig(), CostConfig())


class TestCosts:
    def test_two_bps_of_notional(self) -> None:
        assert one_way_cost(Decimal("10000"), 2.0) == Decimal("2.000000")

    def test_cost_is_a_magnitude_for_both_sides(self) -> None:
        assert one_way_cost(Decimal("-10000"), 2.0) == Decimal("2.000000")

    def test_zero_cost_is_representable_for_diagnostics(self) -> None:
        assert one_way_cost(Decimal("10000"), 0.0) == Decimal("0.000000")


class TestFillPricing:
    def test_fills_at_the_window_vwap_not_a_daily_price(
        self, model: SimulatedExecutionModel
    ) -> None:
        fills, missed = model.execute([instruction()], {"SPY": window(vwap="101.25")})
        assert not missed
        assert fills[0].price == Decimal("101.25")

    def test_buy_consumes_notional_plus_cost(self, model: SimulatedExecutionModel) -> None:
        fills, _ = model.execute([instruction()], {"SPY": window(vwap="100")})
        fill = fills[0]
        assert fill.gross_notional == Decimal("1000")
        assert fill.cost_cash == Decimal("0.200000")
        assert fill.cash_delta == Decimal("-1000.200000")

    def test_sell_releases_notional_but_still_pays_cost(
        self, model: SimulatedExecutionModel
    ) -> None:
        fills, _ = model.execute([instruction(Side.SELL)], {"SPY": window(vwap="100")})
        assert fills[0].cash_delta == Decimal("999.800000")


class TestMissedFills:
    def test_absent_window_records_no_execution_bar(self, model: SimulatedExecutionModel) -> None:
        fills, missed = model.execute([instruction()], {})
        assert not fills
        assert missed[0].reason is MissedFillReason.NO_EXECUTION_BAR

    def test_zero_volume_window_records_zero_volume(self, model: SimulatedExecutionModel) -> None:
        fills, missed = model.execute([instruction()], {"SPY": window(volume="0")})
        assert not fills
        assert missed[0].reason is MissedFillReason.ZERO_VOLUME

    def test_wrong_session_records_non_session_date(self, model: SimulatedExecutionModel) -> None:
        stale = window(session=dt.date(2020, 1, 6))
        fills, missed = model.execute([instruction()], {"SPY": stale})
        assert not fills
        assert missed[0].reason is MissedFillReason.NON_SESSION_DATE

    def test_no_price_is_ever_synthesized(self, model: SimulatedExecutionModel) -> None:
        """The whole point: a missing window must not borrow a neighbouring price."""
        fills, missed = model.execute([instruction()], {})
        assert fills == []
        assert missed[0].requested_quantity == Decimal("10")

    def test_unfunded_buy_records_insufficient_cash(self, model: SimulatedExecutionModel) -> None:
        fills, missed = model.execute(
            [instruction(quantity="100")],
            {"SPY": window(vwap="100")},
            available_cash=Decimal("500"),
        )
        assert not fills
        assert missed[0].reason is MissedFillReason.INSUFFICIENT_CASH

    def test_sells_settle_before_buys_so_proceeds_fund_purchases(
        self, model: SimulatedExecutionModel
    ) -> None:
        instructions = [
            instruction(Side.BUY, "10", "QQQ"),
            instruction(Side.SELL, "10", "SPY"),
        ]
        windows = {"SPY": window("SPY"), "QQQ": window("QQQ")}
        # The buffer covers only the two one-way costs (0.40 total). The 1000 of
        # purchase notional can therefore only come from the sale proceeds, so
        # the buy filling at all proves the sell settled first.
        fills, missed = model.execute(instructions, windows, available_cash=Decimal("1"))
        assert not missed
        assert [fill.side for fill in fills] == [Side.SELL, Side.BUY]

    def test_buy_is_refused_when_proceeds_cannot_cover_costs(
        self, model: SimulatedExecutionModel
    ) -> None:
        """Sale proceeds are net of cost, so an equal-notional swap needs a buffer."""
        instructions = [
            instruction(Side.BUY, "10", "QQQ"),
            instruction(Side.SELL, "10", "SPY"),
        ]
        windows = {"SPY": window("SPY"), "QQQ": window("QQQ")}
        fills, missed = model.execute(instructions, windows, available_cash=Decimal("0"))
        assert [fill.side for fill in fills] == [Side.SELL]
        assert missed[0].reason is MissedFillReason.INSUFFICIENT_CASH

    def test_entry_participation_cap_partially_fills_and_records_the_remainder(self) -> None:
        capped = SimulatedExecutionModel(
            ExecutionConfig(), CostConfig(), max_entry_window_participation=0.01
        )
        fills, missed = capped.execute(
            [instruction(quantity="250")], {"SPY": window(volume="10000")}
        )
        assert fills[0].quantity == Decimal("100")
        assert fills[0].cost_cash == Decimal("2.000000")
        assert missed[0].requested_quantity == Decimal("150.00")
        assert missed[0].reason is MissedFillReason.PARTICIPATION_CAP

    def test_participation_cap_never_clips_a_risk_reducing_sale(self) -> None:
        capped = SimulatedExecutionModel(
            ExecutionConfig(), CostConfig(), max_entry_window_participation=0.01
        )
        fills, missed = capped.execute(
            [instruction(Side.SELL, quantity="250")], {"SPY": window(volume="10000")}
        )
        assert fills[0].quantity == Decimal("250")
        assert not missed


class TestBuildInstructions:
    def test_small_delta_generates_no_trade(self) -> None:
        instructions, missed = build_instructions(
            {"SPY": 0.505},
            {"SPY": Position("SPY", Decimal("50"), Decimal("100"), SIGNAL)},
            {"SPY": Decimal("100")},
            Decimal("10000"),
            signal_session=SIGNAL,
            execution_session=EXECUTION,
            rebalance=RebalanceConfig(),
            execution=ExecutionConfig(),
        )
        assert not instructions and not missed

    def test_zero_to_nonzero_transition_always_trades(self) -> None:
        instructions, _ = build_instructions(
            {"SPY": 0.001},
            {},
            {"SPY": Decimal("100")},
            Decimal("1000000"),
            signal_session=SIGNAL,
            execution_session=EXECUTION,
            rebalance=RebalanceConfig(),
            execution=ExecutionConfig(),
        )
        assert instructions[0].reason is TradeReason.POSITION_TRANSITION

    def test_below_minimum_notional_is_recorded_not_dropped(self) -> None:
        instructions, missed = build_instructions(
            {"SPY": 0.000001},
            {},
            {"SPY": Decimal("100")},
            Decimal("1000000"),
            signal_session=SIGNAL,
            execution_session=EXECUTION,
            rebalance=RebalanceConfig(),
            execution=ExecutionConfig(),
        )
        assert not instructions
        assert missed[0].reason is MissedFillReason.BELOW_MINIMUM_NOTIONAL

    def test_same_session_execution_is_a_causality_error(self) -> None:
        with pytest.raises(CausalityError, match="strictly after"):
            build_instructions(
                {"SPY": 0.5},
                {},
                {"SPY": Decimal("100")},
                Decimal("10000"),
                signal_session=SIGNAL,
                execution_session=SIGNAL,
                rebalance=RebalanceConfig(),
                execution=ExecutionConfig(),
            )

    def test_an_in_flight_exit_is_not_sold_twice(self) -> None:
        # 50 shares held, and a full-exit sale of 50 already scheduled but not
        # settled (a lag >= 2 situation). A decision still targeting zero must
        # emit nothing -- without netting it would sell the same shares again,
        # which the accountant refuses as a short.
        instructions, missed = build_instructions(
            {"SPY": 0.0},
            {"SPY": Position("SPY", Decimal("50"), Decimal("100"), SIGNAL)},
            {"SPY": Decimal("100")},
            Decimal("10000"),
            signal_session=SIGNAL,
            execution_session=EXECUTION,
            rebalance=RebalanceConfig(),
            execution=ExecutionConfig(),
            in_flight={"SPY": Decimal("-50")},
        )
        assert not instructions and not missed

    def test_an_in_flight_entry_is_not_bought_twice(self) -> None:
        # Nothing held yet, but a 50-share buy is scheduled. A decision whose
        # target that buy already satisfies must emit nothing.
        instructions, missed = build_instructions(
            {"SPY": 0.5},
            {},
            {"SPY": Decimal("100")},
            Decimal("10000"),
            signal_session=SIGNAL,
            execution_session=EXECUTION,
            rebalance=RebalanceConfig(),
            execution=ExecutionConfig(),
            in_flight={"SPY": Decimal("50")},
        )
        assert not instructions and not missed


class TestAccounting:
    @pytest.fixture
    def accountant(self) -> SessionAccountant:
        return SessionAccountant()

    @pytest.fixture
    def opening(self) -> LedgerSnapshot:
        return LedgerSnapshot(
            SIGNAL, Decimal("1000"), {"SPY": Position("SPY", Decimal("10"), Decimal("100"), SIGNAL)}
        )

    def test_pure_price_move_is_all_market_pnl(
        self, accountant: SessionAccountant, opening: LedgerSnapshot
    ) -> None:
        snapshot, attribution = accountant.advance(opening, EXECUTION, [], {"SPY": Decimal("110")})
        assert snapshot.equity == Decimal("2100")
        assert attribution.market_pnl == Decimal("100")
        assert attribution.distributions == Decimal("0")

    def test_split_preserves_equity_and_contributes_no_pnl(
        self, accountant: SessionAccountant, opening: LedgerSnapshot
    ) -> None:
        action = CorporateAction("SPY", EXECUTION, ActionKind.SPLIT, ratio=Decimal("2"))
        snapshot, attribution = accountant.advance(
            opening, EXECUTION, [], {"SPY": Decimal("50")}, actions=[action]
        )
        assert snapshot.equity == Decimal("2000")
        assert snapshot.positions["SPY"].quantity == Decimal("20")
        assert attribution.market_pnl == Decimal("0")

    def test_dividend_is_counted_once_as_a_distribution(
        self, accountant: SessionAccountant, opening: LedgerSnapshot
    ) -> None:
        action = CorporateAction(
            "SPY", EXECUTION, ActionKind.CASH_DIVIDEND, cash_per_share=Decimal("1.5")
        )
        snapshot, attribution = accountant.advance(
            opening, EXECUTION, [], {"SPY": Decimal("100")}, actions=[action]
        )
        assert attribution.distributions == Decimal("15.0")
        assert attribution.market_pnl == Decimal("0")
        assert snapshot.cash == Decimal("1015.0")

    def test_fill_moves_cash_and_shares_consistently(
        self, accountant: SessionAccountant, opening: LedgerSnapshot
    ) -> None:
        fill = Fill("SPY", EXECUTION, Side.BUY, Decimal("5"), Decimal("100"), Decimal("1"))
        snapshot, attribution = accountant.advance(
            opening, EXECUTION, [fill], {"SPY": Decimal("100")}
        )
        assert snapshot.positions["SPY"].quantity == Decimal("15")
        assert snapshot.cash == Decimal("499")
        assert attribution.fees == Decimal("1")

    def test_cash_yield_accrues_on_the_opening_balance(
        self, accountant: SessionAccountant, opening: LedgerSnapshot
    ) -> None:
        _, attribution = accountant.advance(
            opening, EXECUTION, [], {"SPY": Decimal("100")}, cash_rate_annualized=0.0252
        )
        assert attribution.cash_yield == pytest.approx(Decimal("0.1"), abs=Decimal("0.001"))

    def test_selling_more_than_held_fails_closed(
        self, accountant: SessionAccountant, opening: LedgerSnapshot
    ) -> None:
        fill = Fill("SPY", EXECUTION, Side.SELL, Decimal("50"), Decimal("100"), Decimal("0"))
        with pytest.raises(AccountingError, match="exceeds held"):
            accountant.advance(opening, EXECUTION, [fill], {"SPY": Decimal("100")})

    def test_holding_without_a_mark_fails_closed(
        self, accountant: SessionAccountant, opening: LedgerSnapshot
    ) -> None:
        with pytest.raises(AccountingError, match="no closing mark"):
            accountant.advance(opening, EXECUTION, [], {})

    def test_equity_identity_holds_after_every_advance(
        self, accountant: SessionAccountant, opening: LedgerSnapshot
    ) -> None:
        fill = Fill("SPY", EXECUTION, Side.BUY, Decimal("2"), Decimal("105"), Decimal("0.5"))
        action = CorporateAction(
            "SPY", EXECUTION, ActionKind.CASH_DIVIDEND, cash_per_share=Decimal("0.25")
        )
        snapshot, _ = accountant.advance(
            opening, EXECUTION, [fill], {"SPY": Decimal("106")}, actions=[action]
        )
        assert snapshot.equity == snapshot.cash + snapshot.holdings_value


@pytest.mark.parametrize("cash_policy", [False, True])
def test_whole_share_mode_rounds_after_cap_and_preserves_native_exit(cash_policy):
    from dataclasses import replace

    from amat.execution.budget import CashBudgetPolicy

    model = SimulatedExecutionModel(
        ExecutionConfig(),
        CostConfig(),
        whole_share_buys=True,
        max_entry_window_participation=0.01,
        cash_policy=CashBudgetPolicy() if cash_policy else None,
    )
    fills, missed = model.execute(
        [instruction(quantity="10.8")],
        {"SPY": window(volume="550")},
        available_cash=Decimal("10000"),
        account_equity=Decimal("10000"),
    )
    assert fills[0].quantity == Decimal("5")
    assert any(m.reason == MissedFillReason.QUANTITY_INCREMENT for m in missed)
    assert fills[0].quantity <= Decimal("5.5")
    trim = instruction(Side.SELL, quantity="2.8")
    fills, _ = model.execute(
        [trim], {"SPY": window()}, available_cash=Decimal("0"), account_equity=Decimal("10000")
    )
    assert fills[0].quantity == Decimal("2")
    # A full exit of a corporate-action fractional entitlement must not leave trapped dust.
    exit_order = replace(trim, reason=TradeReason.POSITION_TRANSITION)
    fills, _ = model.execute(
        [exit_order],
        {"SPY": window()},
        available_cash=Decimal("0"),
        account_equity=Decimal("10000"),
    )
    assert fills[0].quantity == Decimal("2.8")


def test_legacy_execution_explicitly_allows_fractional_quantity():
    model = SimulatedExecutionModel(ExecutionConfig(), CostConfig())
    fills, _ = model.execute([instruction(quantity="2.8")], {"SPY": window()})
    assert fills[0].quantity == Decimal("2.8")
