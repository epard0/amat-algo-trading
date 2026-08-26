"""The simulated paper account: offline replay, journalling, and caveats."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from amat.config.models import StrategyConfig
from amat.data.alpaca import DailyBar, WindowBar
from amat.data.snapshot import write_snapshot
from amat.simulation import (
    SimulatedLiveAccount,
    extend_panel_to_present,
    latest_completed_session,
)

SYMBOLS = ("SPY", "TLT", "GLD")
SESSION_COUNT = 320


def _sessions(count: int = SESSION_COUNT, start: dt.date = dt.date(2023, 1, 2)) -> list[dt.date]:
    """Consecutive weekday-ish dates; the calendar only has to be increasing."""
    return [start + dt.timedelta(days=index) for index in range(count)]


def _prices(count: int, seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    steps = generator.normal(0.0004, 0.009, size=count)
    return 100.0 * np.exp(np.cumsum(steps))


def _snapshot(root: Path, snapshot_id: str = "sim-snapshot"):
    sessions = _sessions()
    raw: list[DailyBar] = []
    adjusted: list[DailyBar] = []
    windows: list[WindowBar] = []
    for index, symbol in enumerate(SYMBOLS):
        series = _prices(len(sessions), seed=index + 1)
        for session, price in zip(sessions, series, strict=True):
            raw.append(
                DailyBar(
                    symbol=symbol,
                    session_date=session,
                    open=price * 0.999,
                    high=price * 1.004,
                    low=price * 0.996,
                    close=price,
                    volume=2_000_000.0,
                    vwap=price,
                    trade_count=9000,
                )
            )
            adjusted.append(
                DailyBar(
                    symbol=symbol,
                    session_date=session,
                    open=price * 0.999,
                    high=price * 1.004,
                    low=price * 0.996,
                    close=price * 0.98,
                    volume=2_000_000.0,
                    vwap=price,
                    trade_count=9000,
                )
            )
            windows.append(
                WindowBar(
                    symbol=symbol,
                    session_date=session,
                    window_start=dt.datetime.combine(session, dt.time(14, 30), tzinfo=dt.UTC),
                    window_end=dt.datetime.combine(session, dt.time(14, 35), tzinfo=dt.UTC),
                    vwap=price,
                    volume=90_000.0,
                )
            )
    return write_snapshot(
        root,
        snapshot_id,
        raw_bars=raw,
        adjusted_bars=adjusted,
        window_bars=windows,
        dividends=[],
        splits=[],
        provider="test",
        feed="test-feed",
        as_of=dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
        execution_window_start=dt.time(9, 30),
        execution_window_end=dt.time(9, 35),
        exchange_timezone="America/New_York",
    )


def _strategy() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "sim-test",
            "version": "0.0.1",
            "universe": {
                "clusters": [
                    {"name": "equity", "gross_cap": 0.6, "symbols": ["SPY"]},
                    {"name": "rates", "gross_cap": 0.6, "symbols": ["TLT"]},
                    {"name": "metals", "gross_cap": 0.6, "symbols": ["GLD"]},
                ]
            },
        }
    )


class TestLatestCompletedSession:
    def test_it_never_returns_the_current_date(self) -> None:
        """A session still in progress is not a completed observation."""
        now = dt.datetime(2026, 8, 8, 18, 0, tzinfo=dt.UTC)
        assert latest_completed_session(now=now) == dt.date(2026, 8, 7)


class _StubClient:
    """A client that reports no sessions beyond the snapshot."""

    def __init__(self) -> None:
        self.daily_calls = 0

    def daily_bars(self, *args: object, **kwargs: object) -> list[DailyBar]:
        self.daily_calls += 1
        return []


class TestExtendPanel:
    def test_no_new_sessions_leaves_the_panel_untouched(self, tmp_path: Path) -> None:
        directory, _ = _snapshot(tmp_path)
        client = _StubClient()
        panel, manifest, appended = extend_panel_to_present(
            directory,
            client,  # type: ignore[arg-type]
            list(SYMBOLS),
            strategy=_strategy(),
            through=dt.date(2030, 1, 1),
        )
        assert appended == 0
        assert len(panel) == SESSION_COUNT
        assert manifest.snapshot_id == "sim-snapshot"

    def test_a_through_date_inside_the_snapshot_skips_the_network(self, tmp_path: Path) -> None:
        """Nothing is fetched when the snapshot already covers the request."""
        directory, _ = _snapshot(tmp_path)
        client = _StubClient()
        _, _, appended = extend_panel_to_present(
            directory,
            client,  # type: ignore[arg-type]
            list(SYMBOLS),
            strategy=_strategy(),
            through=dt.date(2023, 6, 1),
        )
        assert appended == 0
        assert client.daily_calls == 0


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    root = tmp_path_factory.mktemp("sim")
    directory, _ = _snapshot(root)
    account = SimulatedLiveAccount(
        _strategy(),
        snapshot_directory=directory,
        config_sha256="c" * 64,
        client=None,
        initial_equity=Decimal("100000"),
        journal_directory=root / "journal",
    )
    return account.step(), root


class TestSimulatedAccount:
    def test_it_reports_the_last_completed_session(self, report) -> None:
        observation, _ = report
        assert observation.as_of_session == _sessions()[-1]

    def test_offline_mode_appends_nothing(self, report) -> None:
        observation, _ = report
        assert observation.appended_sessions == 0

    def test_equity_and_cash_reconcile_within_the_account(self, report) -> None:
        observation, _ = report
        held = sum(observation.positions.values())
        implied = float(observation.cash) / float(observation.equity) + held
        assert implied == pytest.approx(1.0, abs=1e-6)

    def test_every_report_states_that_it_is_simulated(self, report) -> None:
        """Guardrail 2: a paper result must never read as a real position."""
        observation, _ = report
        assert any("SIMULATED PAPER ACCOUNT" in caveat for caveat in observation.caveats)

    def test_no_caveat_claims_profitability(self, report) -> None:
        observation, _ = report
        joined = " ".join(observation.caveats).lower()
        for banned in ("proven", "guaranteed", "risk-free", "will profit"):
            assert banned not in joined

    def test_the_observation_is_journalled_as_json_lines(self, report) -> None:
        observation, root = report
        path = root / "journal" / f"{observation.as_of_session.isoformat()}.jsonl"
        assert path.is_file()
        payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert payload["as_of_session"] == observation.as_of_session.isoformat()
        assert payload["snapshot_id"] == "sim-snapshot"

    def test_replay_is_deterministic(self, tmp_path: Path) -> None:
        """Two steps over the same data must produce the same account."""
        directory, _ = _snapshot(tmp_path)
        strategy = _strategy()
        observations = [
            SimulatedLiveAccount(
                strategy,
                snapshot_directory=directory,
                config_sha256="c" * 64,
                client=None,
                initial_equity=Decimal("100000"),
            ).step()
            for _ in range(2)
        ]
        assert observations[0].equity == observations[1].equity
        assert observations[0].positions == observations[1].positions


class TestLiveFillJournal:
    def test_offline_reports_have_no_live_fills(self, report) -> None:
        # Every session an offline replay produces is covered by the snapshot,
        # so nothing qualifies as a paper-period trade.
        observation, root = report
        assert observation.live_fills == ()
        path = root / "journal" / f"{observation.as_of_session.isoformat()}.jsonl"
        payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert payload["live_fills"] == []

    def test_live_fills_cover_exactly_the_sessions_past_the_snapshot(self, tmp_path: Path) -> None:
        """The paper period's trade log: every fill after the boundary, none
        from covered history, quantities as exact strings."""
        from amat.backtest.runner import run_backtest
        from amat.data.snapshot import load_panel

        directory, _ = _snapshot(tmp_path)
        strategy = _strategy()
        symbols = [str(symbol) for symbol in strategy.universe.symbols]
        panel, manifest = load_panel(directory, symbols)
        account = SimulatedLiveAccount(
            strategy, snapshot_directory=directory, config_sha256="c" * 64, client=None
        )
        result = run_backtest(
            strategy, panel, strategy.to_universe(), initial_cash=Decimal("100000")
        )
        # Place the boundary just before the replay's last trading session,
        # so the doctored "paper period" is guaranteed to contain fills.
        filled = [record.session for record in result.sessions if record.fills]
        assert filled, "the replay produced no fills at all"
        sessions = list(panel.sessions)
        boundary_index = sessions.index(filled[-1]) - 1
        boundary = sessions[boundary_index]
        appended = len(sessions) - 1 - boundary_index
        doctored = manifest.model_copy(update={"last_session": boundary})
        observation = account._build_report(result, panel, doctored, appended)
        expected = sum(len(record.fills) for record in result.sessions if record.session > boundary)
        assert expected > 0, "the tail must trade for this fixture to test anything"
        assert len(observation.live_fills) == expected
        for row in observation.live_fills:
            assert dt.date.fromisoformat(row["execution_session"]) > boundary
            assert row["side"] in {"buy", "sell"}
            assert Decimal(row["quantity"]) > 0


class TestNextSessionTargetsAreADecision:
    """Regression: the field was assigned the account's current positions.

    ``next_session_targets`` is documented as the weights the strategy would seek
    in the next execution window, and it is the one field an executor reads to
    decide what to trade. Aliasing it to ``positions`` made it report what was
    already held, so anyone diffing the two to find trades would have found none
    -- silently, forever, and most convincingly when the account was flat and
    both were empty.
    """

    def test_it_is_the_engine_decision_not_the_current_holdings(self, tmp_path: Path) -> None:
        from amat.data.snapshot import load_panel
        from amat.portfolio import decide

        directory, _ = _snapshot(tmp_path)
        strategy = _strategy()
        observation = SimulatedLiveAccount(
            strategy,
            snapshot_directory=directory,
            config_sha256="c" * 64,
            client=None,
            initial_equity=Decimal("100000"),
        ).step()

        panel, _ = load_panel(directory, list(SYMBOLS))
        universe = strategy.to_universe()
        allocation = decide(
            strategy, panel.adjusted_closes, universe, decision_index=len(panel) - 1
        )
        expected = {
            str(symbol): float(weight)
            for symbol, weight in allocation.as_mapping(universe).items()
            if weight > 0
        }
        assert observation.next_session_targets == expected

    def test_the_field_is_populated_from_a_formed_decision(self, tmp_path: Path) -> None:
        """A decision must exist to report; absence would be the old defect."""
        directory, _ = _snapshot(tmp_path)
        observation = SimulatedLiveAccount(
            _strategy(),
            snapshot_directory=directory,
            config_sha256="c" * 64,
            client=None,
            initial_equity=Decimal("100000"),
        ).step()
        assert isinstance(observation.next_session_targets, dict)
        assert all(weight > 0 for weight in observation.next_session_targets.values())
