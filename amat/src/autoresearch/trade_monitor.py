"""Join simulated instructions to their causal model context and matured labels."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult, SessionRecord
from amat.domain.fills import Fill
from amat.domain.orders import SimulatedOrder
from amat.reporting.trade_monitor import HORIZONS, document, event_id, finite
from autoresearch.contracts import Candidate
from autoresearch.entry_model import PREDICTION_NAMES, REGIMES, entry_features, forecast_key
from autoresearch.experiments import Simulation


def markouts(
    panel: MarketPanel, index: int, column: int, price: float | None, side: str, *, stop: int
) -> dict[str, Any]:
    """Fixed horizons from the fill to future closes on a common adjustment basis."""
    outcomes = {}
    raw = float(panel.raw_closes[index, column])
    adjusted = float(panel.adjusted_closes[index, column])
    valid_entry = price is not None and all(
        np.isfinite(value) and value > 0 for value in (price, raw, adjusted)
    )
    for horizon in HORIZONS:
        end = index + horizon - 1
        row: dict[str, Any] = {"status": "pending", "horizon_sessions": horizon}
        if not valid_entry:
            row["status"] = "no_fill_price"
        elif end < stop:
            path = panel.adjusted_closes[index : end + 1, column]
            if np.isfinite(path).all() and (path > 0).all():
                assert price is not None  # noqa: S101 - established by valid_entry
                returns = (path / (price * adjusted / raw) - 1) * 10000
                signed = returns if side == "buy" else -returns
                row.update(
                    status="matured",
                    end_session=str(panel.sessions[end]),
                    asset_return_bps=float(returns[-1]),
                    signed_markout_bps=float(signed[-1]),
                    favourable_close_bps=max(0.0, float(signed.max())),
                    adverse_close_bps=max(0.0, -float(signed.min())),
                )
            else:
                row["status"] = "missing_price"
        outcomes[str(horizon)] = row
    return outcomes


def _execution_event(
    record: SessionRecord,
    order: SimulatedOrder | None,
    fills: list[Fill],
    *,
    symbol: str,
    side: str,
    reason: str,
    ordinal: int,
    model_id: str,
    run_id: str,
) -> dict[str, Any]:
    quantity = sum((fill.quantity for fill in fills), Decimal(0))
    gross = sum((fill.gross_notional for fill in fills), Decimal(0))
    price = float(gross / quantity) if quantity else None
    expected = order.quantity if order else None
    alerts = []
    if order is None:
        instruction = "unverified_no_instruction"
    elif quantity > order.quantity:
        instruction = "quantity_exceeds_instruction"
        alerts.append("overfill")
    else:
        instruction = "recorded_intent" if not fills else "matched"
    status = (
        "filled_quantity_recorded"
        if expected is None
        else ("filled" if quantity == expected else "partial")
    )
    if not quantity:
        status = "missed"
    if status in {"partial", "missed"}:
        alerts.append(status)
    if expected is None and quantity:
        alerts.append("missing_instruction")
    missed = [
        str(item.reason)
        for item in record.missed
        if str(item.symbol) == symbol and str(item.side) == side
    ]
    return {
        "event_id": event_id(model_id, run_id, record.session, symbol, side, ordinal),
        "source": "backtest",
        "run_id": run_id,
        "model_id": model_id,
        "event_kind": "instruction",
        "symbol": symbol,
        "side": side,
        "decision_session": str(order.signal_session) if order else None,
        "execution_session": str(record.session),
        "reason": reason,
        "instruction_status": instruction,
        "execution_status": status,
        "intended_quantity": str(expected) if expected is not None else None,
        "filled_quantity": str(quantity),
        "fill_price": price,
        "cost_cash": float(sum((fill.cost_cash for fill in fills), Decimal(0))),
        "miss_reasons": missed,
        "alerts": alerts,
    }


def _forecast_rows(
    simulation: Simulation, candidate: Candidate, decision: int, column: int, stop: int
) -> list[dict[str, Any]]:
    spec = candidate.entry_model
    trace = simulation.entry_model(candidate)
    if trace is None or spec is None:
        return []
    forecast = simulation.entry_forecasts[
        Candidate(config=candidate.config).digest(), forecast_key(spec)
    ]
    outcomes = forecast.outcomes
    targets = (
        outcomes.net_return,
        outcomes.adverse_excursion,
        outcomes.deferral_advantage,
        outcomes.deferral_advantage,
    )
    offsets = (spec.horizon + 1, 5, 2, 2)
    bases = (
        "next-window to window after horizon; adjusted return net of 2bps each way",
        "worst loss from next-window through five sessions; entry close then subsequent lows",
        "next-window / following-window minus one; benefit of one-session deferral",
        "same deferral target, predicted lower quartile; not a confidence interval",
    )
    rows = []
    for head, name in enumerate(PREDICTION_NAMES):
        prediction = finite(trace.predictions[decision, column, head])
        if prediction is None:
            continue
        mature = decision + offsets[head] < stop
        actual = finite(targets[head][decision, column] * 10000) if mature else None
        rows.append(
            {
                "head": name,
                "horizon_sessions": spec.horizon if head == 0 else offsets[head],
                "basis": bases[head],
                "prediction_bps": prediction,
                "actual_bps": actual,
                "error_bps": actual - prediction if actual is not None else None,
                "status": "matured"
                if actual is not None
                else ("missing_price" if mature else "pending"),
                "matures_session": str(simulation.panel.sessions[decision + offsets[head]])
                if mature
                else None,
            }
        )
    return rows


def build_monitor(  # noqa: PLR0912, PLR0915 - join causal evidence before computing outcomes
    simulation: Simulation, candidate: Candidate, result: BacktestResult, *, run_id: str
) -> dict[str, Any]:
    panel = simulation.panel
    run_id = f"{run_id}@{simulation.program.digest()[:12]}"
    dates = {str(session): i for i, session in enumerate(panel.sessions)}
    columns = {str(symbol): i for i, symbol in enumerate(panel.symbols)}
    stop = dates[str(result.sessions[-1].session)] + 1
    first = dates[str(result.sessions[0].session)] + 1
    trace = simulation.diagnostic_trace(candidate)
    entry = simulation.entry_model(candidate)
    _, regimes = entry_features(panel)
    rows = []
    bought_decisions = set()
    for record in result.sessions:
        grouped: dict[tuple[str, str], list[Fill]] = defaultdict(list)
        for fill in record.fills:
            grouped[str(fill.symbol), str(fill.side)].append(fill)
        orders: dict[tuple[str, str], SimulatedOrder] = {}
        for instruction in record.instructions:
            key = str(instruction.symbol), str(instruction.side)
            if key in orders:
                raise ValueError("ambiguous same-symbol same-side instructions")
            orders[key] = instruction
        missed = {(str(m.symbol), str(m.side)): str(m.reason) for m in record.missed}
        for ordinal, (symbol, side) in enumerate(
            sorted(grouped.keys() | orders.keys() | missed.keys())
        ):
            order = orders.get((symbol, side))
            row = _execution_event(
                record,
                order,
                grouped.get((symbol, side), []),
                symbol=symbol,
                side=side,
                reason=str(order.reason) if order else missed.get((symbol, side), "unrecorded"),
                ordinal=ordinal,
                model_id=candidate.digest(),
                run_id=run_id,
            )
            if side == "buy" and row["decision_session"]:
                bought_decisions.add((row["decision_session"], symbol))
            rows.append(row)
    # Observe the baseline opportunity set as well as acted-on trades. These
    # are labelled price counterfactuals, not fills or demonstrated policy gains.
    if entry is not None and candidate.entry_model is not None:
        forecast = simulation.entry_forecasts[
            Candidate(config=candidate.config).digest(), forecast_key(candidate.entry_model)
        ]
        for decision, column in zip(*np.nonzero(forecast.opportunities), strict=True):
            execution = int(decision) + 1
            symbol = str(panel.symbols[column])
            decision_date = str(panel.sessions[decision])
            if not first <= execution < stop or (decision_date, symbol) in bought_decisions:
                continue
            price = finite(panel.window_vwaps[execution, column])
            if panel.window_volumes[execution, column] <= 0:
                price = None
            rows.append(
                {
                    "event_id": event_id(candidate.digest(), run_id, decision_date, symbol, "cf"),
                    "source": "backtest",
                    "run_id": run_id,
                    "model_id": candidate.digest(),
                    "event_kind": "baseline_opportunity",
                    "symbol": symbol,
                    "side": "buy",
                    "decision_session": decision_date,
                    "execution_session": str(panel.sessions[execution]),
                    "reason": str(entry.reasons[decision, column]),
                    "instruction_status": "counterfactual",
                    "execution_status": "not_an_order",
                    "intended_quantity": None,
                    "filled_quantity": None,
                    "fill_price": price,
                    "cost_cash": None,
                    "alerts": [],
                }
            )
    for row in rows:
        column = columns[row["symbol"]]
        execution = dates[row["execution_session"]]
        decision = dates.get(row["decision_session"])
        row.update(regime="unknown", signal_score=None, forecasts=[], decision_to_fill_gap_bps=None)
        row["outcomes"] = markouts(
            panel, execution, column, row["fill_price"], row["side"], stop=stop
        )
        row["model_name"] = candidate.config.strategy.name
        row["market_source"] = panel.source
        if decision is None:
            row["forecast_status"] = "missing_decision_provenance"
            continue
        row["regime"] = REGIMES[int(regimes[decision, column])]
        row["signal_score"] = finite(trace.scores[decision, column])
        row["signal_score_basis"] = "diagnostic effective score; not a probability or return"
        row["forecasts"] = (
            _forecast_rows(simulation, candidate, decision, column, stop)
            if row["side"] == "buy"
            else []
        )
        row["forecast_status"] = "available" if row["forecasts"] else "not_available_for_event"
        if entry is not None:
            row["entry_policy"] = {
                "reason": str(entry.reasons[decision, column]),
                "increase_multiplier": finite(entry.increase_multipliers[decision, column]),
                "utility_bps": finite(entry.utility_bps[decision, column]),
            }
        ratio = panel.adjusted_closes[execution, column] / panel.raw_closes[execution, column]
        reference = finite(panel.adjusted_closes[decision, column] / ratio)
        row["decision_reference_price"] = reference
        row["decision_to_fill_gap_basis"] = "decision close on execution-session adjustment basis"
        if reference and row["fill_price"] and row["event_kind"] == "instruction":
            sign = 1 if row["side"] == "buy" else -1
            row["decision_to_fill_gap_bps"] = (row["fill_price"] / reference - 1) * sign * 10000
    return document(
        rows,
        source="backtest",
        program_sha256=simulation.program.digest(),
        candidate_sha256=candidate.digest(),
        one_way_cost_bps=result.one_way_bps,
        market_source=panel.source,
        as_of_session=str(panel.sessions[stop - 1]),
    )
