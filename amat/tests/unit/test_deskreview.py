"""The review passes: causal levels, honest vocabulary, and no path to an order.

Two failure modes matter here and neither is caught by the code running.

The first is a level that cannot break. Trailing resistance at session ``t``
includes ``t``'s own high, so comparing today's close against today's level
detects nothing, ever, and the review silently reports "no levels crossed" for
the rest of time. The offset is asserted directly.

The second is vocabulary drift. The passes were requested using discretionary
terms -- entry zone, trigger, target, stop -- and v1 has no per-trade risk points
at all. A report that grew a ``stop_price`` would be read as an instruction the
strategy will never carry out. The absence is pinned as a property.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.errors import DataIntegrityError
from deskreview.metrics import (
    close_location,
    detect_level_breaks,
    overnight_gap,
    pullback_state,
    true_range,
)
from deskreview.passes import BandState, build_review

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture(scope="module")
def strategy(canonical_config_path):
    return load_strategy_config(canonical_config_path).strategy


@pytest.fixture(scope="module")
def panel(strategy):
    return generate_panel(strategy.to_universe(), sessions=400, seed=5)


@pytest.fixture(scope="module")
def report(strategy, panel):
    return build_review(
        "morning",
        strategy,
        panel,
        current_weights={"SPY": 0.10, "GLD": 0.05},
        config_sha256="c" * 64,
        generated_at=dt.datetime(2026, 8, 10, 13, 0, tzinfo=dt.UTC),
    )


class TestLevelBreaksAreCausalAndCanActuallyFire:
    def test_the_level_comes_from_the_prior_session(self) -> None:
        """The offset that makes a break detectable at all.

        Resistance through session 1 is 10.0. Session 2 closes at 10.5, which is
        above it. Had the comparison used session 2's own level -- which already
        contains session 2's high of 11.0 -- nothing would have been detected.
        """
        closes = np.array([[9.0], [9.5], [10.5]])
        resistance = np.array([[10.0], [10.0], [11.0]])
        support = np.array([[8.0], [8.0], [8.0]])
        (found,) = detect_level_breaks(("SPY",), closes, resistance, support, index=2)
        assert found.kind == "resistance"
        assert found.level == 10.0
        assert found.distance == pytest.approx(0.05)

    def test_a_close_below_trailing_support_is_a_support_break(self) -> None:
        closes = np.array([[9.0], [7.5]])
        resistance = np.array([[10.0], [10.0]])
        support = np.array([[8.0], [8.0]])
        (found,) = detect_level_breaks(("SPY",), closes, resistance, support, index=1)
        assert found.kind == "support"
        assert found.distance == pytest.approx(-0.0625)

    def test_a_close_inside_the_range_breaks_nothing(self) -> None:
        closes = np.array([[9.0], [9.2]])
        resistance = np.array([[10.0], [10.0]])
        support = np.array([[8.0], [8.0]])
        assert detect_level_breaks(("SPY",), closes, resistance, support, index=1) == []

    def test_the_first_session_has_no_prior_level_and_says_so(self) -> None:
        closes = np.array([[9.0]])
        with pytest.raises(DataIntegrityError, match="needs a prior session"):
            detect_level_breaks(("SPY",), closes, closes, closes, index=0)


class TestBarArithmetic:
    def test_true_range_counts_the_gap(self) -> None:
        """A session that gapped 3% then traded flat is not a quiet session.

        high == low == 103 against a prior close of 100: the intrabar range is
        zero and the true range is 3.
        """
        computed = true_range(np.array([103.0]), np.array([103.0]), np.array([100.0]))
        assert computed[0] == pytest.approx(3.0)

    def test_true_range_is_the_intrabar_range_when_there_is_no_gap(self) -> None:
        computed = true_range(np.array([102.0]), np.array([99.0]), np.array([100.0]))
        assert computed[0] == pytest.approx(3.0)

    def test_close_location_spans_the_bar(self) -> None:
        assert close_location(10.0, 10.0, 8.0) == pytest.approx(1.0)
        assert close_location(8.0, 10.0, 8.0) == pytest.approx(0.0)
        assert close_location(9.0, 10.0, 8.0) == pytest.approx(0.5)

    def test_a_zero_range_bar_is_the_middle_not_a_division_by_zero(self) -> None:
        assert close_location(10.0, 10.0, 10.0) == 0.5

    def test_overnight_gap_rejects_a_non_positive_prior_close(self) -> None:
        with pytest.raises(DataIntegrityError, match="finite and positive"):
            overnight_gap(10.0, 0.0)

    def test_overnight_gap_is_a_fraction_of_the_prior_close(self) -> None:
        assert overnight_gap(103.0, 100.0) == pytest.approx(0.03)


class TestPullbackDescription:
    def test_a_narrow_bar_closing_strong_after_a_decline_is_flagged(self) -> None:
        # The final bar is deliberately narrow (0.20 wide against a ~2.0 average)
        # and closes at 90% of its own range, after a decline from 104.
        closes = np.array([100.0, 104.0, 103.0, 102.0, 101.0, 100.5, 100.48])
        highs = np.array([101.0, 105.0, 104.0, 103.0, 102.0, 101.0, 100.50])
        lows = np.array([99.0, 103.0, 102.0, 101.0, 100.0, 99.5, 100.30])
        state = pullback_state("SPY", closes, highs, lows, window=5)
        assert state.drawdown_from_high < 0
        assert state.contracting
        assert state.closing_strong
        assert state.exhausted

    def test_a_short_series_fails_rather_than_guessing(self) -> None:
        tiny = np.array([100.0, 101.0])
        with pytest.raises(DataIntegrityError, match="too few"):
            pullback_state("SPY", tiny, tiny, tiny, window=5)

    def test_drawdown_is_never_positive(self) -> None:
        rising = np.linspace(100.0, 120.0, 30)
        state = pullback_state("SPY", rising, rising * 1.01, rising * 0.99, window=10)
        assert state.drawdown_from_high <= 0.0


class TestTheBandsAreTheRealEntryZone:
    """The review claims to say whether a trade will fire. That claim is only
    worth anything if it agrees with the code that actually fires trades."""

    @staticmethod
    def _runner_would_trade(target: float, current: float, band: float) -> bool:
        """Ask the real ``build_instructions`` whether it emits an order.

        Built through the actual function rather than a restatement of its rule,
        so that if the rebalance logic changes, this test fails instead of
        quietly continuing to assert a stale copy of it.
        """
        from decimal import Decimal

        from amat.config.models import ExecutionConfig, RebalanceConfig
        from amat.domain.positions import Position
        from amat.domain.types import Symbol
        from amat.execution import build_instructions

        symbol = Symbol("SPY")
        equity = Decimal(100_000)
        mark = Decimal(100)
        held = (
            {
                symbol: Position(
                    symbol=symbol,
                    quantity=Decimal(str(current)) * equity / mark,
                    mark=mark,
                    as_of_session=dt.date(2026, 8, 6),
                )
            }
            if current > 0
            else {}
        )
        instructions, _ = build_instructions(
            {symbol: target},
            held,
            {symbol: mark},
            equity,
            signal_session=dt.date(2026, 8, 6),
            execution_session=dt.date(2026, 8, 7),
            rebalance=RebalanceConfig(min_weight_delta=band, min_trade_notional=Decimal("0.01")),
            execution=ExecutionConfig(),
        )
        return bool(instructions)

    @pytest.mark.parametrize(
        ("target", "current"),
        [
            (0.12, 0.10),  # gap at the band, subject to float representation
            (0.119, 0.10),  # gap clearly under the band
            (0.15, 0.10),  # gap clearly over the band
            (0.0133, 0.0),  # tiny ENTRY -- a transition the band alone would miss
            (0.0, 0.0133),  # tiny EXIT -- likewise
            (0.0, 0.0),  # flat and wanted flat
            (0.10, 0.10),  # no gap at all
        ],
    )
    def test_would_trade_agrees_with_the_runner(self, target: float, current: float) -> None:
        band = 0.02
        state = BandState(symbol="SPY", target_weight=target, current_weight=current, band=band)
        assert state.would_trade == self._runner_would_trade(target, current, band), (
            f"review says {state.would_trade} for target={target} current={current}; "
            "build_instructions disagrees"
        )

    def test_a_tiny_entry_still_trades_because_it_crosses_zero(self) -> None:
        """The regression. Reported 'no trade' for a 1.33% USO entry on live
        data while the runner would have executed it."""
        state = BandState(symbol="USO", target_weight=0.0133, current_weight=0.0, band=0.02)
        assert state.band_fraction < 1.0
        assert state.is_transition
        assert state.would_trade

    def test_transitions_name_what_the_gap_is(self) -> None:
        band = 0.02
        entry = BandState(symbol="A", target_weight=0.10, current_weight=0.0, band=band)
        exit_ = BandState(symbol="B", target_weight=0.0, current_weight=0.10, band=band)
        flat = BandState(symbol="C", target_weight=0.0, current_weight=0.0, band=band)
        reduce_ = BandState(symbol="D", target_weight=0.05, current_weight=0.10, band=band)
        assert (entry.transition, exit_.transition) == ("entry", "exit")
        assert (flat.transition, reduce_.transition) == ("flat", "reduce")


class TestTheReportIsHonestAboutWhatV1Lacks:
    def test_no_holding_carries_a_target_or_a_stop(self, report) -> None:
        """v1 defines neither. Reporting an estimate would imply a rule that
        does not exist and will never fire."""
        assert report.holdings
        assert all(holding.target_price is None for holding in report.holdings)
        assert all(holding.stop_price is None for holding in report.holdings)

    def test_every_report_states_it_decides_nothing(self, report) -> None:
        assert any("OBSERVATION ONLY" in caveat for caveat in report.caveats)

    def test_the_absence_of_targets_and_stops_is_stated_not_implied(self, report) -> None:
        joined = " ".join(report.caveats).lower()
        assert "no profit target" in joined
        assert "no stop-loss" in joined

    def test_an_empty_news_store_is_distinguished_from_no_events(self, report) -> None:
        """Silence and absence-of-evidence must not render identically."""
        joined = " ".join(report.caveats)
        assert "not the same as 'no events'" in joined

    def test_no_caveat_claims_predictive_power(self, report) -> None:
        joined = " ".join(report.caveats).lower()
        for banned in ("proven", "guaranteed", "will rise", "will fall", "profitable"):
            assert banned not in joined

    def test_the_targets_shown_are_the_allocators_own(self, strategy, panel, report) -> None:
        """A review that recomputed targets its own way could disagree with the
        strategy silently. It calls the same function the backtest calls."""
        from amat.portfolio import decide

        allocation = decide(
            strategy, panel.adjusted_closes, strategy.to_universe(), decision_index=len(panel) - 1
        )
        expected = {
            str(symbol): float(weight)
            for symbol, weight in allocation.as_mapping(strategy.to_universe()).items()
        }
        assert {band.symbol: band.target_weight for band in report.bands} == expected


class TestCausality:
    def test_mutating_later_sessions_does_not_change_an_earlier_review(
        self, strategy, panel
    ) -> None:
        """The future-mutation control required by .claude/rules/40-testing.md."""
        cutoff = 300
        truncated = type(panel)(
            sessions=panel.sessions[:cutoff],
            symbols=panel.symbols,
            adjusted_closes=panel.adjusted_closes[:cutoff],
            raw_closes=panel.raw_closes[:cutoff],
            window_vwaps=panel.window_vwaps[:cutoff],
            window_volumes=panel.window_volumes[:cutoff],
            source=panel.source,
            adjusted_highs=panel.adjusted_highs[:cutoff],
            adjusted_lows=panel.adjusted_lows[:cutoff],
            session_volumes=panel.session_volumes[:cutoff],
        )
        mutated_closes = panel.adjusted_closes.copy()
        mutated_closes[cutoff:] *= 3.0
        mutated = type(panel)(
            sessions=panel.sessions,
            symbols=panel.symbols,
            adjusted_closes=mutated_closes,
            raw_closes=panel.raw_closes,
            window_vwaps=panel.window_vwaps,
            window_volumes=panel.window_volumes,
            source=panel.source,
            adjusted_highs=panel.adjusted_highs,
            adjusted_lows=panel.adjusted_lows,
            session_volumes=panel.session_volumes,
        )
        mutated_truncated = type(panel)(
            sessions=mutated.sessions[:cutoff],
            symbols=mutated.symbols,
            adjusted_closes=mutated.adjusted_closes[:cutoff],
            raw_closes=mutated.raw_closes[:cutoff],
            window_vwaps=mutated.window_vwaps[:cutoff],
            window_volumes=mutated.window_volumes[:cutoff],
            source=mutated.source,
            adjusted_highs=mutated.adjusted_highs[:cutoff],
            adjusted_lows=mutated.adjusted_lows[:cutoff],
            session_volumes=mutated.session_volumes[:cutoff],
        )

        kwargs = {
            "current_weights": {},
            "config_sha256": "c" * 64,
            "generated_at": dt.datetime(2026, 8, 10, tzinfo=dt.UTC),
        }
        first = build_review("morning", strategy, truncated, **kwargs)
        second = build_review("morning", strategy, mutated_truncated, **kwargs)
        assert first.as_dict()["bands"] == second.as_dict()["bands"]
        assert first.as_dict()["level_breaks"] == second.as_dict()["level_breaks"]

    def test_a_panel_without_levels_fails_closed(self, strategy, panel) -> None:
        """The tempting fallback -- substitute closes for highs -- produces a
        support level that is the lowest close, which is a different and usually
        higher number. It would compute, look plausible, and be wrong."""
        bare = type(panel)(
            sessions=panel.sessions,
            symbols=panel.symbols,
            adjusted_closes=panel.adjusted_closes,
            raw_closes=panel.raw_closes,
            window_vwaps=panel.window_vwaps,
            window_volumes=panel.window_volumes,
            source=panel.source,
        )
        with pytest.raises(DataIntegrityError, match="highs, lows and volume"):
            build_review(
                "morning",
                strategy,
                bare,
                current_weights={},
                config_sha256="c" * 64,
                generated_at=dt.datetime(2026, 8, 10, tzinfo=dt.UTC),
            )


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


class TestTheReviewCannotReachAnAccount:
    """The dependency runs one way, and stops short of execution."""

    @pytest.mark.parametrize("path", sorted((SRC / "amat").rglob("*.py")), ids=lambda p: p.name)
    def test_amat_never_imports_deskreview(self, path: Path) -> None:
        offenders = [name for name in _imports(path) if name.split(".")[0] == "deskreview"]
        assert not offenders, (
            f"{path.name} imports {offenders}; the core must not depend on a review"
        )

    @pytest.mark.parametrize(
        "path", sorted((SRC / "deskreview").rglob("*.py")), ids=lambda p: p.name
    )
    def test_the_review_imports_no_execution_machinery(self, path: Path) -> None:
        """It may read context, portfolio, and config. It may not build orders.

        ``amat.execution`` constructs instructions and ``amat.planning`` builds
        the execution plan an executor consumes. A review that imported either
        would be one edit away from emitting something tradable.
        """
        forbidden = {"amat.execution", "amat.planning", "amat.simulation"}
        offenders = [name for name in _imports(path) if name in forbidden]
        assert not offenders, f"{path.name} imports {offenders}"

    @pytest.mark.parametrize(
        "path", sorted((SRC / "deskreview").rglob("*.py")), ids=lambda p: p.name
    )
    def test_the_review_performs_no_network_transport(self, path: Path) -> None:
        transport = {"urllib", "http", "socket", "ssl", "requests", "httpx", "aiohttp"}
        offenders = {name.split(".")[0] for name in _imports(path)} & transport
        assert not offenders, f"{path.name} imports network transport {sorted(offenders)}"

    @pytest.mark.parametrize(
        "path", sorted((SRC / "deskreview").rglob("*.py")), ids=lambda p: p.name
    )
    def test_no_state_changing_http_verb_appears(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        found = [verb for verb in ("POST", "PUT", "PATCH", "DELETE") if f'"{verb}"' in text]
        assert not found, f"{path.name} contains HTTP method literal(s) {found}"
