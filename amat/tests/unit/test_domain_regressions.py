"""Regression tests for the defects confirmed by the 2026-08-08 audit.

Each test here fails against the pre-fix code. They are grouped by audit finding
number so a reader can trace a test back to the claim that motivated it.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.bars import DailyBar, ExecutionWindowBar
from amat.domain.fills import Fill
from amat.domain.instruments import Cluster, Universe
from amat.domain.ledgers import LedgerSnapshot, PeriodAttribution, reconcile_session
from amat.domain.orders import SimulatedOrder, TradeReason
from amat.domain.positions import Position
from amat.domain.types import Side
from amat.errors import ConfigurationError, DataIntegrityError

SESSION = dt.date(2020, 1, 2)
NEXT_SESSION = dt.date(2020, 1, 3)


def _bar(**overrides: object) -> DailyBar:
    payload: dict[str, object] = {
        "symbol": "SPY",
        "session_date": SESSION,
        "raw_open": Decimal("100"),
        "raw_high": Decimal("101"),
        "raw_low": Decimal("99"),
        "raw_close": Decimal("100"),
        "adjusted_close": Decimal("100"),
        "volume": Decimal("1000"),
        "source": "test",
        "adjustment_mode": "raw",
    }
    payload.update(overrides)
    return DailyBar(**payload)  # type: ignore[arg-type]


class TestFinding1SideIdentity:
    """A ``str`` side compared equal to the enum but failed the ``is`` test."""

    @pytest.mark.parametrize("side", ["buy", Side.BUY])
    def test_buy_has_positive_signed_quantity(self, side: object) -> None:
        fill = Fill(
            symbol="SPY",
            execution_session=SESSION,
            side=side,  # type: ignore[arg-type]
            quantity=Decimal("10"),
            price=Decimal("100"),
            cost_cash=Decimal("1"),
        )
        assert fill.side is Side.BUY
        assert fill.signed_quantity == Decimal("10")
        assert fill.cash_delta == Decimal("-1001")

    @pytest.mark.parametrize("side", ["sell", Side.SELL])
    def test_sell_has_negative_signed_quantity(self, side: object) -> None:
        fill = Fill(
            symbol="SPY",
            execution_session=SESSION,
            side=side,  # type: ignore[arg-type]
            quantity=Decimal("10"),
            price=Decimal("100"),
            cost_cash=Decimal("1"),
        )
        assert fill.signed_quantity == Decimal("-10")
        assert fill.cash_delta == Decimal("999")

    def test_instruction_side_is_coerced(self) -> None:
        instruction = SimulatedOrder(
            symbol="SPY",
            signal_session=SESSION,
            execution_session=NEXT_SESSION,
            side="buy",  # type: ignore[arg-type]
            quantity=Decimal("5"),
            reason=TradeReason.WEIGHT_DELTA,
        )
        assert instruction.signed_quantity == Decimal("5")

    def test_unknown_side_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="invalid side"):
            Fill(
                symbol="SPY",
                execution_session=SESSION,
                side="hold",  # type: ignore[arg-type]
                quantity=Decimal("1"),
                price=Decimal("1"),
                cost_cash=Decimal("0"),
            )


class TestFinding9SymbolNormalization:
    """Records accepted arbitrary strings because ``NewType`` erases at runtime."""

    def test_position_normalizes_symbol(self) -> None:
        assert Position(" spy ", Decimal("1"), Decimal("100"), SESSION).symbol == "SPY"

    def test_bar_normalizes_symbol(self) -> None:
        assert _bar(symbol=" spy ").symbol == "SPY"

    def test_invalid_symbol_is_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="invalid symbol"):
            Position("not a ticker", Decimal("1"), Decimal("100"), SESSION)

    def test_aliased_symbols_cannot_double_count(self) -> None:
        """``' spy '`` and ``'SPY'`` collapse to one key instead of two lines."""
        positions = {
            "SPY": Position(" spy ", Decimal("10"), Decimal("100"), SESSION),
        }
        snapshot = LedgerSnapshot(SESSION, Decimal("0"), positions)
        assert snapshot.holdings_value == Decimal("1000")


class TestFinding11VolumeValidation:
    """Volume was exempt from the checks every price field received."""

    @pytest.mark.parametrize(
        "volume",
        [Decimal("NaN"), Decimal("Infinity"), 1000.5, 5, float("nan")],
        ids=["nan", "inf", "float", "int", "float_nan"],
    )
    def test_invalid_volume_raises_data_integrity_error(self, volume: object) -> None:
        with pytest.raises(DataIntegrityError):
            _bar(volume=volume)

    def test_zero_volume_is_legal_and_untradable(self) -> None:
        assert _bar(volume=Decimal("0")).is_tradable is False


class TestFinding10DefensiveCopy:
    """``frozen=True`` protects the binding, not the mapping behind it."""

    def test_ledger_snapshot_copies_positions(self) -> None:
        live = {"SPY": Position("SPY", Decimal("10"), Decimal("100"), SESSION)}
        snapshot = LedgerSnapshot(SESSION, Decimal("100"), live)
        before = snapshot.equity

        live["QQQ"] = Position("QQQ", Decimal("5"), Decimal("50"), dt.date(1999, 1, 1))

        assert snapshot.equity == before
        assert "QQQ" not in snapshot.positions

    def test_universe_copies_instruments(self) -> None:
        clusters = [Cluster(name="eq", gross_cap=0.4, symbols=("SPY",))]
        universe = Universe.from_clusters(clusters)
        live = dict(universe.instruments)
        live["EVIL"] = next(iter(universe.instruments.values()))
        assert "EVIL" not in universe.instruments


class TestFinding20ToleranceGuard:
    """A non-finite tolerance declared any residual reconciled."""

    def _pair(self) -> tuple[LedgerSnapshot, LedgerSnapshot]:
        previous = LedgerSnapshot(SESSION, Decimal("1000"), {})
        current = LedgerSnapshot(NEXT_SESSION, Decimal("999999"), {})
        return previous, current

    @pytest.mark.parametrize("tolerance", [Decimal("Infinity"), Decimal("NaN")])
    def test_non_finite_tolerance_is_rejected(self, tolerance: Decimal) -> None:
        previous, current = self._pair()
        zero = PeriodAttribution(*(Decimal(0),) * 5)
        with pytest.raises(ConfigurationError, match="finite"):
            reconcile_session(previous, current, zero, tolerance=tolerance)

    def test_unexplained_residual_still_fails(self) -> None:
        from amat.errors import AccountingError

        previous, current = self._pair()
        zero = PeriodAttribution(*(Decimal(0),) * 5)
        with pytest.raises(AccountingError):
            reconcile_session(previous, current, zero)


class TestCorporateActions:
    """The record that makes guardrail 6 representable at all."""

    def test_split_preserves_marked_value(self) -> None:
        action = CorporateAction("SPY", SESSION, ActionKind.SPLIT, ratio=Decimal("2"))
        quantity, mark = Decimal("10"), Decimal("100")
        assert action.adjust_quantity(quantity) * action.adjust_mark(mark) == quantity * mark

    def test_dividend_credits_cash_and_leaves_shares(self) -> None:
        action = CorporateAction(
            "SPY", SESSION, ActionKind.CASH_DIVIDEND, cash_per_share=Decimal("1.5")
        )
        assert action.cash_credit(Decimal("10")) == Decimal("15.0")
        assert action.adjust_quantity(Decimal("10")) == Decimal("10")

    def test_a_split_may_not_also_pay_cash(self) -> None:
        with pytest.raises(ConfigurationError, match="must not credit cash"):
            CorporateAction(
                "SPY",
                SESSION,
                ActionKind.SPLIT,
                ratio=Decimal("2"),
                cash_per_share=Decimal("1"),
            )

    def test_a_dividend_may_not_rescale_shares(self) -> None:
        with pytest.raises(ConfigurationError, match="must not rescale"):
            CorporateAction(
                "SPY",
                SESSION,
                ActionKind.CASH_DIVIDEND,
                cash_per_share=Decimal("1"),
                ratio=Decimal("2"),
            )


class TestExecutionWindowBar:
    """The record that makes ``price_method = "vwap"`` satisfiable."""

    def _window(self, **overrides: object) -> ExecutionWindowBar:
        payload: dict[str, object] = {
            "symbol": "SPY",
            "session_date": SESSION,
            "window_start": dt.datetime(2020, 1, 2, 14, 30, tzinfo=dt.UTC),
            "window_end": dt.datetime(2020, 1, 2, 14, 35, tzinfo=dt.UTC),
            "vwap": Decimal("100.25"),
            "volume": Decimal("50000"),
            "source": "test",
        }
        payload.update(overrides)
        return ExecutionWindowBar(**payload)  # type: ignore[arg-type]

    def test_valid_window_is_tradable(self) -> None:
        assert self._window().is_tradable is True

    def test_zero_window_volume_is_untradable(self) -> None:
        assert self._window(volume=Decimal("0")).is_tradable is False

    def test_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="UTC"):
            self._window(window_start=dt.datetime(2020, 1, 2, 14, 30))  # noqa: DTZ001

    def test_misordered_window_is_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="must be after"):
            self._window(window_end=dt.datetime(2020, 1, 2, 14, 25, tzinfo=dt.UTC))
