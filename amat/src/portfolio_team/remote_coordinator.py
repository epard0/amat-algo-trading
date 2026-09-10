"""Async model calls with durable reservations and response capture before validation."""

from __future__ import annotations

import asyncio
import json
import time

from portfolio_team.assessment_validation import validate_assessment
from portfolio_team.contracts import Assessment, Bundle, Policy, Role
from portfolio_team.model_runtime import ModelRunError, RemoteProvider
from portfolio_team.store import Store


async def remote_phase(
    store: Store,
    provider: RemoteProvider,
    bundle: Bundle,
    review_id: str,
    *,
    roles: tuple[Role, ...],
    policy: Policy,
    deadline: float,
    previous: tuple[Assessment, ...] = (),
) -> tuple[Assessment, ...]:
    async def one(role: Role) -> Assessment:
        cached = store.cached(review_id, role)
        if cached:
            validate_assessment(cached, role, bundle, previous)
            return cached
        settings = provider.settings
        repair_hint: str | None = None
        while time.monotonic() < deadline:
            request = provider.prepare(role, bundle, previous)
            if repair_hint:
                contents = json.loads(request.contents)
                contents["previous_validation_failure"] = repair_hint
                request = request.model_copy(
                    update={"contents": json.dumps(contents, sort_keys=True)}
                )
            attempt = store.begin_attempt(review_id, role, policy.max_attempts_per_role)
            try:
                store.reserve_model_call(
                    review_id,
                    role,
                    attempt,
                    request_json=request.canonical(),
                    input_tokens=request.input_reservation,
                    output_tokens=request.settings.max_output_tokens,
                    limits=(
                        settings.max_input_reservation_per_review,
                        settings.max_output_reservation_per_review,
                        settings.max_input_reservation_per_day,
                        settings.max_output_reservation_per_day,
                    ),
                )
            except ValueError:
                store.finish_attempt(review_id, role, attempt, None, "deferred_budget")
                raise ModelRunError("deferred_budget") from None
            started = time.monotonic()
            response = await provider.invoke(request, deadline - started)
            # Record even malformed/blocked output before attempting schema validation.
            store.finish_model_call(
                review_id,
                role,
                attempt,
                response_json=response.canonical(),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                elapsed=time.monotonic() - started,
                error=response.error_code,
            )
            error = response.error_code
            retryable = response.retryable
            if error is None:
                try:
                    assessment = Assessment.model_validate_json(response.text or "")
                    validate_assessment(assessment, role, bundle, previous)
                except ValueError:
                    error, retryable = "invalid_structured_assessment", True
                    repair_hint = (
                        "Previous output failed schema/reference validation. Return every required "
                        "field, exact role/evidence ID, only allowed references, and all previous "
                        "finding IDs in chief addressed_findings. Use no additional fields."
                    )
                else:
                    store.finish_attempt(review_id, role, attempt, assessment, None)
                    return assessment
            store.finish_attempt(review_id, role, attempt, None, error)
            if not retryable:
                raise ModelRunError(error or "provider_failure")
            if error == "provider_http_429" and attempt < policy.max_attempts_per_role:
                await asyncio.sleep(
                    min(settings.rate_limit_retry_seconds, max(0, deadline - time.monotonic()))
                )
        raise ModelRunError("review_deadline")

    semaphore = asyncio.Semaphore(provider.settings.concurrent_requests)

    async def bounded(role: Role) -> Assessment:
        async with semaphore:
            return await one(role)

    # Collect every in-flight result so failures do not leave unaccounted background calls.
    responses = await asyncio.gather(*(bounded(role) for role in roles), return_exceptions=True)
    for response in responses:
        if isinstance(response, BaseException):
            raise response
    return tuple(response for response in responses if isinstance(response, Assessment))
