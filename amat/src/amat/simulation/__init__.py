"""Simulated live environment: the managed strategy driven by current data.

What this is
------------
A paper account. It reads current market data, forms the same decisions the
backtest forms, fills them against the same simulated execution model, and keeps
a persisted journal of the result. It is the backtest with its clock attached to
the present instead of to a fixed panel.

What this is not
----------------
It has no connection to any account, and no capability to acquire one. The only
network component it uses is :mod:`amat.data.alpaca`, which is restricted to
historical bars and corporate actions. Fills here are produced by
:class:`amat.execution.SimulatedExecutionModel` against observed window prices --
the same object the backtest uses. Nothing leaves this process except a JSON
journal on local disk.

Replay rather than incremental state
------------------------------------
Each step recomputes the account from inception over the full panel rather than
mutating yesterday's saved state. That is deliberate. Incremental live state is
where paper systems silently diverge from their backtests: one missed session,
one restart mid-window, one schema change, and the two disagree forever with no
way to tell when it started. Replay makes divergence impossible by construction --
the live account *is* the backtest, evaluated through today -- and it costs a
few seconds of computation on a daily-frequency strategy.

The consequence worth stating plainly: this measures what the strategy would
have done, not what it did. It models no queue position, no partial fills beyond
the cash constraint, and no market impact from its own size.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult, run_backtest
from amat.config.models import StrategyConfig
from amat.data.alpaca import AlpacaMarketData
from amat.data.live import extend_market_panel, latest_completed_session
from amat.data.snapshot import SnapshotManifest, load_corporate_actions, load_panel
from amat.domain.types import Cash, Symbol
from amat.errors import DataIntegrityError
from amat.metrics import PerformanceSummary
from amat.metrics import summarize as summarize_performance
from amat.portfolio import decide

__all__ = [
    "LiveSimulationReport",
    "SimulatedLiveAccount",
    "extend_market_panel",
    "extend_panel_to_present",
    "latest_completed_session",
]


def extend_panel_to_present(
    snapshot_directory: Path,
    client: AlpacaMarketData,
    symbols: Sequence[str],
    *,
    strategy: StrategyConfig,
    through: dt.date | None = None,
) -> tuple[MarketPanel, SnapshotManifest, int]:
    """Load the snapshot panel and append sessions observed since it was taken.

    Returns the extended panel, the snapshot manifest, and the number of
    appended sessions.

    The appended tail is fetched live and is *not* written into the immutable
    snapshot. A snapshot is evidence; a live tail is a moving target that will be
    revised by the vendor as corporate actions settle. Mixing them would make the
    snapshot's digest meaningless.

    Only sessions on which every requested symbol has both an adjusted and a raw
    close are appended, matching the complete-row rule the snapshot loader
    applies. A session where one symbol is missing is skipped rather than
    partially filled.
    """
    panel, manifest = load_panel(snapshot_directory, symbols)
    extended, appended = extend_market_panel(
        panel,
        client,
        symbols,
        strategy=strategy,
        through=through,
    )
    return extended, manifest, appended


@dataclass(frozen=True, slots=True)
class LiveSimulationReport:
    """One observation of the paper account."""

    generated_at: dt.datetime
    as_of_session: dt.date
    snapshot_id: str
    appended_sessions: int
    strategy_name: str
    strategy_version: str
    config_sha256: str
    initial_equity: Cash
    equity: Cash
    cash: Cash
    gross_exposure: float
    positions: dict[str, float]
    """Symbol to portfolio weight at the close of ``as_of_session``."""

    next_session_targets: dict[str, float]
    """Weights the strategy would seek in the next execution window.

    Formed by evaluating the allocator at the panel's final session. The v1
    decision is a pure function of the price panel, so this needs no replay
    state -- unlike the rejected v2 overlay, whose terminal decision could only
    be read out of a full run from inception.
    """

    performance: PerformanceSummary
    caveats: tuple[str, ...]

    live_fills: tuple[dict[str, str], ...] = ()
    """Every simulated trade executed in sessions the snapshot does not cover.

    The paper period's own trades, one row per fill -- symbol, side, exact
    quantity/price/cost as strings, and the recorded trade reason -- so the
    journal is a reviewable trade log ("what did the paper account do, and
    why") and not only a sequence of position snapshots. Empty when the
    account has not stepped past the snapshot boundary.
    """

    def as_dict(self) -> dict[str, object]:
        """JSON-ready payload for the journal."""
        return {
            "live_fills": [dict(row) for row in self.live_fills],
            "generated_at": self.generated_at.isoformat(),
            "as_of_session": self.as_of_session.isoformat(),
            "snapshot_id": self.snapshot_id,
            "appended_sessions": self.appended_sessions,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "config_sha256": self.config_sha256,
            "initial_equity": str(self.initial_equity),
            "equity": str(self.equity),
            "cash": str(self.cash),
            "gross_exposure": self.gross_exposure,
            "positions": self.positions,
            "next_session_targets": self.next_session_targets,
            "total_return": self.performance.total_return,
            "annualized_return": self.performance.annualized_return,
            "annualized_volatility": self.performance.annualized_volatility,
            "sharpe": self.performance.sharpe,
            "max_drawdown": self.performance.max_drawdown,
            "caveats": list(self.caveats),
        }


class SimulatedLiveAccount:
    """A paper account replayed from inception through the latest session."""

    def __init__(
        self,
        strategy: StrategyConfig,
        *,
        snapshot_directory: Path,
        config_sha256: str,
        client: AlpacaMarketData | None = None,
        initial_equity: Cash = Decimal("100000"),
        journal_directory: Path | None = None,
    ) -> None:
        self._strategy = strategy
        self._snapshot_directory = Path(snapshot_directory)
        self._config_sha256 = config_sha256
        self._client = client
        self._initial_equity = initial_equity
        self._journal_directory = journal_directory

    def step(self, *, through: dt.date | None = None) -> LiveSimulationReport:
        """Refresh data, replay the account, and journal the observation.

        Raises:
            DataIntegrityError: if the run produced no sessions.
        """
        symbols = [str(symbol) for symbol in self._strategy.universe.symbols]
        if self._client is not None:
            panel, manifest, appended = extend_panel_to_present(
                self._snapshot_directory,
                self._client,
                symbols,
                strategy=self._strategy,
                through=through,
            )
        else:
            panel, manifest = load_panel(self._snapshot_directory, symbols)
            appended = 0

        # Actions come from the immutable snapshot, so they cover the snapshot's
        # sessions and no further. When the panel has been extended live, the
        # appended tail is uncovered -- a dividend with an ex-date in those
        # sessions is simply not in the partition yet. That gap is recorded as a
        # caveat rather than papered over; see `_build_report`.
        actions = load_corporate_actions(
            self._snapshot_directory,
            symbols,
            sessions=[session for session in panel.sessions if session <= manifest.last_session],
        )

        result = run_backtest(
            self._strategy,
            panel,
            self._strategy.to_universe(),
            initial_cash=self._initial_equity,
            actions_by_session=actions,
        )
        if not result.sessions:
            message = "the simulated account produced no sessions"
            raise DataIntegrityError(message)

        report = self._build_report(result, panel, manifest, appended)
        self._journal(report)
        return report

    def _build_report(
        self,
        result: BacktestResult,
        panel: MarketPanel,
        manifest: SnapshotManifest,
        appended: int,
    ) -> LiveSimulationReport:
        last = result.sessions[-1]
        marks = panel.marks_at(len(panel) - 1)
        equity = last.equity

        # Weights are reconstructed from the marked value of each holding rather
        # than carried alongside the ledger, so a position with no valid mark is
        # visibly absent instead of reported at a stale weight.
        positions: dict[str, float] = {}
        for fill_symbol, price in marks.items():
            held = _position_quantity(result, fill_symbol)
            if held and equity > 0:
                positions[str(fill_symbol)] = float(held * price / equity)

        caveats = [
            "SIMULATED PAPER ACCOUNT -- no account is connected and no position is real.",
            "Fills are modelled at the observed execution-window VWAP plus a fixed "
            "cost assumption; queue position and market impact are not modelled.",
        ]
        if appended:
            caveats.append(
                f"{appended} session(s) were appended live and are not covered by the "
                f"immutable snapshot digest for {manifest.snapshot_id}."
            )
            caveats.append(
                f"Corporate actions are covered only through {manifest.last_session.isoformat()}. "
                f"A dividend or split with an ex-date in the {appended} appended session(s) is "
                "not credited, so the tail of this account understates total return."
            )
        if result.warnings:
            caveats.append(f"{len(result.warnings)} session warning(s) were recorded.")

        live_fills = tuple(
            {
                "execution_session": fill.execution_session.isoformat(),
                "symbol": str(fill.symbol),
                "side": fill.side.value,
                "quantity": str(fill.quantity),
                "price": str(fill.price),
                "cost_cash": str(fill.cost_cash),
                "reason": fill.reason.value if fill.reason is not None else "unrecorded",
            }
            for record in result.sessions
            if record.session > manifest.last_session
            for fill in record.fills
        )

        return LiveSimulationReport(
            generated_at=dt.datetime.now(tz=dt.UTC),
            as_of_session=last.session,
            snapshot_id=manifest.snapshot_id,
            appended_sessions=appended,
            strategy_name=self._strategy.name,
            strategy_version=self._strategy.version,
            config_sha256=self._config_sha256,
            initial_equity=result.initial_equity,
            equity=equity,
            cash=last.cash,
            gross_exposure=float(last.gross_exposure),
            positions=positions,
            # The decision formed from the last completed close, which is what
            # would execute in the next window. This was previously aliased to
            # `positions`, so the field documented as "what the strategy would
            # seek" reported "what is already held" -- and any reader diffing
            # the two to find trades would have found none, forever.
            next_session_targets=self._terminal_targets(panel),
            performance=summarize_performance(result),
            caveats=tuple(caveats),
            live_fills=live_fills,
        )

    def _terminal_targets(self, panel: MarketPanel) -> dict[str, float]:
        """Weights the allocator seeks for the session after the panel's last.

        The backtest loop cannot produce this: it schedules each decision into a
        later session *inside* the panel, and the final session has none. For v1
        that is not a problem, because :func:`amat.portfolio.decide` is a pure
        function of the price panel and can simply be evaluated at the last
        index. Only positive weights are reported; a zero target is the absence
        of a position, not a position of zero.
        """
        allocation = decide(
            self._strategy,
            panel.adjusted_closes,
            self._strategy.to_universe(),
            decision_index=len(panel) - 1,
        )
        mapping = allocation.as_mapping(self._strategy.to_universe())
        return {str(symbol): float(weight) for symbol, weight in mapping.items() if weight > 0}

    def _journal(self, report: LiveSimulationReport) -> None:
        """Append the observation to a dated JSON-lines journal."""
        if self._journal_directory is None:
            return
        self._journal_directory.mkdir(parents=True, exist_ok=True)
        path = self._journal_directory / f"{report.as_of_session.isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report.as_dict(), sort_keys=True) + "\n")


def _position_quantity(result: BacktestResult, symbol: Symbol) -> Decimal:
    """Net quantity held in ``symbol`` after replaying every recorded fill.

    Derived from the fill history rather than read from a carried position map,
    so the reported holding is reconcilable to the trades that produced it --
    guardrail 6's requirement that every position change trace to a fill.
    """
    return sum(
        (
            fill.signed_quantity
            for record in result.sessions
            for fill in record.fills
            if fill.symbol == symbol
        ),
        start=Decimal(0),
    )
