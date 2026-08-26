"""The v5 breadth-expansion contract.

Three protocol obligations, each pinned here before any measurement counts:

* the null case -- v5 restricted to the v1 symbol list -- reproduces v1
  bit-for-bit;
* only the universe differs from v1 (the falsification harness refuses
  anything else, and this file proves there is nothing for it to refuse);
* causality holds on the expanded universe: mutating observations after a cut
  changes nothing at or before it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.synthetic import generate_panel
from amat.validation.falsification import _identical_core

V1 = "configs/strategy/amat_v1.toml"
V5 = "configs/strategy/amat_v5.toml"

ADDED = ("FXI", "TIP", "HYG", "EMB", "VNQ")


@pytest.fixture(scope="module")
def v1() -> StrategyConfig:
    return load_strategy_config(V1).strategy


@pytest.fixture(scope="module")
def v5() -> StrategyConfig:
    return load_strategy_config(V5).strategy


class TestComposition:
    def test_the_universe_is_v1_plus_exactly_the_declared_additions(self, v1, v5) -> None:
        v1_symbols = {str(symbol) for symbol in v1.universe.symbols}
        v5_symbols = {str(symbol) for symbol in v5.universe.symbols}
        assert v5_symbols - v1_symbols == set(ADDED)
        assert v1_symbols <= v5_symbols
        assert len(v5.universe.symbols) == 17

    def test_shy_is_excluded(self, v5) -> None:
        """SHY's 2021-12-01 conflicting dividend fails the loader closed, and
        its 1.55% realized vol sits under the 5% volatility floor. Its
        presence would be a defect, not an asset."""
        assert "SHY" not in {str(symbol) for symbol in v5.universe.symbols}

    def test_v1_clusters_keep_their_caps(self, v1, v5) -> None:
        v5_caps = {cluster.name: cluster.gross_cap for cluster in v5.universe.clusters}
        for cluster in v1.universe.clusters:
            assert v5_caps[cluster.name] == cluster.gross_cap

    def test_only_the_universe_differs_from_v1(self, v1, v5) -> None:
        """The falsification harness enforces this at run time; asserting it
        here means a drifted copy of a v1 section fails in seconds, not
        minutes into a study."""
        _identical_core(v5, v1)


class TestTheNullCase:
    def test_v5_restricted_to_the_v1_universe_is_v1(self, v1, v5) -> None:
        """Bit-identity, the protocol's non-negotiable: with the additions
        removed, every session of a run under the restricted v5 config equals
        the v1 run exactly."""
        payload = v5.model_dump()
        payload["universe"] = v1.model_dump()["universe"]
        restricted = StrategyConfig.model_validate(payload)

        panel = generate_panel(v1.to_universe(), sessions=330, seed=17, trend_strength=0.5)
        baseline = run_backtest(v1, panel, v1.to_universe(), initial_cash=Decimal("100000"))
        candidate = run_backtest(
            restricted, panel, restricted.to_universe(), initial_cash=Decimal("100000")
        )
        assert len(baseline.sessions) == len(candidate.sessions)
        for ours, theirs in zip(baseline.sessions, candidate.sessions, strict=True):
            assert ours.equity == theirs.equity
            assert ours.cash == theirs.cash
            assert ours.fills == theirs.fills


class TestCausality:
    def test_mutating_the_future_does_not_change_the_past(self, v5) -> None:
        """The decisive leakage test, on the expanded universe: garbage after
        the cut, bit-identical sessions through it."""
        universe = v5.to_universe()
        sessions = 380
        cut = 340
        panel = generate_panel(universe, sessions=sessions, seed=23, trend_strength=0.4)
        baseline = run_backtest(v5, panel, universe, initial_cash=Decimal("100000"))

        mutated_adjusted = panel.adjusted_closes.copy()
        mutated_raw = panel.raw_closes.copy()
        mutated_adjusted[cut:] *= 7.5
        mutated_raw[cut:] *= 7.5
        from dataclasses import replace

        mutated_panel = replace(panel, adjusted_closes=mutated_adjusted, raw_closes=mutated_raw)
        mutated = run_backtest(v5, mutated_panel, universe, initial_cash=Decimal("100000"))

        # A decision formed at cut-1 fills at cut, so records strictly before
        # the cut are the ones no future row may touch.
        untouched = [
            (before, after)
            for before, after in zip(baseline.sessions, mutated.sessions, strict=True)
            if before.session < panel.sessions[cut]
        ]
        assert untouched, "the cut must leave measurable history"
        for before, after in untouched:
            assert before.equity == after.equity
            assert before.fills == after.fills
