"""Per-asset attribution, concentration gates, and report rendering."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from amat.backtest.attribution import ClosedTrade
from amat.errors import DataIntegrityError
from amat.reporting import (
    MAX_SYMBOL_PNL_SHARE,
    MAX_YEAR_PNL_SHARE,
    attribute_by_asset,
    build_report,
    render_html,
    render_text,
)

ENTRY = dt.date(2020, 1, 2)
EXIT = dt.date(2020, 3, 2)


def trade(
    symbol: str,
    net: str,
    *,
    gross: str | None = None,
    costs: str = "1",
    risk: str | None = "100",
    entry: dt.date = ENTRY,
    exit_session: dt.date = EXIT,
) -> ClosedTrade:
    net_value = Decimal(net)
    gross_value = Decimal(gross) if gross is not None else net_value + Decimal(costs)
    return ClosedTrade(
        symbol=symbol,
        entry_session=entry,
        exit_session=exit_session,
        quantity=Decimal("10"),
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        gross_pnl=gross_value,
        costs=Decimal(costs),
        net_pnl=net_value,
        initial_risk=Decimal(risk) if risk is not None else None,
        max_favourable=Decimal("50"),
        max_adverse=Decimal("-20"),
        sessions_held=7,
    )


class TestAssetAttribution:
    def test_groups_by_symbol_and_sorts_by_net_pnl(self) -> None:
        assets = attribute_by_asset([trade("SPY", "100"), trade("QQQ", "300"), trade("SPY", "50")])
        assert [a.symbol for a in assets] == ["QQQ", "SPY"]
        assert assets[0].net_pnl == pytest.approx(300.0)
        assert assets[1].net_pnl == pytest.approx(150.0)
        assert assets[1].trades == 2

    def test_shares_sum_to_one_when_all_contributions_are_positive(self) -> None:
        assets = attribute_by_asset([trade("SPY", "100"), trade("QQQ", "300"), trade("IWM", "100")])
        assert sum(a.pnl_share for a in assets) == pytest.approx(1.0)

    def test_a_losing_asset_reports_a_negative_share(self) -> None:
        """A losing instrument must not appear as a positive contributor."""
        assets = attribute_by_asset([trade("SPY", "300"), trade("EFA", "-100")])
        losing = next(a for a in assets if a.symbol == "EFA")
        assert losing.net_pnl < 0
        assert losing.pnl_share < 0

    def test_win_rate_and_extremes(self) -> None:
        assets = attribute_by_asset([trade("SPY", "100"), trade("SPY", "-40"), trade("SPY", "20")])
        asset = assets[0]
        assert asset.wins == 2
        assert asset.win_rate == pytest.approx(2 / 3)
        assert asset.best_trade == pytest.approx(100.0)
        assert asset.worst_trade == pytest.approx(-40.0)

    def test_average_r_is_none_when_no_trade_recorded_risk(self) -> None:
        assets = attribute_by_asset([trade("SPY", "100", risk=None)])
        assert assets[0].average_r is None

    def test_hold_is_measured_on_the_run_calendar(self) -> None:
        """Replayed trades carry sessions_held=0; the calendar must be preferred."""
        calendar = {ENTRY: 10, EXIT: 25}
        replayed = trade("SPY", "100")
        object.__setattr__(replayed, "sessions_held", 0)
        assets = attribute_by_asset([replayed], session_index=calendar)
        assert assets[0].average_hold_sessions == pytest.approx(15.0)

    def test_empty_input_yields_no_assets(self) -> None:
        assert attribute_by_asset([]) == ()


class _StubResult:
    """Minimal stand-in for a run result, exposing only what a report reads."""

    def __init__(self, equity: list[float], trades: list[ClosedTrade]) -> None:
        start = dt.date(2020, 1, 1)
        self.sessions = tuple(
            _StubSession(start + dt.timedelta(days=index), Decimal(str(value)))
            for index, value in enumerate(equity)
        )
        self.trades = tuple(trades)
        self.initial_equity = Decimal(str(equity[0]))
        self.one_way_bps = 2.0
        self.is_primary = True
        self.total_fills = len(trades) * 2
        self.total_missed = 0
        self.total_costs = sum((t.costs for t in trades), start=Decimal(0))


class _StubSession:
    def __init__(self, session: dt.date, equity: Decimal) -> None:
        self.session = session
        self.equity = equity
        self.fills = ()


class TestBuildReport:
    def _report(self, **overrides: object):
        equity = overrides.pop("equity", [100000.0, 105000.0, 102000.0, 110000.0])
        trades = overrides.pop("trades", [trade("SPY", "6000"), trade("QQQ", "4000")])
        return build_report(
            _StubResult(equity, trades),  # type: ignore[arg-type]
            strategy="amat-etf v1.0.0",
            config_sha256="a" * 64,
            data_source="synthetic",
            engine="v1-frozen",
            caveats=("synthetic data",),
        )

    def test_headline_fields(self) -> None:
        report = self._report()
        assert report.initial_equity == pytest.approx(100000.0)
        assert report.final_equity == pytest.approx(110000.0)
        assert report.total_return == pytest.approx(0.10)

    def test_drawdown_is_non_positive_and_finds_the_trough(self) -> None:
        report = self._report()
        assert report.max_drawdown < 0
        # 105000 -> 102000 is the only decline: -2.857%
        assert report.max_drawdown == pytest.approx(-3000 / 105000)

    def test_caveats_are_carried_onto_the_report(self) -> None:
        assert "synthetic data" in self._report().caveats

    def test_too_short_a_run_fails_closed(self) -> None:
        with pytest.raises(DataIntegrityError, match="at least 2 sessions"):
            self._report(equity=[100000.0])

    def test_report_serializes(self) -> None:
        payload = self._report().as_dict()
        assert payload["strategy"] == "amat-etf v1.0.0"
        assert "assets" in payload and "concentration" in payload


class TestConcentrationGates:
    def _gates(self, trades: list[ClosedTrade]):
        return build_report(
            _StubResult([100000.0, 110000.0, 120000.0], trades),  # type: ignore[arg-type]
            strategy="s",
            config_sha256="a" * 64,
            data_source="synthetic",
            engine="v1",
        ).concentration

    def test_a_single_dominant_symbol_fails_the_gate(self) -> None:
        gates = self._gates([trade("SPY", "9000"), trade("QQQ", "100")])
        assert gates.best_symbol == "SPY"
        assert gates.best_symbol_share > MAX_SYMBOL_PNL_SHARE
        assert gates.symbol_gate_passed is False
        assert gates.all_passed is False

    def test_an_evenly_spread_book_passes_the_symbol_gate(self) -> None:
        gates = self._gates([trade(sym, "1000") for sym in ("SPY", "QQQ", "IWM", "EFA", "TLT")])
        assert gates.best_symbol_share == pytest.approx(0.2)
        assert gates.symbol_gate_passed is True

    def test_gates_are_reported_even_when_they_fail(self) -> None:
        """A report that hid a failing gate would be selective reporting."""
        gates = self._gates([trade("SPY", "9000")])
        assert gates.symbol_gate_passed is False
        assert gates.best_symbol == "SPY"

    def test_thresholds_match_the_promotion_gates_document(self) -> None:
        assert MAX_SYMBOL_PNL_SHARE == 0.25
        assert MAX_YEAR_PNL_SHARE == 0.40


class TestRendering:
    def _report(self):
        return build_report(
            _StubResult(  # type: ignore[arg-type]
                [100000.0, 105000.0, 102000.0, 110000.0],
                [trade("SPY", "6000"), trade("EFA", "-500")],
            ),
            strategy="amat-etf v1.0.0",
            config_sha256="b" * 64,
            data_source="synthetic",
            engine="v1-frozen",
            caveats=("SYNTHETIC DATA -- not a market result",),
        )

    def test_text_report_states_its_caveats_and_sample(self) -> None:
        text = render_text(self._report())
        assert "SYNTHETIC DATA" in text
        assert "CONCENTRATION GATES" in text
        assert "BY ASSET" in text
        assert "SPY" in text and "EFA" in text

    def test_text_report_never_claims_profitability(self) -> None:
        """Guardrail 2: no report may describe a result as proven or profitable."""
        text = render_text(self._report()).lower()
        for banned in ("proven", "guaranteed", "risk-free", "will profit"):
            assert banned not in text

    def test_html_is_self_contained(self) -> None:
        from amat.metrics import PerformanceSummary

        report = self._report()
        metrics = PerformanceSummary(
            sessions=4,
            first_session=report.first_session,
            last_session=report.last_session,
            initial_equity=100000.0,
            final_equity=110000.0,
            total_return=0.1,
            annualized_return=0.1,
            annualized_volatility=0.05,
            sharpe=2.0,
            max_drawdown=-0.03,
            max_drawdown_start=report.first_session,
            max_drawdown_trough=report.last_session,
            hit_rate=0.6,
            average_gross_exposure=0.5,
            total_fills=4,
            total_missed_fills=0,
            total_costs=2.0,
            cost_drag_annualized=0.0,
            one_way_bps=2.0,
            is_primary=True,
        )
        html = render_html([(report, metrics)])
        assert "<svg" in html and "viz-root" in html
        # Self-contained: no external asset references.
        for external in ("http://", "https://", "<script src", "<link rel"):
            assert external not in html

    def test_html_requires_at_least_one_report(self) -> None:
        with pytest.raises(DataIntegrityError, match="at least one report"):
            render_html([])
