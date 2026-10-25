"""The decision the backtest throws away is the only one a live account wants.

The session loop schedules each decision into a later session *inside the panel*.
On the final session there is no such session, so the loop forms no decision at
all -- and that is exactly the decision that would execute tomorrow.

For v1 this costs nothing to recover: :func:`amat.portfolio.decide` is a pure
function of the price panel, so the terminal decision is the allocator evaluated
at the last index. It was not always this cheap. The rejected v2 overlay was
path-dependent, so the same decision could only be read out of a full replay from
inception, behind an opt-in flag that existed solely to avoid perturbing already
published artifacts. Removing the overlay removed that machinery with it, and
these tests now assert the property rather than the workaround.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from amat.planning import build_plan

SESSIONS = 400
EQUITY = Decimal("100000")


@pytest.fixture(scope="module")
def strategy(canonical_config_path):
    return load_strategy_config(canonical_config_path).strategy


@pytest.fixture(scope="module")
def panel(strategy):
    return generate_panel(strategy.to_universe(), sessions=SESSIONS, seed=7)


def plan(strategy, panel, **overrides):
    kwargs = {
        "engine": "v1-frozen",
        "current_positions": {},
        "account_equity": EQUITY,
        "config_sha256": "0" * 64,
        "snapshot_id": "synthetic",
        "code_revision": "test",
        "generated_at": dt.datetime(2026, 8, 7, 21, 30, tzinfo=dt.UTC),
    }
    kwargs.update(overrides)
    return build_plan(strategy, panel, strategy.to_universe(), **kwargs)


class TestTheDecisionReachesAPlan:
    def test_monitoring_prices_cover_unheld_assets_and_only_completed_sessions(
        self, strategy, panel
    ):
        context = plan(strategy, panel).as_dict()["monitoring"]
        history = context["price_history"]
        assert len(history) == 65
        assert [row["session"] for row in history] == [str(d) for d in panel.sessions[-65:]]
        assert context["as_of_session"] == str(panel.sessions[-1])
        assert set(history[-1]["prices"]) == set(panel.symbols)
        assert history[-1]["prices"][str(panel.symbols[0])]["raw_close"] == panel.raw_closes[-1, 0]
        assert context["forecast_status"] == "not_emitted_by_this_model"
        assert set(context["decision_diagnostics"]["trend_scores"]) == set(panel.symbols)
        assert "not a probability" in context["decision_diagnostics"]["score_basis"]

    def test_a_plan_is_formed_for_the_last_completed_session(self, strategy, panel) -> None:
        built = plan(strategy, panel)
        assert built.as_of_session == panel.sessions[-1]
        assert built.engine == "v1-frozen"

    def test_a_plan_may_not_execute_in_the_session_that_formed_it(self, strategy, panel) -> None:
        built = plan(strategy, panel)
        assert built.earliest_execution_session > built.as_of_session

    def test_the_decision_is_not_vacuous_here(self, strategy, panel) -> None:
        """Guards the cap assertions below from passing on an empty book."""
        assert plan(strategy, panel).gross_target_exposure > 0

    def test_gross_exposure_respects_the_cap(self, strategy, panel) -> None:
        built = plan(strategy, panel)
        assert built.gross_target_exposure <= strategy.constraints.max_gross_exposure + 1e-9

    def test_no_symbol_is_held_above_its_cap(self, strategy, panel) -> None:
        built = plan(strategy, panel)
        cap = strategy.constraints.max_symbol_weight
        assert all(weight <= cap + 1e-9 for weight in built.target_weights.values())

    def test_every_weight_is_long_or_flat(self, strategy, panel) -> None:
        built = plan(strategy, panel)
        assert all(weight > 0 for weight in built.target_weights.values())

    def test_a_flat_account_buys_rather_than_sells(self, strategy, panel) -> None:
        built = plan(strategy, panel)
        assert built.intents
        assert all(intent.side == "buy" for intent in built.intents)

    def test_an_exit_only_symbol_is_forced_to_zero_and_sold(self, strategy, panel) -> None:
        symbol = str(panel.symbols[0])
        built = plan(
            strategy,
            panel,
            current_positions={symbol: Decimal("100")},
            forced_zero_symbols=(symbol,),
        )
        assert symbol not in built.target_weights
        assert any(intent.symbol == symbol and intent.side == "sell" for intent in built.intents)

    def test_the_decision_is_reproducible(self, strategy, panel) -> None:
        first, second = plan(strategy, panel), plan(strategy, panel)
        assert first.target_weights == second.target_weights
        assert [i.quantity for i in first.intents] == [i.quantity for i in second.intents]

    def test_an_empty_panel_cannot_produce_a_plan(self, strategy, panel) -> None:
        empty = type(panel)(
            sessions=(),
            symbols=panel.symbols,
            adjusted_closes=panel.adjusted_closes[:0],
            raw_closes=panel.raw_closes[:0],
            window_vwaps=panel.window_vwaps[:0],
            window_volumes=panel.window_volumes[:0],
            source=panel.source,
        )
        with pytest.raises(DataIntegrityError, match="empty panel"):
            plan(strategy, empty)
