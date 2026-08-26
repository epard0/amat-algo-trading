"""The execution boundary: what an executor is handed, and what it is refused.

These tests exist because the plan is the last artifact this repository produces
before a human or a machine spends money on it. Everything upstream is checked
against a simulated ledger that forgives; a plan is checked against nothing.

Two properties matter more than the rest and are asserted from several angles:

* a plan may never instruct execution in the session that formed it, which is
  the canonical look-ahead defect expressed at the boundary; and
* a plan may never quietly say less than it knows -- a withheld trade, a missing
  mark, or a holding the strategy does not recognise must be visible, because
  each of them looks exactly like "nothing to do" when it is dropped.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from amat.config.loading import load_strategy_config
from amat.errors import ConfigurationError, DataIntegrityError
from amat.execution.intent import PLAN_SCHEMA_VERSION, build_execution_plan

SESSION = dt.date(2026, 8, 7)
GENERATED = dt.datetime(2026, 8, 7, 21, 30, tzinfo=dt.UTC)
EQUITY = Decimal("100000")

#: Every universe symbol priced at 100, so a weight of 0.10 is exactly 100
#: shares and every expected quantity below is checkable by hand.
FLAT_MARKS = {
    symbol: Decimal("100")
    for symbol in (
        "SPY",
        "QQQ",
        "IWM",
        "EFA",
        "EEM",
        "TLT",
        "IEF",
        "GLD",
        "SLV",
        "DBC",
        "USO",
        "UUP",
    )
}


@pytest.fixture(scope="module")
def strategy(canonical_config_path):
    """The frozen v1 configuration: 2pp rebalance band, $25 minimum notional."""
    return load_strategy_config(canonical_config_path).strategy


def plan_for(
    strategy,
    *,
    targets: dict[str, float],
    positions: dict[str, Decimal] | None = None,
    marks: dict[str, Decimal] | None = None,
    equity: Decimal = EQUITY,
):
    return build_execution_plan(
        strategy,
        engine="v1-frozen",
        as_of_session=SESSION,
        target_weights=targets,
        marks=FLAT_MARKS if marks is None else marks,
        current_positions=positions or {},
        account_equity=equity,
        config_sha256="0" * 64,
        snapshot_id="test-snapshot",
        code_revision="deadbeef",
        generated_at=GENERATED,
    )


class TestCausality:
    """A plan formed from session t may not execute in session t."""

    def test_earliest_execution_is_strictly_after_the_signal_session(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10})
        assert plan.earliest_execution_session > plan.as_of_session

    def test_the_gap_is_the_configured_lag_not_an_assumption(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10})
        expected = SESSION + dt.timedelta(days=strategy.execution.signal_to_execution_lag_sessions)
        assert plan.earliest_execution_session == expected

    def test_the_window_is_carried_so_an_executor_need_not_infer_it(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10})
        assert plan.window_start_local == strategy.execution.window_start
        assert plan.window_end_local == strategy.execution.window_end
        assert plan.exchange_timezone == strategy.execution.timezone


class TestStaleness:
    def test_a_plan_expires_after_its_validity_window(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10})
        assert not plan.is_expired(now=GENERATED)
        assert plan.is_expired(now=GENERATED + dt.timedelta(days=30))

    def test_expiry_is_strictly_after_the_session_that_formed_it(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10})
        assert plan.expires_at.date() > plan.as_of_session

    def test_a_naive_instant_cannot_be_used_to_judge_expiry(self, strategy) -> None:
        """A naive clock is how a stale plan gets executed in the wrong zone."""
        plan = plan_for(strategy, targets={"SPY": 0.10})
        with pytest.raises(ConfigurationError, match="timezone-aware"):
            plan.is_expired(now=dt.datetime(2026, 8, 8, 12, 0))  # noqa: DTZ001


class TestRefusals:
    """Each of these produces a wrong position rather than an obvious error."""

    def test_a_negative_target_weight_is_refused(self, strategy) -> None:
        with pytest.raises(ConfigurationError, match="short is not representable"):
            plan_for(strategy, targets={"SPY": -0.10})

    def test_gross_exposure_above_the_cap_is_refused(self, strategy) -> None:
        with pytest.raises(DataIntegrityError, match="exceeds the configured cap"):
            plan_for(strategy, targets={"SPY": 0.80, "QQQ": 0.80})

    def test_a_missing_mark_for_a_wanted_symbol_is_refused(self, strategy) -> None:
        marks = {symbol: price for symbol, price in FLAT_MARKS.items() if symbol != "SPY"}
        with pytest.raises(DataIntegrityError, match="no decision-session mark"):
            plan_for(strategy, targets={"SPY": 0.10}, marks=marks)

    def test_a_missing_mark_for_a_held_symbol_is_refused(self, strategy) -> None:
        """Skipping it would claim a flat target for a position still open."""
        marks = {symbol: price for symbol, price in FLAT_MARKS.items() if symbol != "GLD"}
        with pytest.raises(DataIntegrityError, match="no decision-session mark"):
            plan_for(strategy, targets={}, positions={"GLD": Decimal("50")}, marks=marks)

    def test_non_positive_equity_is_refused(self, strategy) -> None:
        with pytest.raises(ConfigurationError, match="equity must be positive"):
            plan_for(strategy, targets={"SPY": 0.10}, equity=Decimal(0))


class TestRebalanceRulesComeFromTheContract:
    """The plan must not invent a second rebalance policy alongside the engine's."""

    def test_a_delta_below_the_band_emits_no_trade(self, strategy) -> None:
        # 95 shares at 100 against 100,000 equity is a 9.5% weight; seeking 10%
        # is a half-point move, well inside the 2-point band.
        plan = plan_for(strategy, targets={"SPY": 0.10}, positions={"SPY": Decimal("95")})
        assert plan.intents == ()

    def test_a_zero_to_nonzero_transition_trades_inside_the_band(self, strategy) -> None:
        """Entering and exiting are exempt from the band, or nothing ever opens."""
        plan = plan_for(strategy, targets={"SPY": 0.005})
        assert [intent.symbol for intent in plan.intents] == ["SPY"]
        assert plan.intents[0].side == "buy"
        assert plan.intents[0].quantity == Decimal("5")
        assert plan.intents[0].reason == "position_transition"

    def test_selling_to_flat_sells_exactly_what_is_held(self, strategy) -> None:
        plan = plan_for(strategy, targets={}, positions={"SPY": Decimal("100")})
        assert [intent.symbol for intent in plan.intents] == ["SPY"]
        assert plan.intents[0].side == "sell"
        assert plan.intents[0].quantity == Decimal("100")

    def test_a_sell_never_exceeds_the_held_quantity(self, strategy) -> None:
        """Long-or-cash: overselling would open a short the contract forbids."""
        held = Decimal("100")
        plan = plan_for(strategy, targets={"SPY": 0.0}, positions={"SPY": held})
        for intent in plan.intents:
            if intent.side == "sell":
                assert intent.quantity <= held

    def test_a_trade_below_the_minimum_notional_is_withheld_not_dropped(self, strategy) -> None:
        """Withheld and satisfied must never look identical to the reader."""
        # 0.5% of 1,000 is 5 dollars, below the 25-dollar floor.
        plan = plan_for(strategy, targets={"SPY": 0.005}, equity=Decimal("1000"))
        assert plan.intents == ()
        assert [item.symbol for item in plan.withheld] == ["SPY"]
        assert plan.withheld[0].reason == "below_minimum_notional"


class TestReconciliation:
    def test_a_holding_outside_the_universe_is_reported(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10}, positions={"TSLA": Decimal("10")})
        assert plan.unrecognized_holdings == ("TSLA",)

    def test_a_holding_outside_the_universe_is_never_traded(self, strategy) -> None:
        """Liquidating what it does not understand is worse than doing nothing."""
        plan = plan_for(strategy, targets={"SPY": 0.10}, positions={"TSLA": Decimal("10")})
        assert all(intent.symbol != "TSLA" for intent in plan.intents)

    def test_an_unrecognized_holding_raises_a_caveat(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10}, positions={"TSLA": Decimal("10")})
        assert any("outside the strategy universe" in caveat for caveat in plan.caveats)

    def test_current_positions_are_echoed_for_after_the_fact_checking(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10}, positions={"GLD": Decimal("7")})
        assert plan.current_positions == {"GLD": Decimal("7")}


class TestSerialization:
    def test_the_plan_serializes_to_json(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10}, positions={"GLD": Decimal("7")})
        payload = json.loads(json.dumps(plan.as_dict()))
        assert payload["schema_version"] == PLAN_SCHEMA_VERSION
        assert payload["as_of_session"] == SESSION.isoformat()

    def test_quantities_serialize_as_strings_not_floats(self, strategy) -> None:
        """A share count that round-trips through float is a wrong share count."""
        plan = plan_for(strategy, targets={"SPY": 0.005})
        payload = plan.as_dict()
        assert isinstance(payload["intents"][0]["quantity"], str)
        assert isinstance(payload["account_equity"], str)

    def test_identical_inputs_produce_an_identical_plan(self, strategy) -> None:
        first = plan_for(strategy, targets={"SPY": 0.10}, positions={"GLD": Decimal("7")})
        second = plan_for(strategy, targets={"SPY": 0.10}, positions={"GLD": Decimal("7")})
        assert json.dumps(first.as_dict(), sort_keys=True) == json.dumps(
            second.as_dict(), sort_keys=True
        )

    def test_provenance_travels_with_the_plan(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10})
        assert plan.config_sha256 == "0" * 64
        assert plan.snapshot_id == "test-snapshot"
        assert plan.code_revision == "deadbeef"
        assert plan.strategy_name == strategy.name

    def test_the_plan_says_it_submits_nothing(self, strategy) -> None:
        plan = plan_for(strategy, targets={"SPY": 0.10})
        assert any("submits nothing" in caveat for caveat in plan.caveats)
