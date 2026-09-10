"""Episode diagnostics: where the run behaved as designed, and where it paid.

Purpose
-------
The headline table says *how much* a run made; it cannot say *which behaviours*
produced or cost that money. This module classifies the run's own artifacts into
episodes a reader can inspect one by one:

* **captured trends** -- round trips that did what a trend follower exists to do;
* **whipsaws** -- quick, losing round trips: the fee the strategy pays for
  entering on strength that immediately reverses. A trend system cannot avoid
  these; their *size and count* are the honest measure of the cost;
* **give-backs** -- trades that held a large open profit and surrendered most of
  it before the exit signal fired. These bound what a different exit rule could
  at best have changed;
* **missed rallies** -- windows where an instrument rallied hard while the
  portfolio held (almost) none of it, split by *why*: a binding cap (an
  allocator decision) versus a signal that never crossed the deadband (a signal
  decision).

Every episode is computed from the run's recorded fills, marks, and constraint
records. Nothing here re-runs the strategy, and nothing here is a
recommendation: an episode names a behaviour and its measured cost in *this
sample*. Whether changing it would have helped out of sample is exactly the
question a backtest cannot answer.

Holdings reconstruction
-----------------------
Per-symbol weights are replayed from fills plus split ratios, then reconciled
against the ledger's own ``equity - cash`` each session. The replay refuses to
describe holdings it cannot reconcile: a chart annotated with wrong positions is
worse than no chart.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from amat.backtest.attribution import ClosedTrade, TradeLedger
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import RunResult
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.types import SessionDate, Symbol
from amat.errors import DataIntegrityError

__all__ = [
    "MissedRally",
    "StrategyInsights",
    "TradeEpisode",
    "build_insights",
    "reconstruct_weights",
]

#: A round trip returning at least this much on entry notional "captured a trend".
CAPTURED_TREND_RETURN = 0.08
#: A losing round trip at most this many sessions long is a whipsaw.
WHIPSAW_MAX_SESSIONS = 21
#: A give-back surrendered at least this fraction of its peak open profit ...
GIVEBACK_SURRENDER_FRACTION = 0.5
#: ... and the peak itself was at least this fraction of entry notional.
GIVEBACK_MIN_PEAK_RETURN = 0.05
#: Missed-rally scan window, matching the strategy's medium horizon.
MISSED_RALLY_WINDOW = 63
#: An instrument gaining at least this much over the window counts as a rally.
MISSED_RALLY_MIN_RETURN = 0.20
#: Holding on average below this weight during the rally counts as "missed".
MISSED_RALLY_MAX_WEIGHT = 0.02
#: Replayed position value must match ``equity - cash`` within this fraction
#: of equity. The replay uses the ledger's own fills and split ratios, so a
#: larger gap means the reconstruction is wrong, not that the run is.
_RECONCILE_TOLERANCE = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class TradeEpisode:
    """One classified round trip."""

    kind: str
    symbol: str
    entry_session: str
    exit_session: str
    sessions_held: int
    net_pnl: float
    return_pct: float
    peak_open_pnl: float
    note: str

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "entry_session": self.entry_session,
            "exit_session": self.exit_session,
            "sessions_held": self.sessions_held,
            "net_pnl": self.net_pnl,
            "return_pct": self.return_pct,
            "peak_open_pnl": self.peak_open_pnl,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class MissedRally:
    """A strong move the portfolio was (almost) absent from."""

    symbol: str
    start_session: str
    end_session: str
    asset_return: float
    mean_weight: float
    cap_bound_sessions: int
    note: str

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "start_session": self.start_session,
            "end_session": self.end_session,
            "asset_return": self.asset_return,
            "mean_weight": self.mean_weight,
            "cap_bound_sessions": self.cap_bound_sessions,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class StrategyInsights:
    """Every classified episode plus the reconstruction it rests on."""

    captured_trends: tuple[TradeEpisode, ...]
    whipsaws: tuple[TradeEpisode, ...]
    givebacks: tuple[TradeEpisode, ...]
    missed_rallies: tuple[MissedRally, ...]
    trades: tuple[ClosedTrade, ...]
    whipsaw_total_cost: float
    giveback_total_surrendered: float
    reconciliation_max_error: float
    #: ``(sessions, symbols)`` end-of-session weights of equity, aligned to the
    #: run's session rows and the panel's symbol columns. Reconciled against the
    #: ledger before being stored; deliberately absent from :meth:`as_dict`.
    weights: np.ndarray

    def as_dict(self) -> dict[str, object]:
        return {
            "captured_trends": [episode.as_dict() for episode in self.captured_trends],
            "whipsaws": [episode.as_dict() for episode in self.whipsaws],
            "givebacks": [episode.as_dict() for episode in self.givebacks],
            "missed_rallies": [rally.as_dict() for rally in self.missed_rallies],
            "whipsaw_total_cost": self.whipsaw_total_cost,
            "giveback_total_surrendered": self.giveback_total_surrendered,
            "reconciliation_max_error": self.reconciliation_max_error,
        }


def _panel_offset(result: RunResult, panel: MarketPanel) -> int:
    """Index into ``panel.sessions`` of the run's first recorded session."""
    first = result.sessions[0].session
    for index, session in enumerate(panel.sessions):
        if session == first:
            return index
    message = f"run's first session {first.isoformat()} is not in the panel"
    raise DataIntegrityError(message)


def reconstruct_weights(
    result: RunResult,
    panel: MarketPanel,
    *,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> tuple[np.ndarray, float]:
    """Replay per-symbol portfolio weights from the run's fills.

    Returns:
        ``(weights, max_error)``: a ``(sessions, symbols)`` array of end-of-
        session weights of equity, aligned to ``result.sessions`` rows and
        ``panel.symbols`` columns, and the largest relative gap between the
        replayed position value and the ledger's own ``equity - cash``.

    Raises:
        DataIntegrityError: if the replay cannot reconcile against the ledger.
            The replay applies the same fills and the same split ratios the run
            recorded, so disagreement means these weights would be fiction.
    """
    actions_by_session = actions_by_session or {}
    offset = _panel_offset(result, panel)
    symbols = list(panel.symbols)
    column = {symbol: position for position, symbol in enumerate(symbols)}

    holdings: dict[Symbol, Decimal] = {}
    weights = np.zeros((len(result.sessions), len(symbols)), dtype=float)
    max_error = Decimal(0)

    for row, record in enumerate(result.sessions):
        for action in actions_by_session.get(record.session, ()):
            if action.kind == ActionKind.SPLIT and action.symbol in holdings:
                holdings[action.symbol] *= action.ratio
        for fill in record.fills:
            holdings[fill.symbol] = holdings.get(fill.symbol, Decimal(0)) + fill.signed_quantity
        holdings = {symbol: qty for symbol, qty in holdings.items() if qty != 0}

        marks = panel.marks_at(offset + row)
        position_value = Decimal(0)
        for symbol, quantity in holdings.items():
            mark = marks.get(symbol)
            if mark is None:
                message = (
                    f"{record.session.isoformat()}: {symbol} held but has no mark; "
                    "weights cannot be reconstructed"
                )
                raise DataIntegrityError(message)
            value = quantity * mark
            position_value += value
            if record.equity > 0:
                weights[row, column[symbol]] = float(value / record.equity)

        expected = record.equity - record.cash
        if record.equity > 0:
            error = abs(position_value - expected) / record.equity
            max_error = max(max_error, error)
            if error > _RECONCILE_TOLERANCE:
                message = (
                    f"{record.session.isoformat()}: replayed position value "
                    f"{position_value:.2f} disagrees with ledger equity-cash "
                    f"{expected:.2f} by {error:.6%} of equity; refusing to "
                    "report unreconciled holdings"
                )
                raise DataIntegrityError(message)

    return weights, float(max_error)


def replay_trades(
    result: RunResult,
    panel: MarketPanel,
    *,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> tuple[ClosedTrade, ...]:
    """Round trips replayed with per-session marks, so excursions are real.

    :func:`amat.reporting.build_report` replays fills without marking, which is
    all attribution needs. Episode classification additionally needs each
    trade's peak open profit (MFE) and holding period, so this replay marks
    every open trade at every session close exactly as the run experienced it.
    """
    offset = _panel_offset(result, panel)
    ledger = TradeLedger()
    closed: list[ClosedTrade] = []
    actions_by_session = actions_by_session or {}
    for row, record in enumerate(result.sessions):
        for action in actions_by_session.get(record.session, ()):
            if action.kind == ActionKind.SPLIT:
                ledger.apply_split(action.symbol, action.ratio)
        closed.extend(ledger.apply_session(record.fills, panel.marks_at(offset + row)))
    return tuple(closed)


def _coalesce_round_trips(trades: Sequence[ClosedTrade]) -> list[ClosedTrade]:
    """Merge partial exits of one position into a single round trip.

    :class:`TradeLedger` emits one ``ClosedTrade`` per sell, and every partial
    exit carries the *position's* full excursion history. Classifying those
    rows independently counts one position's peak open profit once per partial
    sell -- measured on the full v1 sample this inflated "total surrendered"
    roughly eight-fold. Episodes are therefore classified on coalesced round
    trips keyed by ``(symbol, entry_session)``: P&L and costs sum, the peak is
    taken once, and the exit is the last partial's.
    """
    grouped: dict[tuple[Symbol, dt.date], list[ClosedTrade]] = {}
    for trade in trades:
        grouped.setdefault((trade.symbol, trade.entry_session), []).append(trade)

    coalesced: list[ClosedTrade] = []
    for parts in grouped.values():
        if len(parts) == 1:
            coalesced.append(parts[0])
            continue
        quantity = sum((part.quantity for part in parts), Decimal(0))
        notional = sum((part.entry_price * part.quantity for part in parts), Decimal(0))
        last = max(parts, key=lambda part: (part.exit_session, part.sessions_held))
        coalesced.append(
            ClosedTrade(
                symbol=last.symbol,
                entry_session=last.entry_session,
                exit_session=last.exit_session,
                quantity=quantity,
                entry_price=notional / quantity if quantity else last.entry_price,
                exit_price=last.exit_price,
                gross_pnl=sum((part.gross_pnl for part in parts), Decimal(0)),
                costs=sum((part.costs for part in parts), Decimal(0)),
                net_pnl=sum((part.net_pnl for part in parts), Decimal(0)),
                initial_risk=last.initial_risk,
                max_favourable=max(part.max_favourable for part in parts),
                max_adverse=min(part.max_adverse for part in parts),
                sessions_held=max(part.sessions_held for part in parts),
            )
        )
    coalesced.sort(key=lambda trade: (trade.exit_session, trade.symbol))
    return coalesced


def _classify_trades(
    trades: Sequence[ClosedTrade],
) -> tuple[list[TradeEpisode], list[TradeEpisode], list[TradeEpisode]]:
    captured: list[TradeEpisode] = []
    whipsaws: list[TradeEpisode] = []
    givebacks: list[TradeEpisode] = []

    for trade in _coalesce_round_trips(trades):
        entry_notional = float(trade.entry_price * trade.quantity)
        if entry_notional <= 0:
            continue
        peak = float(trade.max_favourable)
        net = float(trade.net_pnl)
        base = TradeEpisode(
            kind="",
            symbol=str(trade.symbol),
            entry_session=trade.entry_session.isoformat(),
            exit_session=trade.exit_session.isoformat(),
            sessions_held=trade.sessions_held,
            net_pnl=net,
            return_pct=trade.return_pct,
            peak_open_pnl=peak,
            note="",
        )

        if trade.return_pct >= CAPTURED_TREND_RETURN:
            captured.append(
                _with(
                    base,
                    kind="captured_trend",
                    note=(
                        f"entered on trend, held {trade.sessions_held} sessions, "
                        f"exited {trade.return_pct:+.1%} net -- the behaviour the "
                        "strategy is designed for"
                    ),
                )
            )
        if net < 0 and trade.sessions_held <= WHIPSAW_MAX_SESSIONS:
            whipsaws.append(
                _with(
                    base,
                    kind="whipsaw",
                    note=(
                        f"entry reversed within {trade.sessions_held} sessions and cost "
                        f"{net:,.0f}; the deadband already suppresses weaker signals, so "
                        "this is the residual cost of acting on trends at all"
                    ),
                )
            )
        surrendered = peak - net
        if (
            peak >= GIVEBACK_MIN_PEAK_RETURN * entry_notional
            and surrendered >= GIVEBACK_SURRENDER_FRACTION * peak
        ):
            givebacks.append(
                _with(
                    base,
                    kind="giveback",
                    note=(
                        f"open profit peaked at {peak:,.0f} and closed at {net:,.0f}; "
                        f"{surrendered:,.0f} was surrendered waiting for the exit "
                        "signal. This bounds what a different exit rule could at best "
                        "have kept in this sample; it is not evidence one would have"
                    ),
                )
            )
    return captured, whipsaws, givebacks


def _with(episode: TradeEpisode, *, kind: str, note: str) -> TradeEpisode:
    return TradeEpisode(
        kind=kind,
        symbol=episode.symbol,
        entry_session=episode.entry_session,
        exit_session=episode.exit_session,
        sessions_held=episode.sessions_held,
        net_pnl=episode.net_pnl,
        return_pct=episode.return_pct,
        peak_open_pnl=episode.peak_open_pnl,
        note=note,
    )


def _cluster_of(symbol: str, clusters: Mapping[str, Sequence[str]]) -> str | None:
    for name, members in clusters.items():
        if symbol in members:
            return name
    return None


def _missed_rallies(
    result: RunResult,
    panel: MarketPanel,
    weights: np.ndarray,
    *,
    clusters: Mapping[str, Sequence[str]] | None,
) -> list[MissedRally]:
    """Non-overlapping windows where an instrument rallied unheld."""
    offset = _panel_offset(result, panel)
    rows = len(result.sessions)
    if rows <= MISSED_RALLY_WINDOW:
        return []

    adjusted = panel.adjusted_closes[offset : offset + rows]
    sessions = [record.session for record in result.sessions]
    binding = [record.binding_constraints for record in result.sessions]

    rallies: list[MissedRally] = []
    for col, symbol in enumerate(panel.symbols):
        series = adjusted[:, col]
        start = MISSED_RALLY_WINDOW
        while start < rows:
            begin = start - MISSED_RALLY_WINDOW
            window_return = float(series[start] / series[begin] - 1.0)
            mean_weight = float(weights[begin:start, col].mean())
            if window_return >= MISSED_RALLY_MIN_RETURN and mean_weight < MISSED_RALLY_MAX_WEIGHT:
                cluster = _cluster_of(str(symbol), clusters) if clusters else None
                cap_names = {"symbol_cap", "gross_cap"}
                if cluster is not None:
                    cap_names.add(f"cluster_cap:{cluster}")
                cap_sessions = sum(
                    1 for names in binding[begin:start] if any(name in cap_names for name in names)
                )
                if cap_sessions > MISSED_RALLY_WINDOW // 2:
                    note = (
                        "a cap bound the allocator for most of this window, so the "
                        "absence was a portfolio-construction decision, not a missed "
                        "signal"
                    )
                else:
                    note = (
                        "the trend signal never cleared the deadband here; a "
                        "shorter horizon or lower deadband would have acted sooner, "
                        "at the price of more whipsaws everywhere else"
                    )
                rallies.append(
                    MissedRally(
                        symbol=str(symbol),
                        start_session=sessions[begin].isoformat(),
                        end_session=sessions[start].isoformat(),
                        asset_return=window_return,
                        mean_weight=mean_weight,
                        cap_bound_sessions=cap_sessions,
                        note=note,
                    )
                )
                start += MISSED_RALLY_WINDOW  # skip past this window: no double counting
            else:
                start += 5
    rallies.sort(key=lambda rally: rally.asset_return, reverse=True)
    return rallies


def build_insights(
    result: RunResult,
    panel: MarketPanel,
    *,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
    clusters: Mapping[str, Sequence[str]] | None = None,
) -> StrategyInsights:
    """Classify a completed run's artifacts into inspectable episodes.

    Args:
        result: The finished run.
        panel: The exact panel the run consumed.
        actions_by_session: The corporate actions the run was given; needed to
            replay share counts across splits.
        clusters: Cluster membership (name -> symbols), used only to attribute
            a missed rally to a cluster cap.
    """
    weights, max_error = reconstruct_weights(result, panel, actions_by_session=actions_by_session)
    trades = replay_trades(result, panel, actions_by_session=actions_by_session)
    captured, whipsaws, givebacks = _classify_trades(trades)
    rallies = _missed_rallies(result, panel, weights, clusters=clusters)

    return StrategyInsights(
        captured_trends=tuple(captured),
        whipsaws=tuple(whipsaws),
        givebacks=tuple(givebacks),
        missed_rallies=tuple(rallies),
        trades=trades,
        whipsaw_total_cost=float(sum(episode.net_pnl for episode in whipsaws)),
        giveback_total_surrendered=float(
            sum(episode.peak_open_pnl - episode.net_pnl for episode in givebacks)
        ),
        reconciliation_max_error=max_error,
        weights=weights,
    )


def weight_matrix_dates(result: RunResult) -> list[dt.date]:
    """The session dates the weight matrix rows correspond to."""
    return [record.session for record in result.sessions]
