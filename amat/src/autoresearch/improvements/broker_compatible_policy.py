"""Compile the explicit research DAY-limit policy; never grants broker authority."""

from __future__ import annotations

from typing import Any

from autoresearch.improvements.agent_native_adapter import (
    AdapterPlan,
    canonical_sha,
    compile_adapter,
)

SCHEMA = "research-causal-day-limit/1"
RULES = {
    "trigger": "one_fresh_executable_quote_after_completed_prefix",
    "prefix_seconds": 60,
    "max_quote_age_seconds": 2,
    "max_prefix_delay_seconds": 5,
    "entry_participation": "0.01",
    "opening_window_seconds": 300,
    "time_in_force": "day",
    "expiry": "stop_submission_request_cancel_keep_commitments_until_terminal",
    "fill": "broker_observation_only_no_vwap_or_queue_inference",
    "retry": "never_automatically_resubmit_original_intent",
    "corporate_actions": "explicit_empty_coverage_required",
}


def compile_broker_policy(plan: AdapterPlan) -> dict[str, Any]:
    """Freeze original authorization and a distinct causal execution contract.

    Original expiry is retained as the last submission/cancel-request boundary;
    this policy explicitly accounts for fills before cancellation confirmation.
    It does not claim an impossible broker-enforced hard fill cutoff.
    """
    original = plan.as_dict()
    if compile_adapter(original["proposal"], plan.context) != plan:
        raise ValueError("Original adapter plan changed")
    if str(plan.context.policy.one_way_bps) not in {"2", "6", "10"} or any(
        a["kind"] not in {"hold", "open", "resize", "close", "stop_loss", "take_profit"}
        for a in original["proposal"]["actions"]
    ):
        raise ValueError("Unsupported fixed-cost or action capability")
    body = {
        "schema": SCHEMA,
        "engine": "research-policy/1",
        "research_only": True,
        "execution_authority": "none",
        "promotion_authority": False,
        "required_capabilities": [SCHEMA],
        "rules": dict(RULES),
        "original_plan": original,
        "policy_id": canonical_sha(
            {"schema": SCHEMA, "rules": RULES, "cost_bps": str(plan.context.policy.one_way_bps)}
        ),
    }
    return {**body, "contract_sha256": canonical_sha(body)}
