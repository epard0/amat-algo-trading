"""Capital-constrained minute replay with observable clocks and delayed activation."""

from __future__ import annotations

import datetime as dt
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Literal

import numpy as np

from amat.backtest.event_accounting import EventAccountant
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.fills import Fill
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.types import Side, Symbol, parse_symbol
from amat.execution import one_way_cost
from amat.execution.budget import CashBudgetPolicy, budget_buys
from amat.risk.active import ActiveRiskConfig, apply_active_risk, initial_state
from autoresearch.improvements.minute import FEATURE_WARMUP, FeatureStream, MinuteBatch
from autoresearch.improvements.refit import RefitCoordinator, TrainingRow, fit_model

MIN_LABELS = 100
LIQUIDATION_LEAD_SECONDS = 300
METALS_CAP = 0.30
REBALANCE_BAND = 0.02
MIN_TRADE_NOTIONAL = Decimal(25)

MinuteArm = Literal["M0", "M1", "M2", "M3"]


def default_shocks(symbols: tuple[str, ...]) -> tuple[dict[str, float], ...]:
    """Designed research stresses, not occurrence probabilities."""
    return (
        {s: -0.15 if s not in {"GLD", "SLV", "IEF", "UUP"} else -0.03 for s in symbols},
        {s: -0.30 if s == "SLV" else -0.12 if s == "GLD" else -0.02 for s in symbols},
        dict.fromkeys(symbols, -0.10),
    )


def replay_minutes(  # noqa: PLR0912, PLR0915 - chronological event and account loop
    batches: Iterable[MinuteBatch],
    symbols: tuple[str, ...],
    arm: MinuteArm,
    *,
    initial_cash: Decimal = Decimal(100000),
    cost_bps: float = 2,
    decision_minutes: int = 5,
    label_minutes: int = 15,
    training_sessions: int = 20,
    refit_minutes: int = 250,
    max_fits: int = 64,
    seed: int = 0,
    risk_config: ActiveRiskConfig | None = None,
    daily_targets: Mapping[dt.date, dict[str, float]] | None = None,
    actions: Mapping[dt.date, Sequence[CorporateAction]] | None = None,
    feature_provider: Callable[[dt.datetime], dict[str, np.ndarray | None]] | None = None,
    trade_start: dt.date | None = None,
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Flat-at-close research mandate; failed liquidations remain actual holdings.

    Recorded completed minute availability is bar end + latency. Orders can fill
    only at a later bar open. Participation is a declared aggregate-bar proxy.
    GP screening uses mature forward labels; performance comes from this ledger.
    Historical fitting uses a completion barrier plus the next decision boundary,
    ensuring deterministic replay rather than CPU-speed-dependent activation.
    """
    if arm not in {"M0", "M1", "M2", "M3"} or decision_minutes < 1 or label_minutes < 1:
        raise ValueError("invalid minute replay contract")
    if arm == "M0" and daily_targets is None:
        raise ValueError("M0 requires prior-completed-daily target inputs")
    risk_config = risk_config or ActiveRiskConfig()
    stream = FeatureStream(symbols)
    coordinator = RefitCoordinator(refit_minutes, max_fits=max_fits, cost_bps=cost_bps)
    initial_candidate = None
    accountant = EventAccountant()
    ledger: LedgerSnapshot | None = None
    state = None
    marks: dict[Symbol, Decimal] = {}
    pending: dict[str, float] | None = None
    valid_after: dt.datetime | None = None
    observations: deque[tuple[dt.datetime, str, tuple[float, ...], float]] = deque()
    training: deque[TrainingRow] = deque()
    sessions: list[dt.date] = []

    class EvidenceLog(list[dict[str, Any]]):
        def __init__(self, kind: str) -> None:
            self.kind = kind
            super().__init__()

        def append(self, row: dict[str, Any]) -> None:
            if event_sink is None:
                super().append(row)
            else:
                event_sink(self.kind, row)

        def __iadd__(self, rows: Iterable[dict[str, Any]]) -> EvidenceLog:  # type: ignore[override, misc]
            for row in rows:
                self.append(row)
            return self

    records = EvidenceLog("records")
    fill_log = EvidenceLog("fills")
    decisions = EvidenceLog("decisions")
    stale_minutes = 0
    failed_liquidations = []
    missed_log = EvidenceLog("missed")
    last_session = None
    cutoff_start = None
    try:
        for batch in batches:
            date = batch.start.date()
            bars = {b.symbol: b for b in batch.bars}
            if len(bars) != len(batch.bars) or not set(bars).issubset(symbols):
                raise ValueError("duplicate or unauthorized batch symbol")
            if ledger is None:
                ledger = LedgerSnapshot(date, initial_cash, {})
                state = initial_state(
                    "simulated-minute", f"{arm}-research-risk", batch.close_at, float(initial_cash)
                )
            opening_equity = ledger.equity
            attributions = []
            if date != last_session:
                if last_session is not None and ledger.positions:
                    failed_liquidations.append(str(last_session))
                pending = None
                valid_after = None
                sessions.append(date)
                last_session = date
                session_actions = list((actions or {}).get(date, ()))
                if session_actions:
                    adjusted_marks = dict(marks)
                    for action in session_actions:
                        if action.kind == ActionKind.SPLIT:
                            stream.split(str(action.symbol), float(action.ratio))
                            if action.symbol in adjusted_marks:
                                adjusted_marks[action.symbol] /= action.ratio
                    ledger, event_attribution = accountant.advance_at(
                        batch.start,
                        f"{batch.start}:actions",
                        ledger,
                        date,
                        [],
                        adjusted_marks,
                        actions=session_actions,
                    )
                    attributions.append(event_attribution)
                    marks = adjusted_marks
            # Execute only the previous authorized decision, using this bar's
            # open. Future volume is a capacity assumption, not signal input.
            fills = []
            batch_missed = 0
            if pending is not None and valid_after is not None and batch.start >= valid_after:
                for held_symbol, position in ledger.positions.items():
                    bar = bars.get(str(held_symbol))
                    if bar is None or bar.volume <= 0:
                        missed_log.append(
                            {
                                "at": str(batch.start),
                                "symbol": str(held_symbol),
                                "reason": "missing_or_zero_volume_exit_bar",
                            }
                        )
                        batch_missed += 1
                        continue
                    if (
                        abs(
                            pending.get(str(held_symbol), 0)
                            - float(position.market_value / ledger.equity)
                        )
                        < REBALANCE_BAND
                        and pending.get(str(held_symbol), 0) > 0
                    ):
                        continue
                    target = Decimal(str(pending.get(str(held_symbol), 0))) * ledger.equity
                    quantity = max(Decimal(0), position.quantity - target / Decimal(str(bar.open)))
                    if quantity > 0 and (
                        quantity * Decimal(str(bar.open)) >= MIN_TRADE_NOTIONAL
                        or pending.get(str(held_symbol), 0) == 0
                    ):
                        price = Decimal(str(bar.open))
                        fills.append(
                            Fill(
                                held_symbol,
                                date,
                                Side.SELL,
                                quantity,
                                price,
                                one_way_cost(quantity * price, cost_bps),
                            )
                        )
                if fills:
                    opening_marks = {
                        **marks,
                        **{parse_symbol(s): Decimal(str(b.open)) for s, b in bars.items()},
                    }
                    ledger, event_attribution = accountant.advance_at(
                        batch.start, f"{batch.start}:sells", ledger, date, fills, opening_marks
                    )
                    attributions.append(event_attribution)
                requests: dict[str, Decimal] = {}
                prices: dict[str, Decimal] = {}
                for symbol in symbols:
                    bar = bars.get(symbol)
                    if bar is None or bar.volume <= 0:
                        continue
                    held = ledger.positions.get(parse_symbol(symbol))
                    price = Decimal(str(bar.open))
                    desired = Decimal(str(pending[symbol])) * ledger.equity / price
                    quantity = max(Decimal(0), desired - (held.quantity if held else Decimal(0)))
                    current_weight = float(held.market_value / ledger.equity) if held else 0.0
                    if held and abs(pending[symbol] - current_weight) < REBALANCE_BAND:
                        continue
                    if quantity * price < MIN_TRADE_NOTIONAL:
                        continue
                    # Raw volume is used only as an ex-post fill capacity proxy.
                    requests[symbol] = min(quantity, Decimal(str(bar.volume)) * Decimal(".01"))
                    prices[symbol] = price
                    if requests[symbol] < quantity:
                        missed_log.append(
                            {
                                "at": str(batch.start),
                                "symbol": symbol,
                                "reason": "participation_cap",
                                "quantity": str(quantity - requests[symbol]),
                            }
                        )
                        batch_missed += 1
                quantities, budget_residual = budget_buys(
                    requests,
                    prices,
                    confirmed_cash=ledger.cash,
                    equity=ledger.equity,
                    one_way_bps=cost_bps,
                    policy=CashBudgetPolicy(),
                )
                for symbol, residual_quantity in budget_residual.items():
                    if residual_quantity > 0:
                        missed_log.append(
                            {
                                "at": str(batch.start),
                                "symbol": symbol,
                                "reason": "cash_budget",
                                "quantity": str(residual_quantity),
                            }
                        )
                        batch_missed += 1
                buys = [
                    Fill(
                        parse_symbol(s),
                        date,
                        Side.BUY,
                        q,
                        prices[s],
                        one_way_cost(q * prices[s], cost_bps),
                    )
                    for s, q in quantities.items()
                    if q > 0
                ]
                if buys:
                    opening_marks = {
                        **marks,
                        **{parse_symbol(s): Decimal(str(b.open)) for s, b in bars.items()},
                    }
                    ledger, event_attribution = accountant.advance_at(
                        batch.start, f"{batch.start}:buys", ledger, date, buys, opening_marks
                    )
                    attributions.append(event_attribution)
                fills += buys
                pending = None
                valid_after = None
            marks.update({parse_symbol(s): Decimal(str(b.close)) for s, b in bars.items()})
            # Explicit stale valuations retain last known marks; no synthetic
            # execution bar is generated and increases will be blocked below.
            ledger, attribution = accountant.advance_at(
                batch.start + dt.timedelta(minutes=1),
                f"{batch.start}:close",
                ledger,
                date,
                [],
                marks,
            )
            attributions.append(attribution)
            residual = (
                ledger.equity
                - opening_equity
                - sum((a.explained_change for a in attributions), Decimal(0))
            )
            if abs(residual) > Decimal(".000001"):
                raise ValueError("minute batch accounting residual")
            if state is None:
                raise ValueError("missing risk state")
            state = state.observe(batch.close_at, float(ledger.equity))
            admitted = [stream.admit(bar, batch.close_at) for bar in batch.bars]
            complete = len(bars) == len(symbols) and all(admitted)
            if not complete:
                stale_minutes += 1
            if feature_provider is None:
                values = {s: stream.values(s, batch.start) for s in symbols}
            else:
                cached = feature_provider(batch.start)
                values = {
                    s: cached[s]
                    if s in bars
                    and s not in stream.invalidated
                    and len(stream.closes[s]) >= FEATURE_WARMUP
                    else None
                    for s in symbols
                }
            ready = any(v is not None for v in values.values())
            # Labels mature only when an actual later close exists at exactly
            # the declared horizon; gaps discard labels instead of imputing.
            while (
                observations
                and observations[0][0] + dt.timedelta(minutes=label_minutes) <= batch.start
            ):
                stamp, label_symbol, features, label_price = observations.popleft()
                if (
                    stamp + dt.timedelta(minutes=label_minutes) == batch.start
                    and label_symbol in bars
                ):
                    training.append(
                        TrainingRow(
                            stamp + dt.timedelta(minutes=1, seconds=1),
                            batch.close_at,
                            features,
                            bars[label_symbol].close / label_price - 1,
                            label_symbol,
                        )
                    )
            if ready:
                for symbol, feature_values in values.items():
                    if feature_values is None or symbol not in bars:
                        continue
                    observations.append(
                        (batch.start, symbol, tuple(map(float, feature_values)), bars[symbol].close)
                    )
            if len(sessions) >= training_sessions:
                cutoff_start = sessions[-training_sessions]
                while training and training[0].observed.date() < cutoff_start:
                    training.popleft()
            trigger = coordinator.observe(batch.start) if any(admitted) else False
            can_fit = ready and len(sessions) >= training_sessions and len(training) >= MIN_LABELS
            if (
                arm in {"M2", "M3"}
                and can_fit
                and coordinator.active is None
                and coordinator.fit_count == 0
            ):
                try:
                    initial_candidate = fit_model(
                        tuple(training), batch.close_at, seed=seed, cost_bps=cost_bps
                    )
                    coordinator.fit_count += 1
                    coordinator.events.append(
                        {
                            "event": "initial_fit",
                            "at": str(batch.close_at),
                            "model_id": initial_candidate.identity,
                            "artifact": asdict(initial_candidate),
                        }
                    )
                except ValueError as exc:
                    coordinator.fit_count += 1
                    coordinator.events.append({"event": "initial_fit_failed", "reason": str(exc)})
            if arm == "M3" and trigger and can_fit and initial_candidate is None:
                coordinator.submit(
                    tuple(training), batch.close_at, seed=seed + coordinator.fit_count
                )
            closing = (
                batch.session_close - batch.close_at
            ).total_seconds() <= LIQUIDATION_LEAD_SECONDS
            boundary = batch.start.minute % decision_minutes == 0
            if closing or boundary:
                if (
                    initial_candidate is not None
                    and batch.close_at > initial_candidate.trained_through
                ):
                    if initial_candidate.validation_score > 0:
                        coordinator.active = initial_candidate
                        coordinator.events.append(
                            {
                                "event": "initial_activated",
                                "at": str(batch.close_at),
                                "model_id": initial_candidate.identity,
                            }
                        )
                    else:
                        coordinator.events.append(
                            {"event": "initial_rejected", "at": str(batch.close_at)}
                        )
                    initial_candidate = None
                if (
                    coordinator.pending is not None
                    and coordinator.trigger is not None
                    and batch.close_at > coordinator.trigger
                ):
                    # Registered replay barrier: fixed boundary activation;
                    # runtime deployment uses asynchronous completion polling.
                    with suppress(ValueError, RuntimeError):
                        coordinator.pending.result()
                    coordinator.activate(batch.close_at)
                if closing or (trade_start is not None and date < trade_start):
                    targets = dict.fromkeys(symbols, 0.0)
                elif arm == "M0":
                    targets = {
                        s: float((daily_targets or {}).get(date, {}).get(s, 0)) for s in symbols
                    }
                elif ready:
                    scores = {}
                    for symbol in symbols:
                        x = values[symbol]
                        if x is None:
                            scores[symbol] = 0.0
                            continue
                        score = float(np.mean(x[:2]))
                        if coordinator.active is not None:
                            score = float(coordinator.active.expression.predict(x))
                        scores[symbol] = max(0.0, score)
                    total = sum(scores.values())
                    targets = {
                        s: min(0.25, 0.9 * v / total) if total else 0 for s, v in scores.items()
                    }
                    metals = sum(targets.get(s, 0) for s in ("GLD", "SLV"))
                    if metals > METALS_CAP:
                        for symbol in ("GLD", "SLV"):
                            if symbol in targets:
                                targets[symbol] *= METALS_CAP / metals
                else:
                    targets = {s: float(ledger.weights().get(parse_symbol(s), 0)) for s in symbols}
                # Shared caps/position count across all four signal arms.
                ranked = sorted(symbols, key=lambda s: (-targets[s], symbols.index(s)))[:4]
                targets = {s: min(0.25, w) if s in ranked else 0.0 for s, w in targets.items()}
                metals_total = sum(targets.get(s, 0) for s in ("GLD", "SLV"))
                if metals_total > METALS_CAP:
                    for metal in ("GLD", "SLV"):
                        if metal in targets:
                            targets[metal] *= METALS_CAP / metals_total
                for missing_symbol in set(symbols) - set(bars):
                    targets[missing_symbol] = min(
                        targets[missing_symbol],
                        float(ledger.weights().get(parse_symbol(missing_symbol), 0)),
                    )
                decision = apply_active_risk(
                    targets,
                    {str(s): w for s, w in ledger.weights().items()},
                    state,
                    risk_config,
                    default_shocks(symbols),
                    stale=not any(admitted),
                )
                state = decision.state
                if pending != decision.targets:
                    pending = decision.targets
                    valid_after = batch.close_at
                decisions.append(
                    {
                        "at": str(batch.close_at),
                        "targets": pending,
                        "risk_reasons": decision.reasons,
                        "model_id": coordinator.active.identity if coordinator.active else None,
                    }
                )
            fill_log += [{"at": str(batch.start), **asdict(f)} for f in fills]
            records.append(
                {
                    "at": str(batch.close_at),
                    "session": str(date),
                    "equity": float(ledger.equity),
                    "cash": float(ledger.cash),
                    "exposure": ledger.gross_exposure,
                    "fills": len(fills),
                    "missed": batch_missed,
                    "fees": float(sum((f.cost_cash for f in fills), Decimal(0))),
                    "market_pnl": float(sum((a.market_pnl for a in attributions), Decimal(0))),
                    "distributions": float(
                        sum((a.distributions for a in attributions), Decimal(0))
                    ),
                    "accounting_residual": str(residual),
                    "complete": complete,
                }
            )
        if ledger is not None and ledger.positions:
            failed_liquidations.append(str(last_session))
        return {
            "schema": "minute-portfolio-replay/1",
            "arm": arm,
            "cost_bps": cost_bps,
            "initial_equity": float(initial_cash),
            "records": records,
            "fills": fill_log,
            "decisions": decisions,
            "missed": missed_log,
            "refits": coordinator.events,
            "stale_minutes": stale_minutes,
            "failed_liquidations": failed_liquidations,
            "refit_checkpoint": coordinator.checkpoint(),
            "risk_config": risk_config.model_dump(),
            "risk_limits_are_research_only": True,
            "feature_revisions": stream.revisions,
            "duplicate_bars": stream.duplicates,
            "execution_assumption": (
                "later open; aggregate raw-volume capacity; 1s observation latency"
            ),
            "fitting_assumption": (
                "mature-label screen; replay completion barrier; portfolio evidence separate"
            ),
        }
    finally:
        coordinator.close()
