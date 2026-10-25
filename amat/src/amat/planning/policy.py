"""Inert research-policy plan contract; requires explicit actual-account state."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from amat.execution.budget import CashBudgetPolicy, budget_buys
from amat.execution.intent import build_execution_plan
from amat.portfolio import decide, ledoit_wolf_covariance, precompute_decision_inputs
from amat.risk.active import ActiveRiskConfig, RiskState, apply_active_risk

MAX_STATE_AGE_SECONDS = 720
EQUITY_TOLERANCE = 1e-6


def build_policy_plan(
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    positions: Mapping[str, Decimal],
    equity: Decimal,
    confirmed_cash: Decimal,
    state: RiskState,
    risk: ActiveRiskConfig,
    shocks: tuple[dict[str, float], ...],
    config_sha256: str,
    snapshot_id: str,
    generated_at: dt.datetime,
    committed_cash: Decimal = Decimal(0),
    cash_policy: CashBudgetPolicy | None = None,
) -> dict[str, Any]:
    """Never initialize/reset risk from a simulated history or spend pending sales."""
    if (
        generated_at.tzinfo is None
        or not 0 <= (generated_at - state.timestamp).total_seconds() <= MAX_STATE_AGE_SECONDS
    ):
        raise ValueError("fresh explicit risk/account state required")
    if abs(float(equity) - state.equity) > EQUITY_TOLERANCE:
        raise ValueError("risk state equity differs from observed account")
    inputs = precompute_decision_inputs(strategy, panel.adjusted_closes, sessions=panel.sessions)
    allocation = decide(
        strategy,
        panel.adjusted_closes,
        strategy.to_universe(),
        decision_index=len(panel) - 1,
        precomputed=inputs,
    )
    symbols = tuple(map(str, strategy.universe.symbols))
    marks = {str(s): m for s, m in panel.marks_at(len(panel) - 1).items()}
    held = {s: float(q * marks[s] / equity) for s, q in positions.items() if s in marks}
    covariance = ledoit_wolf_covariance(inputs.returns[1:], strategy.covariance)
    policy = apply_active_risk(
        {str(s): v for s, v in allocation.as_mapping(strategy.to_universe()).items()},
        held,
        state,
        risk,
        shocks,
        covariance=covariance,
    )
    rebalance = (
        strategy.rebalance.model_copy(update={"min_weight_delta": 0.0})
        if policy.reasons
        else strategy.rebalance
    )
    selected = strategy.model_copy(update={"rebalance": rebalance})
    plan = build_execution_plan(
        selected,
        engine="research-policy/1",
        as_of_session=panel.sessions[-1],
        target_weights=policy.targets,
        marks=marks,
        current_positions=dict(positions),
        account_equity=equity,
        config_sha256=config_sha256,
        snapshot_id=snapshot_id,
        code_revision="source-bound-research-policy",
        generated_at=generated_at,
    )
    requests = {i.symbol: i.quantity for i in plan.intents if i.side == "buy"}
    quantities, residual = budget_buys(
        requests,
        marks,
        confirmed_cash=confirmed_cash,
        equity=equity,
        committed_cash=committed_cash,
        one_way_bps=strategy.costs.baseline_one_way_bps,
        policy=cash_policy,
    )
    payload = plan.as_dict()
    for intent in payload["intents"]:
        if intent["side"] == "buy":
            intent["quantity"] = str(quantities[intent["symbol"]])
            intent["notional"] = str(quantities[intent["symbol"]] * marks[intent["symbol"]])
    payload["intents"] = [i for i in payload["intents"] if Decimal(i["quantity"]) > 0]
    envelope = {
        "schema": "research-policy-plan/1",
        "research_only": True,
        "required_capabilities": ["actual-risk-state/1", "cash-budget/1"],
        "account_id": state.account_id,
        "state": asdict(policy.state),
        "risk_config": risk.model_dump(),
        "risk_reasons": policy.reasons,
        "cash_residual": {s: str(q) for s, q in residual.items()},
        "account_cash": str(confirmed_cash),
        "committed_cash": str(committed_cash),
        "expires_at": (generated_at + dt.timedelta(minutes=5)).isoformat(),
        "plan": payload,
        "universe": symbols,
    }
    raw = json.loads(json.dumps(envelope, default=str, allow_nan=False))
    raw["decision_id"] = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    return raw


def build_intraday_plan(
    targets: dict[str, float],
    marks: Mapping[str, Decimal],
    positions: Mapping[str, Decimal],
    *,
    equity: Decimal,
    confirmed_cash: Decimal,
    committed_cash: Decimal,
    state: RiskState,
    risk: ActiveRiskConfig,
    shocks: tuple[dict[str, float], ...],
    now: dt.datetime,
    data_available_at: dt.datetime,
    session_close: dt.datetime,
    config_sha256: str,
    model_id: str,
    cash_policy: CashBudgetPolicy | None = None,
) -> dict[str, Any]:
    """Inert same-session authorization, distinct from the next-session daily plan."""
    import math  # noqa: PLC0415

    if (
        any(t.tzinfo is None for t in (now, data_available_at, session_close))
        or data_available_at > now
        or now >= session_close
        or not 0 <= (now - state.timestamp).total_seconds() <= MAX_STATE_AGE_SECONDS
    ):
        raise ValueError("invalid intraday information/account clock")
    if abs(float(equity) - state.equity) > EQUITY_TOLERANCE or not model_id:
        raise ValueError("missing model or inconsistent account")
    if set(positions) - set(targets) or set(targets) - set(marks):
        raise ValueError("unrecognized positions or missing marks")
    if any(not q.is_finite() or q < 0 for q in positions.values()) or any(
        not p.is_finite() or p <= 0 for p in marks.values()
    ):
        raise ValueError("invalid raw account marks")
    if any(not math.isfinite(w) or w < 0 for w in targets.values()):
        raise ValueError("invalid targets")
    held = {s: float(q * marks[s] / equity) for s, q in positions.items()}
    decision = apply_active_risk(targets, held, state, risk, shocks)
    raw: list[dict[str, Any]] = []
    buys: dict[str, Decimal] = {}
    for symbol, weight in decision.targets.items():
        quantity = Decimal(str(weight)) * equity / marks[symbol] - positions.get(symbol, Decimal(0))
        if quantity == 0:
            continue
        if quantity > 0:
            buys[symbol] = quantity
        raw.append(
            {
                "symbol": symbol,
                "side": "buy" if quantity > 0 else "sell",
                "quantity": str(abs(quantity)),
                "reference_price": str(marks[symbol]),
                "notional": str(abs(quantity) * marks[symbol]),
                "target_weight": weight,
            }
        )
    scaled, residual = budget_buys(
        buys,
        marks,
        confirmed_cash=confirmed_cash,
        equity=equity,
        committed_cash=committed_cash,
        policy=cash_policy,
    )
    for intent in raw:
        if intent["side"] == "buy":
            intent["quantity"] = str(scaled[intent["symbol"]])
            intent["notional"] = str(scaled[intent["symbol"]] * marks[intent["symbol"]])
    body = {
        "schema": "research-policy-plan/1",
        "research_only": True,
        "required_capabilities": ["actual-risk-state/1", "cash-budget/1"],
        "account_id": state.account_id,
        "state": asdict(decision.state),
        "risk_config": risk.model_dump(),
        "risk_reasons": decision.reasons,
        "data_available_at": str(data_available_at),
        "generated_at": str(now),
        "valid_after": str(now + dt.timedelta(seconds=1)),
        "expires_at": str(min(now + dt.timedelta(minutes=5), session_close)),
        "cash_residual": {s: str(q) for s, q in residual.items()},
        "plan": {
            "config_sha256": config_sha256,
            "engine": "research-policy/1",
            "model_id": model_id,
            "intents": [i for i in raw if Decimal(i["quantity"]) > 0],
        },
    }
    payload = json.loads(json.dumps(body, default=str, allow_nan=False))
    payload["decision_id"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    return payload
