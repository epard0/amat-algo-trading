"""Episode diagnostics and the PDF report.

The diagnostics' one hard invariant: everything they say is replayed from the
run's own artifacts and reconciled against the ledger before being reported.
The PDF's: it renders without recomputing anything, and its page set is
complete.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np
import pytest

from amat.backtest.attribution import ClosedTrade
from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.domain.actions import ActionKind, CorporateAction
from amat.errors import DataIntegrityError
from amat.metrics import summarize
from amat.reporting import build_report
from amat.reporting.insights import (
    _classify_trades,
    build_insights,
    reconstruct_weights,
)
from amat.reporting.pdf import render_study_pdf

SESSIONS = 330


@pytest.fixture(scope="module")
def strategy(canonical_config_path):
    return load_strategy_config(canonical_config_path).strategy


@pytest.fixture(scope="module")
def panel(strategy):
    return generate_panel(strategy.to_universe(), sessions=SESSIONS, seed=11, trend_strength=0.6)


@pytest.fixture(scope="module")
def result(strategy, panel):
    return run_backtest(strategy, panel, strategy.to_universe(), initial_cash=Decimal("1200000"))


@pytest.fixture(scope="module")
def insights(result, panel, strategy):
    clusters = {
        cluster.name: [str(symbol) for symbol in cluster.symbols]
        for cluster in strategy.universe.clusters
    }
    return build_insights(result, panel, clusters=clusters)


class TestFillsCarryTheirRationale:
    def test_every_fill_the_runner_produces_names_its_reason(self, result) -> None:
        """The PDF annotates trades with why they happened; that is only honest
        if the reason rides on the fill record itself."""
        from amat.domain.orders import TradeReason

        fills = [fill for record in result.sessions for fill in record.fills]
        assert fills, "fixture must produce trades"
        assert all(isinstance(fill.reason, TradeReason) for fill in fills)

    def test_a_directly_built_fill_defaults_to_unrecorded_not_guessed(self) -> None:
        from amat.domain.fills import Fill
        from amat.domain.types import Side

        fill = Fill("SPY", dt.date(2024, 1, 5), Side.BUY, Decimal(5), Decimal(100), Decimal(1))
        assert fill.reason is None


class TestWeightReconstruction:
    def test_row_sums_match_the_ledgers_own_gross_exposure(self, result, panel) -> None:
        """The replayed weights must agree with the ledger, session by session.

        ``gross_exposure`` on the session record is the ledger's own statement
        of position value over equity; the replay derives the same number from
        fills alone. Agreement is what authorizes annotating charts with it.
        """
        weights, max_error = reconstruct_weights(result, panel)
        assert weights.shape == (len(result.sessions), len(panel.symbols))
        assert np.all(weights >= 0.0)
        for row, record in enumerate(result.sessions):
            assert weights[row].sum() == pytest.approx(record.gross_exposure, abs=1e-9)
        assert max_error < 1e-9

    def test_a_split_the_run_never_saw_fails_reconciliation(self, result, panel) -> None:
        """Wrong inputs must refuse loudly, not draw a wrong chart."""
        weights, _ = reconstruct_weights(result, panel)
        rows, cols = np.nonzero(weights > 0.05)
        assert rows.size, "fixture must hold at least one position"
        # Pick a held (session, symbol) and fabricate a split there. The replay
        # doubles the shares; the ledger did not; reconciliation must fail.
        row, col = int(rows[-1]), int(cols[-1])
        session = result.sessions[row].session
        symbol = str(panel.symbols[col])
        bogus = {
            session: (
                CorporateAction(
                    symbol=symbol,
                    session_date=session,
                    kind=ActionKind.SPLIT,
                    ratio=Decimal(2),
                ),
            )
        }
        with pytest.raises(DataIntegrityError, match="disagrees with ledger"):
            reconstruct_weights(result, panel, actions_by_session=bogus)

    def test_build_insights_passes_splits_to_both_replays(
        self, result, panel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Episode replay must see the same actions as holdings replay.

        A forward split can make a legitimate later sale exceed the quantity
        inferred from fills alone. This wiring regression was exposed by XLE's
        2025-12-16 sale in the wide-universe report.
        """
        import amat.reporting.insights as insights_module

        actions: dict[dt.date, tuple[CorporateAction, ...]] = {}
        seen: list[object] = []
        original_reconstruct = insights_module.reconstruct_weights
        original_replay = insights_module.replay_trades

        def reconstruct_with_capture(*args, **kwargs):
            seen.append(kwargs.get("actions_by_session"))
            return original_reconstruct(*args, **kwargs)

        def replay_with_capture(*args, **kwargs):
            seen.append(kwargs.get("actions_by_session"))
            return original_replay(*args, **kwargs)

        monkeypatch.setattr(insights_module, "reconstruct_weights", reconstruct_with_capture)
        monkeypatch.setattr(insights_module, "replay_trades", replay_with_capture)

        build_insights(result, panel, actions_by_session=actions)

        assert seen == [actions, actions]


def _trade(**overrides) -> ClosedTrade:
    payload = {
        "symbol": "SPY",
        "entry_session": dt.date(2024, 1, 5),
        "exit_session": dt.date(2024, 3, 5),
        "quantity": Decimal(100),
        "entry_price": Decimal(100),
        "exit_price": Decimal(110),
        "gross_pnl": Decimal(1000),
        "costs": Decimal(10),
        "net_pnl": Decimal(990),
        "initial_risk": None,
        "max_favourable": Decimal(1200),
        "max_adverse": Decimal(-100),
        "sessions_held": 40,
    }
    payload.update(overrides)
    return ClosedTrade(**payload)


class TestEpisodeClassification:
    def test_a_large_winner_is_a_captured_trend(self) -> None:
        captured, whipsaws, _ = _classify_trades([_trade()])
        assert [episode.kind for episode in captured] == ["captured_trend"]
        assert not whipsaws

    def test_a_quick_loss_is_a_whipsaw_and_a_slow_loss_is_not(self) -> None:
        quick = _trade(
            net_pnl=Decimal(-500),
            gross_pnl=Decimal(-490),
            sessions_held=10,
            max_favourable=Decimal(50),
        )
        slow = _trade(
            symbol="QQQ",
            net_pnl=Decimal(-500),
            gross_pnl=Decimal(-490),
            sessions_held=22,
            max_favourable=Decimal(50),
        )
        _, whipsaws, _ = _classify_trades([quick, slow])
        assert len(whipsaws) == 1
        assert whipsaws[0].sessions_held == 10

    def test_partial_exits_coalesce_so_a_peak_is_counted_once(self) -> None:
        """TradeLedger emits one ClosedTrade per sell, each carrying the
        position's full excursion. Classified independently, one position's
        peak would be counted once per partial exit -- measured on the full v1
        sample this inflated total surrendered roughly eight-fold."""
        first_exit = _trade(
            net_pnl=Decimal(100),
            gross_pnl=Decimal(105),
            max_favourable=Decimal(2000),
            exit_session=dt.date(2024, 2, 5),
            sessions_held=20,
        )
        second_exit = _trade(
            net_pnl=Decimal(50),
            gross_pnl=Decimal(55),
            max_favourable=Decimal(2000),
            exit_session=dt.date(2024, 3, 5),
            sessions_held=40,
        )
        _, _, givebacks = _classify_trades([first_exit, second_exit])
        assert len(givebacks) == 1
        assert givebacks[0].peak_open_pnl == 2000.0
        assert givebacks[0].net_pnl == 150.0
        assert givebacks[0].exit_session == "2024-03-05"

    def test_surrendering_most_of_a_peak_is_a_giveback(self) -> None:
        gave = _trade(net_pnl=Decimal(100), gross_pnl=Decimal(110), max_favourable=Decimal(900))
        kept = _trade(
            symbol="QQQ",
            net_pnl=Decimal(800),
            gross_pnl=Decimal(810),
            max_favourable=Decimal(900),
        )
        _, _, givebacks = _classify_trades([gave, kept])
        assert len(givebacks) == 1
        assert givebacks[0].net_pnl == 100.0

    def test_totals_are_sums_of_their_episodes(self, insights) -> None:
        assert insights.whipsaw_total_cost == pytest.approx(
            sum(episode.net_pnl for episode in insights.whipsaws)
        )
        assert insights.giveback_total_surrendered == pytest.approx(
            sum(episode.peak_open_pnl - episode.net_pnl for episode in insights.givebacks)
        )

    def test_the_serialized_form_carries_no_arrays(self, insights) -> None:
        import json

        payload = insights.as_dict()
        assert "weights" not in payload
        json.dumps(payload)  # must be a plain, serializable structure


class TestTablesAreRenderedNotTyped:
    """The report's tabular pages draw real table artists.

    The previous renderer printed monospaced text with dash rules for borders;
    these tests pin the replacement so a regression back to ASCII tables
    cannot pass silently.
    """

    def test_a_table_is_a_table_artist_with_styled_header_and_numerals(self) -> None:
        import matplotlib.pyplot as plt
        from matplotlib.table import Table

        from amat.reporting.pdf import _draw_table

        figure, axis = plt.subplots()
        try:
            _draw_table(
                axis,
                columns=("symbol", "net P&L"),
                rows=[("SPY", "1,234"), ("TLT", "-56")],
                numeric_columns=(1,),
            )
            tables = [child for child in axis.get_children() if isinstance(child, Table)]
            assert tables, "expected a real Table artist, not text"
            cells = tables[0].get_celld()
            assert cells[(0, 0)].get_text().get_fontweight() == "bold"
            assert cells[(1, 1)].get_text().get_horizontalalignment() == "right"
            assert cells[(1, 0)].get_text().get_horizontalalignment() == "left"
        finally:
            plt.close(figure)

    def test_an_empty_section_renders_a_labelled_absence(self) -> None:
        import matplotlib.pyplot as plt
        from matplotlib.table import Table

        from amat.reporting.pdf import _draw_table

        figure, axis = plt.subplots()
        try:
            _draw_table(axis, columns=("symbol",), rows=[])
            assert not [child for child in axis.get_children() if isinstance(child, Table)]
            assert "none in this sample" in [text.get_text() for text in axis.texts]
        finally:
            plt.close(figure)


class TestPdfReport:
    def test_the_document_contains_every_page(self, tmp_path, result, panel, insights) -> None:
        report = build_report(
            result,
            strategy="amat-etf v1.0.0",
            config_sha256="0" * 64,
            data_source="synthetic",
            engine="v1-frozen",
            caveats=("SYNTHETIC DATA -- not a market result.",),
        )
        target = tmp_path / "study.pdf"
        render_study_pdf(
            target,
            report=report,
            metrics=summarize(result),
            sleeves=(),
            result=result,
            panel=panel,
            insights=insights,
        )
        raw = target.read_bytes()
        assert raw.startswith(b"%PDF")
        # headline + comparison + cumulative + portfolio value + one per
        # symbol + stats + two episode-diagnostics table pages. Exact count:
        # the substring "/Type /Page" also matches the document's single
        # "/Type /Pages" tree node, so it is subtracted -- a >= comparison
        # would absorb one missing page.
        expected_pages = 4 + len(panel.symbols) + 3
        pages = raw.count(b"/Type /Page") - raw.count(b"/Type /Pages")
        assert pages == expected_pages

    def test_an_advisory_page_is_labelled_as_such(self, tmp_path, result, panel, insights) -> None:
        report = build_report(
            result,
            strategy="amat-etf v1.0.0",
            config_sha256="0" * 64,
            data_source="synthetic",
            engine="v1-frozen",
        )
        with_advisory = tmp_path / "advisory.pdf"
        without = tmp_path / "plain.pdf"
        for target, advisory in ((without, None), (with_advisory, "Consider nothing.")):
            render_study_pdf(
                target,
                report=report,
                metrics=summarize(result),
                sleeves=(),
                result=result,
                panel=panel,
                insights=insights,
                advisory=advisory,
                advisory_provenance="model test, requested 2026-08-11",
            )
        assert with_advisory.read_bytes().count(b"/Type /Page") > without.read_bytes().count(
            b"/Type /Page"
        )
