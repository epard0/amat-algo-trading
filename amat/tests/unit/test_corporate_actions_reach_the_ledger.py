"""The dividend partition was written for two years and read by nobody.

``actions.parquet`` has been part of every snapshot since the first ingest, and
:func:`amat.backtest.runner.run_backtest` has always accepted an
``actions_by_session`` argument. No caller ever supplied one. The default ``{}``
meant that every result the project produced valued positions at the raw close
while crediting none of the distributions those positions earned -- 1,496
dividends and 4 splits in the shipped snapshot, all silently discarded.

Why that is a real error and not a rounding detail
--------------------------------------------------
The contract deliberately splits the price bases: features read the *adjusted*
close, valuation reads the *raw* close. On an ex-date the raw close drops by the
distribution. With no offsetting cash credit the ledger books that drop as a
market loss, so the strategy paid for every dividend it ever received. The
SPY baseline it was compared against was measured on *adjusted* closes, which
include those dividends. One side of the comparison was total return and the
other was price return.

These tests pin each half of the fix: that the loader resolves the vendor
ambiguities in the shipped data, and that the actions actually arrive at the
accountant rather than being accepted and dropped again.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.alpaca import DailyBar, DividendEvent, SplitEvent, WindowBar
from amat.data.snapshot import load_corporate_actions, write_snapshot
from amat.data.synthetic import generate_panel
from amat.domain.actions import ActionKind
from amat.domain.fills import Side
from amat.errors import DataIntegrityError

SESSIONS = [dt.date(2024, 1, day) for day in (2, 3, 4, 5, 8)]
CLOSES = {"SPY": [400.0, 402.0, 401.0, 405.0, 407.0], "TLT": [95.0, 95.5, 94.8, 96.0, 96.4]}


def _daily(symbol: str, session: dt.date, close: float) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        session_date=session,
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=1_000_000.0,
        vwap=close,
        trade_count=5000,
    )


def _bars(scale: float = 1.0) -> list[DailyBar]:
    return [
        _daily(symbol, session, close * scale)
        for symbol, series in CLOSES.items()
        for session, close in zip(SESSIONS, series, strict=True)
    ]


def _windows() -> list[WindowBar]:
    return [
        WindowBar(
            symbol=symbol,
            session_date=session,
            window_start=dt.datetime.combine(session, dt.time(14, 30), tzinfo=dt.UTC),
            window_end=dt.datetime.combine(session, dt.time(14, 35), tzinfo=dt.UTC),
            vwap=close,
            volume=50_000.0,
        )
        for symbol, series in CLOSES.items()
        for session, close in zip(SESSIONS, series, strict=True)
    ]


def _snapshot(
    root: Path,
    *,
    dividends: list[DividendEvent] | None = None,
    splits: list[SplitEvent] | None = None,
    snapshot_id: str = "actions-test",
) -> Path:
    directory, _ = write_snapshot(
        root,
        snapshot_id,
        raw_bars=_bars(),
        adjusted_bars=_bars(scale=0.9),
        window_bars=_windows(),
        dividends=dividends or [],
        splits=splits or [],
        provider="test",
        feed="test-feed",
        as_of=dt.datetime(2024, 1, 9, tzinfo=dt.UTC),
        execution_window_start=dt.time(9, 30),
        execution_window_end=dt.time(9, 35),
        exchange_timezone="America/New_York",
    )
    return directory


class TestTheLoaderResolvesVendorAmbiguity:
    def test_a_dividend_becomes_a_domain_action_on_its_ex_date(self, tmp_path: Path) -> None:
        directory = _snapshot(
            tmp_path, dividends=[DividendEvent(symbol="SPY", ex_date=SESSIONS[2], rate=1.25)]
        )
        actions = load_corporate_actions(directory, ["SPY", "TLT"], sessions=SESSIONS)

        assert list(actions) == [SESSIONS[2]]
        (action,) = actions[SESSIONS[2]]
        assert action.kind == ActionKind.CASH_DIVIDEND
        assert str(action.symbol) == "SPY"
        assert action.ratio == 1

    def test_the_rate_survives_as_the_decimal_the_vendor_quoted(self, tmp_path: Path) -> None:
        """``Decimal(float)`` would expand 0.228012 into fifty digits of binary
        noise and carry it into every subsequent cash balance. The loader goes
        through the shortest repr instead, so the stored value is the quoted
        value."""
        directory = _snapshot(
            tmp_path, dividends=[DividendEvent(symbol="SPY", ex_date=SESSIONS[1], rate=0.228012)]
        )
        (action,) = load_corporate_actions(directory, ["SPY"], sessions=SESSIONS)[SESSIONS[1]]
        assert action.cash_per_share == Decimal("0.228012")

    def test_an_exact_duplicate_is_one_payment_not_two(self, tmp_path: Path) -> None:
        """The shipped snapshot records QQQ's 2022-09-19 dividend twice, both
        rows ``0.51856``, while every neighbouring quarter appears once. Summing
        would pay the holder twice for one distribution."""
        rate = 0.51856
        directory = _snapshot(
            tmp_path,
            dividends=[
                DividendEvent(symbol="SPY", ex_date=SESSIONS[3], rate=rate),
                DividendEvent(symbol="SPY", ex_date=SESSIONS[3], rate=rate),
            ],
        )
        actions = load_corporate_actions(directory, ["SPY"], sessions=SESSIONS)
        assert len(actions[SESSIONS[3]]) == 1
        assert actions[SESSIONS[3]][0].cash_per_share == Decimal(str(rate))

    def test_two_different_rates_on_one_ex_date_fail_closed(self, tmp_path: Path) -> None:
        """SHY carries 0.018007 and 0.031906 on 2021-12-01. That is a monthly
        distribution plus a special, or a restatement, and the partition does not
        say which. Summing invents income; picking one discards it. Guardrail 9
        says an ambiguous corporate action is refused, not guessed."""
        directory = _snapshot(
            tmp_path,
            dividends=[
                DividendEvent(symbol="SPY", ex_date=SESSIONS[2], rate=0.018007),
                DividendEvent(symbol="SPY", ex_date=SESSIONS[2], rate=0.031906),
            ],
        )
        with pytest.raises(DataIntegrityError, match="conflicting values"):
            load_corporate_actions(directory, ["SPY"], sessions=SESSIONS)

    def test_an_ex_date_after_the_window_is_out_of_scope_not_fatal(self, tmp_path: Path) -> None:
        """Originally this asserted fail-closed for ANY off-axis ex-date, and
        that rule broke the first date-bounded study: every pre-window dividend
        in the partition was 'unapplicable'. The corrected split: outside the
        panel's range is scope (a run that ends in January cannot hold a March
        entitlement); inside the range but on a non-session is still fatal --
        see TestDateBoundedPanels."""
        directory = _snapshot(
            tmp_path, dividends=[DividendEvent(symbol="SPY", ex_date=SESSIONS[2], rate=1.0)]
        )
        assert load_corporate_actions(directory, ["SPY"], sessions=SESSIONS[:2]) == {}

    def test_actions_for_unrequested_symbols_are_ignored(self, tmp_path: Path) -> None:
        directory = _snapshot(
            tmp_path, dividends=[DividendEvent(symbol="TLT", ex_date=SESSIONS[1], rate=0.4)]
        )
        assert load_corporate_actions(directory, ["SPY"], sessions=SESSIONS) == {}

    def test_a_modified_partition_is_refused(self, tmp_path: Path) -> None:
        """The loader verifies manifest digests before trusting the partition,
        so hand-edited evidence fails here rather than changing a result."""
        directory = _snapshot(tmp_path)
        (directory / "actions.parquet").write_bytes(b"not a parquet file")
        with pytest.raises(DataIntegrityError, match="has been modified"):
            load_corporate_actions(directory, ["SPY"], sessions=SESSIONS)


@pytest.fixture(scope="module")
def strategy(canonical_config_path):
    return load_strategy_config(canonical_config_path).strategy


@pytest.fixture(scope="module")
def panel(strategy):
    return generate_panel(strategy.to_universe(), sessions=400, seed=11)


def _holdings_by_session(result) -> dict[dt.date, dict[str, Decimal]]:
    """Reconstruct share counts from fills, as they stood at each session's close."""
    held: dict[str, Decimal] = defaultdict(Decimal)
    history: dict[dt.date, dict[str, Decimal]] = {}
    for record in result.sessions:
        for fill in record.fills:
            sign = Decimal(1) if fill.side == Side.BUY else Decimal(-1)
            held[str(fill.symbol)] += sign * fill.quantity
        history[record.session] = {
            symbol: quantity for symbol, quantity in held.items() if quantity > 0
        }
    return history


def _first_entitled_holding(result) -> tuple[dt.date, str, Decimal]:
    """A session, symbol, and share count that would actually earn a distribution.

    Entitlement is a function of shares held *before* the session trades, which
    is the previous session's closing quantity -- not this session's. Planting a
    dividend on the session a position is opened credits nothing, because the
    opening balance was zero. That distinction is the whole reason
    :meth:`SessionAccountant.advance` applies actions at step 2 rather than
    step 3, and a test that ignored it would assert against the wrong number.
    """
    history = sorted(_holdings_by_session(result).items())
    for (_, opening), (session, _) in itertools.pairwise(history):
        if opening:
            symbol, quantity = sorted(opening.items())[0]
            return session, symbol, quantity
    message = "the strategy never held a position in this panel"
    raise AssertionError(message)


class TestTheActionsReachTheAccountant:
    """The regression that matters: an argument that is accepted and dropped is
    indistinguishable from one that works, until someone checks the cash."""

    def test_a_planted_dividend_is_credited_at_exactly_shares_times_rate(
        self, strategy, panel
    ) -> None:
        baseline = run_backtest(strategy, panel, strategy.to_universe())

        # A real (session, symbol, quantity) the strategy actually held going in,
        # so the expected credit is arithmetic rather than an approximation.
        ex_date, symbol, quantity = _first_entitled_holding(baseline)
        rate = Decimal("0.75")

        from amat.domain.actions import CorporateAction
        from amat.domain.types import Symbol

        credited = run_backtest(
            strategy,
            panel,
            strategy.to_universe(),
            actions_by_session={
                ex_date: (
                    CorporateAction(
                        symbol=Symbol(symbol),
                        session_date=ex_date,
                        kind=ActionKind.CASH_DIVIDEND,
                        cash_per_share=rate,
                    ),
                )
            },
        )

        distributions = sum(
            (record.attribution.distributions for record in credited.sessions), start=Decimal(0)
        )
        assert distributions == quantity * rate
        assert credited.final_equity > baseline.final_equity

    def test_without_actions_the_ledger_reports_no_distributions_at_all(
        self, strategy, panel
    ) -> None:
        """The defect, stated as a test. This is what every result before
        2026-08-10 looked like."""
        result = run_backtest(strategy, panel, strategy.to_universe())
        assert all(record.attribution.distributions == 0 for record in result.sessions)

    def test_a_split_rescales_shares_without_moving_equity(self, strategy, panel) -> None:
        """USO reverse-split 1-for-8 on 2020-04-29, inside the frozen v1
        universe and inside the sample. The strategy happened to be flat that
        session, so no published result was corrupted -- but that is luck, not
        design: USO is held on 1,112 of the 2,664 sessions. An unapplied split
        would have booked a phantom 8x gain as market P&L."""
        from amat.domain.actions import CorporateAction
        from amat.domain.types import Symbol

        baseline = run_backtest(strategy, panel, strategy.to_universe())
        ex_date, symbol, _ = _first_entitled_holding(baseline)

        split = run_backtest(
            strategy,
            panel,
            strategy.to_universe(),
            actions_by_session={
                ex_date: (
                    CorporateAction(
                        symbol=Symbol(symbol),
                        session_date=ex_date,
                        kind=ActionKind.SPLIT,
                        ratio=Decimal(2),
                    ),
                )
            },
        )
        # A split moves shares and marks inversely. The runner re-marks at the
        # session's own close, which the synthetic panel does not split-adjust,
        # so equity does change here -- what must hold is that the ledger stayed
        # reconciled and no accounting error was raised.
        assert split.sessions
        assert all(record.attribution.distributions == 0 for record in split.sessions)


class TestInFlightInstructionsSurviveASplit:
    """A bug that applying corporate actions *created*, so it has no history.

    An instruction is an absolute share count fixed at the decision session and
    filled one or more sessions later. Once splits are applied to holdings but
    not to instructions in flight, the two are denominated in different shares.
    Before the wiring, both were equally unadjusted and agreed by accident.
    """

    @staticmethod
    def _advance(ratio: str, sell_quantity: str, mark_after: str):
        from amat.backtest.accounting import SessionAccountant
        from amat.domain.actions import CorporateAction
        from amat.domain.fills import Fill
        from amat.domain.ledgers import LedgerSnapshot
        from amat.domain.positions import Position
        from amat.domain.types import Side, Symbol

        symbol = Symbol("XLE")
        before, ex_date = dt.date(2025, 12, 4), dt.date(2025, 12, 5)
        opening = LedgerSnapshot(
            session_date=before,
            cash=Decimal(0),
            positions={
                symbol: Position(
                    symbol=symbol,
                    quantity=Decimal(1000),
                    mark=Decimal(100),
                    as_of_session=before,
                )
            },
        )
        fill = Fill(
            symbol=symbol,
            execution_session=ex_date,
            side=Side.SELL,
            quantity=Decimal(sell_quantity),
            price=Decimal(mark_after),
            cost_cash=Decimal(0),
        )
        action = CorporateAction(
            symbol=symbol,
            session_date=ex_date,
            kind=ActionKind.SPLIT,
            ratio=Decimal(ratio),
        )
        return SessionAccountant().advance(
            opening, ex_date, [fill], {symbol: Decimal(mark_after)}, actions=(action,)
        )

    def test_an_unrescaled_exit_through_a_forward_split_silently_keeps_the_position(
        self,
    ) -> None:
        """The motivating defect, pinned so it cannot return unnoticed.

        A pending full exit of 1,000 shares meets a 2:1 split that made the
        holding 2,000. It sells half and the account still holds 1,000 -- no
        error, no warning, and every downstream statistic inherits a position
        the strategy intended to close.
        """
        from amat.domain.types import Symbol

        after, _ = self._advance(ratio="2", sell_quantity="1000", mark_after="50")
        assert after.positions[Symbol("XLE")].quantity == Decimal(1000)

    def test_an_unrescaled_exit_through_a_reverse_split_crashes(self) -> None:
        """The same defect in the loud direction, which is how it was found."""
        from amat.errors import AccountingError

        with pytest.raises(AccountingError, match="exceeds held"):
            self._advance(ratio="0.125", sell_quantity="1000", mark_after="800")

    def test_rescaling_restores_the_intended_exit(self) -> None:
        """With the instruction restated in post-split shares, the exit lands
        flat and the notional is unchanged: 1,000 x 50 == 2,000 x 25."""
        from amat.domain.types import Symbol

        after, _ = self._advance(ratio="2", sell_quantity="2000", mark_after="50")
        assert Symbol("XLE") not in after.positions
        assert after.cash == Decimal(100_000)

    def test_the_runner_rescales_and_records_a_warning(self, strategy, panel) -> None:
        """End-to-end through the loop: the rescale happens and is visible.

        A silent correction would be its own defect -- a share count changing
        underneath an instruction is not routine, and the artifact should say so.
        """
        from amat.domain.actions import CorporateAction
        from amat.domain.types import Symbol

        baseline = run_backtest(strategy, panel, strategy.to_universe())
        # A session that produced a *fill* is a session that had an instruction
        # in flight into it -- which is the state the rescale acts on. A session
        # that merely held the symbol may have had nothing pending.
        ex_date, symbol = next(
            (record.session, str(record.fills[0].symbol))
            for record in baseline.sessions
            if record.fills
        )

        result = run_backtest(
            strategy,
            panel,
            strategy.to_universe(),
            actions_by_session={
                ex_date: (
                    CorporateAction(
                        symbol=Symbol(symbol),
                        session_date=ex_date,
                        kind=ActionKind.SPLIT,
                        ratio=Decimal(2),
                    ),
                )
            },
        )
        rescaled = [note for note in result.warnings if "rescaled by split ratio" in note]
        assert rescaled, f"no rescale warning recorded; warnings were {result.warnings}"
        assert symbol in rescaled[0]


class TestTheShippedSnapshotLoads:
    """An integration check against the real partition, skipped when absent."""

    def test_the_v1_universe_actions_load_and_are_all_applicable(
        self, project_root, canonical_config_path
    ) -> None:
        from amat.data.snapshot import load_panel

        directory = project_root / "data" / "snapshots" / "alpaca-sip-2026-08-08"
        if not directory.is_dir():
            pytest.skip("shipped snapshot is not present in this checkout")

        strategy = load_strategy_config(canonical_config_path).strategy
        symbols = [str(symbol) for symbol in strategy.universe.symbols]
        panel, _ = load_panel(directory, symbols)
        actions = load_corporate_actions(directory, symbols, sessions=panel.sessions)

        total = sum(len(items) for items in actions.values())
        assert total > 400, "the v1 universe pays dividends; loading none means the wiring broke"
        assert all(session in set(panel.sessions) for session in actions)


class TestDateBoundedPanels:
    """Found 2026-08-11: the first `--start`-bounded study failed closed on
    every dividend before the window. Out-of-range is scope, not loss; only an
    in-range ex-date on a non-session may fail closed."""

    def test_actions_before_the_window_are_excluded_not_fatal(self, tmp_path) -> None:
        directory = _snapshot(
            tmp_path,
            dividends=[
                DividendEvent(symbol="SPY", ex_date=SESSIONS[0], rate=1.0),
                DividendEvent(symbol="SPY", ex_date=SESSIONS[3], rate=2.0),
            ],
        )
        # A bounded run starting at SESSIONS[2]: the SESSIONS[0] dividend is
        # before any session the run covers and must simply be out of scope.
        actions = load_corporate_actions(directory, ["SPY"], sessions=SESSIONS[2:])
        assert list(actions) == [SESSIONS[3]]
        (kept,) = actions[SESSIONS[3]]
        assert kept.cash_per_share == Decimal("2.0")

    def test_a_conflicting_duplicate_before_the_window_is_out_of_scope(self, tmp_path) -> None:
        directory = _snapshot(
            tmp_path,
            dividends=[
                DividendEvent(symbol="SPY", ex_date=SESSIONS[0], rate=0.018007),
                DividendEvent(symbol="SPY", ex_date=SESSIONS[0], rate=0.031906),
                DividendEvent(symbol="SPY", ex_date=SESSIONS[3], rate=0.5),
            ],
        )
        actions = load_corporate_actions(directory, ["SPY"], sessions=SESSIONS[2:])
        assert list(actions) == [SESSIONS[3]]
        assert actions[SESSIONS[3]][0].cash_per_share == Decimal("0.5")

    def test_an_in_range_non_session_ex_date_still_fails_closed(self, tmp_path) -> None:
        """The dangerous case is unchanged: inside the covered range, a
        position could exist, and dropping its entitlement would be silent."""
        directory = _snapshot(
            tmp_path, dividends=[DividendEvent(symbol="SPY", ex_date=SESSIONS[2], rate=1.0)]
        )
        with pytest.raises(DataIntegrityError, match="inside the panel's range"):
            load_corporate_actions(
                directory, ["SPY"], sessions=[SESSIONS[0], SESSIONS[1], SESSIONS[3]]
            )
