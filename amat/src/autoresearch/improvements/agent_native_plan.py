"""Lossless, inert native-plan envelope for the research opening adapter.

The legacy executor must refuse this capability contract. A native quantity
projection alone is deliberately insufficient: the bound adapter plan owns
conditions, exact expiry and existing commitments. Nothing here grants broker
authority or reconstructs a trusted account from an untrusted artifact.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from amat.execution.intent import ExecutionPlan, TradeIntent
from autoresearch.improvements.agent_native_adapter import (
    POLICY,
    ZONE,
    AdapterPlan,
    canonical_sha,
    compile_adapter,
)
from autoresearch.improvements.agent_portfolio_arm import ResearchContext

SCHEMA = "research-native-action-plan/1"
CAPABILITIES = (
    "research-exact-quantity/1",
    "opening-quote-conditions/1",
    "complete-window-limit/1",
    "original-expiry-and-commitments/1",
    "native-whole-entry-volume-cap/1",
    "single-attempt-receipt/1",
)


def export_native_plan(plan: AdapterPlan) -> dict[str, Any]:
    """Project exact quantities while retaining the complete executable research policy."""
    bound = plan.as_dict()
    proposal = bound["proposal"]
    if compile_adapter(proposal, plan.context) != plan:
        raise ValueError("Research plan differs from its original context/proposal")
    context = plan.context
    positions = dict(context.positions)
    marks = {row.symbol: row.mark for row in context.evidence}
    intents = []
    conditions = []
    for index, action in enumerate(proposal["actions"]):
        if action["kind"] == "hold":
            continue
        symbol = action["symbol"]
        quantity = Decimal(action["quantity"])
        current = positions.get(symbol, Decimal(0))
        remaining = current + quantity * (1 if action["side"] == "buy" else -1)
        intents.append(
            TradeIntent(
                symbol,
                action["side"],
                quantity,
                marks[symbol],
                quantity * marks[symbol],
                float(current * marks[symbol] / context.equity),
                float(remaining * marks[symbol] / context.equity),
                "position_transition" if remaining == 0 else "weight_delta",
            )
        )
        conditions.append(
            {
                "action_index": index,
                "kind": action["kind"],
                "limit_price": action["limit_price"],
                "trigger_price": action.get("trigger_price"),
                "execution_at": proposal["execution_at"],
                "expires_at": proposal["expires_at"],
            }
        )
    native = ExecutionPlan(
        schema_version="1.0.0",
        generated_at=context.decision_at,
        engine="research-policy/1",
        strategy_name="explicit-research-actions",
        strategy_version=POLICY,
        config_sha256=context.policy.identity,
        snapshot_id=context.evidence_sha256,
        appended_sessions=0,
        code_revision="",
        as_of_session=context.decision_at.astimezone(ZONE).date(),
        earliest_execution_session=context.execution_at.astimezone(ZONE).date(),
        window_start_local=dt.time(9, 30),
        window_end_local=dt.time(9, 35),
        exchange_timezone=str(ZONE),
        expires_at=context.expires_at,
        account_equity=context.equity,
        target_weights={},
        current_positions=positions,
        intents=tuple(intents),
        withheld=(),
        unrecognized_holdings=(),
        caveats=(
            "RESEARCH ONLY: legacy broker execution is unsupported.",
            "Weights are descriptive; exact quantities and bound conditions control this policy.",
            "Reference marks are neither limit prices nor expected fills.",
        ),
    ).as_dict()
    # Protect both the outer contract and a mistakenly extracted inner projection.
    native["required_capabilities"] = list(CAPABILITIES)
    native["research_only"] = True
    native["execution_authority"] = "none"
    body = {
        "schema": SCHEMA,
        "engine": "research-policy/1",
        "research_only": True,
        "execution_authority": "none",
        "promotion_authority": False,
        "required_capabilities": list(CAPABILITIES),
        "adapter_plan": bound,
        "native_plan": native,
        "conditions": conditions,
        "account_id": context.account_id,
        "policy_id": context.policy.identity,
        "context_sha256": context.evidence_sha256,
        "original_pending_cash": str(
            sum((row.pending_cash for row in context.authorized_intents), Decimal(0))
        ),
    }
    return {**body, "envelope_sha256": canonical_sha(body)}


def restore_native_plan(payload: dict[str, Any], context: ResearchContext) -> AdapterPlan:
    """Verify against caller-supplied trusted state, never authority declared by the file."""
    try:
        plan = compile_adapter(payload["adapter_plan"]["proposal"], context)
        expected = export_native_plan(plan)
        if payload != expected or canonical_sha(payload) != canonical_sha(expected):
            raise ValueError("Native research envelope differs from exact trusted context/plan")
    except (KeyError, TypeError) as exc:
        raise ValueError("Malformed native research envelope") from exc
    return plan
