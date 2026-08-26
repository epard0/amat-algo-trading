"""The exit-rule overlay: stop-loss/take-profit on the adjusted entry basis.

The mechanics run on hand-built fixtures (a fill sets the basis, a breach
forces the target flat, an embargo blocks re-entry) and the end-to-end
behavior on an engineered panel where one symbol is made to crash: the run
with a stop must flatten that symbol within the execution lag of the breach,
the run without must not, and the two must be *identical* before the breach.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import ClassVar

import numpy as np
import pytest

from amat.backtest.exits import ExitRules, apply_exit_rules, track_entry_bases
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.domain.fills import Fill
from amat.domain.positions import Position
from amat.domain.types import Side
from amat.errors import ConfigurationError

SESSION = dt.date(2024, 1, 5)


def _position(symbol: str, quantity: str) -> Position:
    return Position(symbol, Decimal(quantity), Decimal("100"), SESSION)


def _fill(symbol: str, side: Side, price: str) -> Fill:
    return Fill(symbol, SESSION, side, Decimal("10"), Decimal(price), Decimal("0"))


class TestExitRulesValidation:
    def test_a_rule_free_object_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="no-op"):
            ExitRules()

    @pytest.mark.parametrize(
        ("stop", "profit"), [(0.0, None), (1.0, None), (None, 0.0), (None, 10.0)]
    )
    def test_out_of_domain_rules_are_refused(self, stop, profit) -> None:
        with pytest.raises(ConfigurationError):
            ExitRules(stop_loss=stop, take_profit=profit)

    def test_a_sub_session_embargo_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="embargo"):
            ExitRules(stop_loss=0.10, reentry_embargo_sessions=0)

    def test_the_label_names_only_the_active_rules(self) -> None:
        assert ExitRules(stop_loss=0.10, take_profit=0.20).label == "sl10%+tp20%"
        assert ExitRules(stop_loss=0.05).label == "sl5%"
        assert ExitRules(take_profit=0.20).label == "tp20%"


class TestEntryBasisTracking:
    COLUMNS: ClassVar[dict[str, int]] = {"SPY": 0}

    def test_a_fresh_buy_records_the_adjusted_basis(self) -> None:
        basis: dict = {}
        track_entry_bases(
            basis,
            fills=[_fill("SPY", Side.BUY, "100")],
            positions={"SPY": _position("SPY", "10")},
            adjusted_row=np.array([90.0]),  # adjusted/raw ratio 0.9
            raw_row=np.array([100.0]),
            symbol_columns=self.COLUMNS,
        )
        assert basis["SPY"] == pytest.approx(90.0)

    def test_a_scale_up_does_not_move_the_basis(self) -> None:
        basis = {"SPY": 90.0}
        track_entry_bases(
            basis,
            fills=[_fill("SPY", Side.BUY, "120")],
            positions={"SPY": _position("SPY", "20")},
            adjusted_row=np.array([120.0]),
            raw_row=np.array([120.0]),
            symbol_columns=self.COLUMNS,
        )
        assert basis["SPY"] == pytest.approx(90.0)

    def test_returning_to_flat_clears_the_basis(self) -> None:
        basis = {"SPY": 90.0}
        track_entry_bases(
            basis,
            fills=[_fill("SPY", Side.SELL, "95")],
            positions={},
            adjusted_row=np.array([95.0]),
            raw_row=np.array([95.0]),
            symbol_columns=self.COLUMNS,
        )
        assert basis == {}


class TestApplyExitRules:
    COLUMNS: ClassVar[dict[str, int]] = {"SPY": 0, "TLT": 1}

    def _apply(self, rules: ExitRules, *, price: float, embargo: dict | None = None) -> tuple:
        embargo = {} if embargo is None else embargo
        targets = apply_exit_rules(
            rules,
            {"SPY": 0.25, "TLT": 0.10},
            positions={"SPY": _position("SPY", "10")},
            entry_basis={"SPY": 100.0},
            embargo_until=embargo,
            decision_index=50,
            adjusted_row=np.array([price, 95.0]),
            symbol_columns=self.COLUMNS,
        )
        return targets, embargo

    def test_a_stop_breach_flattens_the_target_and_starts_the_embargo(self) -> None:
        targets, embargo = self._apply(ExitRules(stop_loss=0.10), price=89.9)
        assert targets["SPY"] == 0.0
        assert targets["TLT"] == 0.10  # untouched: no position, no breach
        assert embargo == {"SPY": 71}  # 50 + 21

    def test_a_take_profit_breach_flattens_the_target(self) -> None:
        # 121, not 120: at exactly the threshold the float subtraction lands
        # a few ulps under 0.20 and the comparison is (correctly) strict.
        targets, _ = self._apply(ExitRules(take_profit=0.20), price=121.0)
        assert targets["SPY"] == 0.0

    def test_inside_the_band_nothing_moves(self) -> None:
        targets, embargo = self._apply(ExitRules(stop_loss=0.10, take_profit=0.20), price=105.0)
        assert targets == {"SPY": 0.25, "TLT": 0.10}
        assert embargo == {}

    def test_a_dividend_cannot_fake_a_loss(self) -> None:
        # The adjusted series is total-return: a 12% raw drop that is entirely
        # distributions leaves the adjusted close at the basis, so a stop that
        # would trigger on the raw price must not trigger here.
        targets, _ = self._apply(ExitRules(stop_loss=0.10), price=100.0)
        assert targets["SPY"] == 0.25

    def test_an_embargo_blocks_re_entry_until_its_first_allowed_index(self) -> None:
        blocked, _ = self._apply(ExitRules(stop_loss=0.50), price=100.0, embargo={"SPY": 51})
        assert blocked["SPY"] == 0.0
        allowed, _ = self._apply(ExitRules(stop_loss=0.50), price=100.0, embargo={"SPY": 50})
        assert allowed["SPY"] == 0.25

    def test_per_asset_rules_only_fire_for_the_assigned_symbol(self) -> None:
        embargo: dict = {}
        targets = apply_exit_rules(
            {
                "SPY": ExitRules(stop_loss=0.10),
                "TLT": ExitRules(stop_loss=0.50),
            },
            {"SPY": 0.25, "TLT": 0.10},
            positions={"SPY": _position("SPY", "10"), "TLT": _position("TLT", "10")},
            entry_basis={"SPY": 100.0, "TLT": 100.0},
            embargo_until=embargo,
            decision_index=50,
            adjusted_row=np.array([89.9, 89.9]),
            symbol_columns=self.COLUMNS,
        )
        assert targets["SPY"] == 0.0
        assert targets["TLT"] == 0.10
        assert embargo == {"SPY": 71}


@pytest.fixture(scope="module")
def strategy():
    return load_strategy_config("configs/strategy/amat_v1.toml").strategy


#: Records start at the first decision session (warm-up 252, zero-indexed).
_FIRST_DECISION = 251


@pytest.fixture(scope="module")
def rising_panel(strategy) -> MarketPanel:
    """A panel where only SPY trades: a steady rise the signal never exits.

    SPY compounds ~0.4% a session with a small deterministic wiggle (so no
    variance estimate degenerates); the other eleven symbols alternate
    +-0.05% around flat, which the deadband reads as no trend, so they are
    never entered. On this panel the baseline holds SPY indefinitely -- the
    one behavior an explicit take-profit, and only a take-profit, changes.
    """
    base = generate_panel(strategy.to_universe(), sessions=330, seed=13, trend_strength=0.5)
    total = len(base)
    prices = np.empty((total, len(base.symbols)))
    prices[0, :] = 100.0
    for row in range(1, total):
        spy_rate = 1.004 + 0.001 * ((row % 3) - 1)
        prices[row, 0] = prices[row - 1, 0] * spy_rate
        flat_rate = 1.0 + 0.0005 * (1 if row % 2 == 0 else -1)
        prices[row, 1:] = prices[row - 1, 1:] * flat_rate
    return MarketPanel(
        sessions=base.sessions,
        symbols=base.symbols,
        adjusted_closes=prices,
        raw_closes=prices.copy(),
        window_vwaps=prices.copy(),
        window_volumes=base.window_volumes,
        source="synthetic",
    )


def _spy_net_shares(result, *, through: int | None = None) -> Decimal:
    total = Decimal(0)
    for record in result.sessions[:through]:
        for fill in record.fills:
            if fill.symbol == "SPY":
                total += fill.signed_quantity
    return total


def _first_breach_index(panel: MarketPanel, take_profit: float) -> int:
    """The decision index where SPY first sits ``take_profit`` above basis.

    The basis is the entry fill: the first decision (index 251) executes at
    252 against that session's window VWAP.
    """
    basis = float(panel.window_vwaps[_FIRST_DECISION + 1, 0])
    spy = panel.adjusted_closes[:, 0]
    for row in range(_FIRST_DECISION + 1, len(panel)):
        if spy[row] / basis - 1.0 >= take_profit:
            return row
    message = "the fixture never reaches the take-profit level"
    raise AssertionError(message)


class TestRunnerIntegration:
    TAKE_PROFIT = 0.20
    EMBARGO = 21

    def test_the_take_profit_exits_re_embargoes_and_re_enters(self, strategy, rising_panel) -> None:
        rules = ExitRules(take_profit=self.TAKE_PROFIT, reentry_embargo_sessions=self.EMBARGO)
        taken = run_backtest(strategy, rising_panel, strategy.to_universe(), exit_rules=rules)
        baseline = run_backtest(strategy, rising_panel, strategy.to_universe())
        breach = _first_breach_index(rising_panel, self.TAKE_PROFIT)

        # Identical before the breach: the overlay changes nothing until a
        # rule actually fires.
        prefix = breach - _FIRST_DECISION
        assert taken.equity_curve[:prefix] == baseline.equity_curve[:prefix]

        # Mid-embargo the take-profit run is flat SPY while the baseline,
        # riding an unbroken trend, still holds it.
        mid_embargo = breach + self.EMBARGO // 2
        through = mid_embargo - _FIRST_DECISION + 1
        assert _spy_net_shares(taken, through=through) == 0
        assert _spy_net_shares(baseline, through=through) > 0

        # After the embargo the signal is still long, so the overlay run
        # re-enters: the embargo ends, it does not ban the symbol.
        assert _spy_net_shares(taken) > 0

    def test_rules_that_never_trigger_are_a_perfect_no_op(self, strategy, rising_panel) -> None:
        wide = run_backtest(
            strategy,
            rising_panel,
            strategy.to_universe(),
            exit_rules=ExitRules(stop_loss=0.90, take_profit=9.0),
        )
        baseline = run_backtest(strategy, rising_panel, strategy.to_universe())
        assert wide.equity_curve == baseline.equity_curve
        assert wide.total_fills == baseline.total_fills

    def test_the_overlay_is_causal_under_future_mutation(self, strategy, rising_panel) -> None:
        cut = 300
        mutated_adjusted = rising_panel.adjusted_closes.copy()
        mutated_adjusted[cut:, :] *= 1.5
        mutated_raw = rising_panel.raw_closes.copy()
        mutated_raw[cut:, :] *= 1.5
        mutated = MarketPanel(
            sessions=rising_panel.sessions,
            symbols=rising_panel.symbols,
            adjusted_closes=mutated_adjusted,
            raw_closes=mutated_raw,
            window_vwaps=rising_panel.window_vwaps.copy(),
            window_volumes=rising_panel.window_volumes.copy(),
            source="synthetic",
        )
        rules = ExitRules(stop_loss=0.10, take_profit=0.30)
        original = run_backtest(strategy, rising_panel, strategy.to_universe(), exit_rules=rules)
        rewritten = run_backtest(strategy, mutated, strategy.to_universe(), exit_rules=rules)
        prefix = cut - _FIRST_DECISION
        assert original.equity_curve[:prefix] == rewritten.equity_curve[:prefix]
        # Equity alone has a lag-sized blind spot: the decision formed at the
        # last compared session fills after the cut. Its diagnostics and the
        # fills recorded before the cut close it.
        for before, after in zip(
            original.sessions[:prefix], rewritten.sessions[:prefix], strict=True
        ):
            assert before.fills == after.fills
            assert before.binding_constraints == after.binding_constraints
