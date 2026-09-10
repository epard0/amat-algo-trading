"""Independent first passes, bounded attempts, and deterministic policy resolution."""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from portfolio_team.assessment_validation import validate_assessment
from portfolio_team.contracts import ROLES, Assessment, Bundle, Decision, Policy, Role
from portfolio_team.evidence import sha256
from portfolio_team.model_runtime import RemoteProvider
from portfolio_team.providers import PROMPTS, Provider
from portfolio_team.remote_coordinator import remote_phase
from portfolio_team.store import Store

PROTOCOL_BYTES = json.dumps(
    {"protocol": "offline-committee/1", "prompts": PROMPTS}, sort_keys=True
).encode()
PROTOCOL_SHA256 = sha256(PROTOCOL_BYTES)


def _phase(
    store: Store,
    provider: Provider,
    bundle: Bundle,
    review_id: str,
    *,
    roles: tuple[Role, ...],
    policy: Policy,
    previous: tuple[Assessment, ...] = (),
) -> tuple[Assessment, ...]:
    results: dict[Role, Assessment] = {}
    pending = set(roles)
    for role in roles:
        cached = store.cached(review_id, role)
        if cached:
            validate_assessment(cached, role, bundle, previous)
            results[role] = cached
            pending.remove(role)
    while pending:
        failures: set[Role] = set()
        with ThreadPoolExecutor(max_workers=len(pending)) as pool:
            futures = {}
            for role in sorted(pending):
                attempt = store.begin_attempt(review_id, role, policy.max_attempts_per_role)
                futures[pool.submit(provider.assess, role, bundle, previous)] = (role, attempt)
            for future in as_completed(futures):
                role, attempt = futures[future]
                try:
                    assessment = Assessment.model_validate(future.result())
                    validate_assessment(assessment, role, bundle, previous)
                except Exception as exc:
                    # Do not persist provider exception text: it may contain credentials.
                    error = f"invalid_or_failed_assessment:{type(exc).__name__}"
                    store.finish_attempt(review_id, role, attempt, None, error)
                    failures.add(role)
                else:
                    store.finish_attempt(review_id, role, attempt, assessment, None)
                    results[role] = assessment
        pending = failures
    return tuple(results[role] for role in roles)


def review_identity(
    bundle: Bundle,
    provider: Provider | RemoteProvider,
    policy: Policy,
    *,
    single_reviewer: bool = False,
) -> tuple[str, bytes]:
    protocol_bytes = provider.protocol() if isinstance(provider, RemoteProvider) else PROTOCOL_BYTES
    if single_reviewer:
        protocol_data = json.loads(protocol_bytes)
        protocol_data["mode"] = "single-reviewer-baseline"
        protocol_bytes = json.dumps(protocol_data, sort_keys=True).encode()
    protocol_hash = sha256(protocol_bytes)
    review_id = sha256(
        json.dumps(
            {
                "bundle": bundle.digest(),
                "policy": policy.digest(),
                "provider": provider.id,
                "protocol": protocol_hash,
            },
            sort_keys=True,
        ).encode()
    )
    return review_id, protocol_bytes


def run_review(
    store: Store,
    bundle: Bundle,
    provider: Provider | RemoteProvider,
    policy: Policy | None = None,
    *,
    single_reviewer: bool = False,
) -> Decision:
    selected_policy = policy or Policy()
    review_id, protocol_bytes = review_identity(
        bundle, provider, selected_policy, single_reviewer=single_reviewer
    )
    store.record_artifact(selected_policy.canonical().encode())
    protocol_hash = store.record_artifact(protocol_bytes)
    existing = store.decision(review_id)
    if existing:
        return existing
    store.start(review_id, bundle.digest())
    try:
        if isinstance(provider, RemoteProvider):
            deadline = time.monotonic() + provider.settings.review_timeout_seconds
            independent = (
                ()
                if single_reviewer
                else asyncio.run(
                    remote_phase(
                        store,
                        provider,
                        bundle,
                        review_id,
                        roles=ROLES[:-1],
                        policy=selected_policy,
                        deadline=deadline,
                    )
                )
            )
            chief = asyncio.run(
                remote_phase(
                    store,
                    provider,
                    bundle,
                    review_id,
                    roles=("chief_pm",),
                    policy=selected_policy,
                    deadline=deadline,
                    previous=independent,
                )
            )[0]
        else:
            independent = (
                ()
                if single_reviewer
                else _phase(
                    store, provider, bundle, review_id, roles=ROLES[:-1], policy=selected_policy
                )
            )
            chief = _phase(
                store,
                provider,
                bundle,
                review_id,
                roles=("chief_pm",),
                policy=selected_policy,
                previous=independent,
            )[0]
        proposed = chief.disposition
        effective = proposed
        reason = "Complete simulated committee; recommendations are research-only."
        if isinstance(provider, RemoteProvider):
            reason = "Complete model review; recommendations are research-only."
        if proposed == "shortlist_for_forward_shadow":
            effective = "request_evidence"
            reason = "This historical aggregate adapter cannot establish shortlisting requirements."
        if any(check.scope == "comparison" and check.status != "pass" for check in bundle.checks):
            effective = "request_evidence"
            reason = "Required comparison evidence is missing or incompatible."
        decision = Decision(
            review_id=review_id,
            evidence_sha256=bundle.digest(),
            policy_sha256=selected_policy.digest(),
            provider_id=provider.id,
            protocol_sha256=protocol_hash,
            proposed_disposition=proposed,
            effective_disposition=effective,
            resolution_reason=reason,
            assessments=(*independent, chief),
        )
        store.complete(decision)
    except Exception as exc:
        store.fail(review_id, f"review_failed:{type(exc).__name__}")
        raise
    return decision
