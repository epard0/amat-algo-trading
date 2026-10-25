"""Read-only application-budget/attempt preflight, never a provider quota probe."""

from __future__ import annotations

from typing import Any

from portfolio_team.assessment_validation import validate_assessment
from portfolio_team.contracts import ROLES, Assessment, Bundle, Policy
from portfolio_team.coordinator import review_identity
from portfolio_team.model_runtime import RemoteProvider
from portfolio_team.providers import Provider
from portfolio_team.store import Store


def preflight_review(
    store: Store,
    bundle: Bundle,
    provider: Provider | RemoteProvider,
    *,
    single_reviewer: bool = False,
) -> dict[str, Any]:
    policy = Policy()
    review_id, _ = review_identity(bundle, provider, policy, single_reviewer=single_reviewer)
    saved = store.decision(review_id)
    result: dict[str, Any] = {
        "review_id": review_id,
        "provider": provider.id,
        "ready": True,
        "completed": saved is not None,
        "next_role": None,
        "budget": None,
        "execution_authority": "none",
        "limitation": (
            "Next dispatch only; not provider quota, credentials or full-review validation."
        ),
    }
    if saved:
        return result
    roles = ("chief_pm",) if single_reviewer else ROLES
    previous: list[Assessment] = []
    for role in roles:
        cached = store.cached(review_id, role)
        if cached:
            validate_assessment(cached, role, bundle, tuple(previous) if role == "chief_pm" else ())
            previous.append(cached)
            continue
        result["next_role"] = role
        result["attempts_remaining"] = max(
            0, policy.max_attempts_per_role - store.next_attempt(review_id, role) + 1
        )
        if isinstance(provider, RemoteProvider):
            request = provider.prepare(role, bundle, tuple(previous) if role == "chief_pm" else ())
            result["budget"] = store.model_budget(
                review_id,
                input_tokens=request.input_reservation,
                output_tokens=request.settings.max_output_tokens,
                limits=provider.settings.reservation_limits,
            )
        result["ready"] = result["attempts_remaining"] > 0 and (
            result["budget"] is None or result["budget"]["allowed"]
        )
        if not result["attempts_remaining"]:
            result["block_reason"] = "attempts_exhausted"
        elif not result["ready"]:
            result["block_reason"] = "deferred_budget"
        return result
    return result
