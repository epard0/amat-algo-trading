"""Ordered, bounded failover with per-attempt budget hooks."""

from __future__ import annotations

import asyncio
import json
import os
import time
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from llmrouter.contracts import ChatRequest, ChatResponse, ProviderName, Record, Transport
from llmrouter.costs import CostLedger
from llmrouter.health import HealthPolicy, ProviderHealth
from llmrouter.transports import BASE_URLS, GeminiTransport, OpenAICompatibleTransport

ENVIRONMENT = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "groq": "GROQ_API_KEY",
}


def wire_schema(value: Any, *, gemini: bool) -> Any:
    """Adapt the grammar; local validation still enforces the full contract."""
    if isinstance(value, list):
        return [wire_schema(item, gemini=gemini) for item in value]
    if not isinstance(value, dict):
        return value
    excluded = {"default", "title"}
    if gemini:
        excluded |= {"additionalProperties", "minItems", "maxItems", "minLength", "maxLength"}
    result = {
        key: wire_schema(item, gemini=gemini) for key, item in value.items() if key not in excluded
    }
    if "properties" in result and not gemini:
        result["required"] = list(result["properties"])
        result["additionalProperties"] = False
    return result


class Target(Record):
    provider: ProviderName
    model: str = Field(min_length=1, max_length=200)


class Chain(Record):
    targets: tuple[Target, ...] = Field(min_length=1, max_length=8)
    complex_targets: tuple[Target, ...] = Field(
        default=(), max_length=8, exclude_if=lambda value: not value
    )

    coding_targets: tuple[Target, ...] = Field(
        default=(), max_length=8, exclude_if=lambda value: not value
    )

    judge_targets: tuple[Target, ...] = Field(
        default=(), max_length=8, exclude_if=lambda value: not value
    )

    def for_task(self, task_class: str) -> tuple[Target, ...]:
        if task_class == "judge" and self.judge_targets:
            return self.judge_targets
        if task_class == "coding" and self.coding_targets:
            return self.coding_targets
        if task_class in {"coding", "complex"} and self.complex_targets:
            return self.complex_targets
        return self.targets

    @classmethod
    def load(cls, path: Path) -> Chain:
        return cls.model_validate(tomllib.loads(path.read_text()))


class Budget(Protocol):
    def reserve(self, target: Target, request: ChatRequest) -> int: ...
    def settle(self, call_id: int, response: ChatResponse) -> None: ...


class RepairableResponseError(ValueError):
    """Locally authored, credential-free feedback safe to send to a proposer."""

    def __init__(self, code: str, feedback: str) -> None:
        super().__init__(code)
        self.code, self.feedback = code, feedback


class Router:
    def __init__(self, chain: Chain, *, transports: dict[str, Transport] | None = None) -> None:
        self.chain = chain
        self.health: dict[str, ProviderHealth] = {
            target.provider: ProviderHealth(HealthPolicy(), clock=time.time)
            for target in (
                *chain.targets,
                *chain.complex_targets,
                *chain.coding_targets,
                *chain.judge_targets,
            )
        }
        self.model_health = {
            f"{target.provider}/{target.model}": ProviderHealth(HealthPolicy(), clock=time.time)
            for target in (
                *chain.targets,
                *chain.complex_targets,
                *chain.coding_targets,
                *chain.judge_targets,
            )
        }
        self.last_attempts: list[dict[str, str]] = []
        self.transports: dict[str, Transport] = (
            transports
            if transports is not None
            else {
                "openai": OpenAICompatibleTransport("responses"),
                "gemini": GeminiTransport(),
                "openrouter": OpenAICompatibleTransport("chat"),
                "nvidia": OpenAICompatibleTransport("chat"),
                "groq": OpenAICompatibleTransport("chat"),
            }
        )

    def cooldowns(self) -> dict[str, float]:
        return {
            provider: health.cooling_until
            for provider, health in {**self.health, **self.model_health}.items()
            if health.cooling_until is not None
        }

    def restore_cooldowns(self, values: dict[str, float]) -> None:
        for provider, until in values.items():
            health = {**self.health, **self.model_health}.get(provider)
            if health is not None and until > time.time():
                health.state = "cooling"
                health.cooling_until = until

    async def complete(  # noqa: PLR0912, PLR0915 - ordered bounded retries and settlement
        self,
        request: ChatRequest,
        budget: Budget,
        *,
        validate: Callable[[str], object] | None = None,
        repair: bool = False,
    ) -> ChatResponse | None:
        # At most two format downgrades per target and one proposal repair
        # across the chain, each separately reserved and settled. Quota/capacity
        # failures move to another eligible target without an immediate retry.
        self.last_attempts = []
        trace_id = uuid.uuid4().hex
        record_event = getattr(budget, "event", None)

        def trace(kind: str, **payload: Any) -> None:
            if callable(record_event):
                record_event(
                    kind,
                    trace_id=trace_id,
                    task_class=request.task_class,
                    purpose=request.schema_name,
                    chain_sha256=self.chain.digest(),
                    **payload,
                )

        targets = list(self.chain.for_task(request.task_class))
        trace("route_started", targets=[target.model_dump() for target in targets])
        active_request = request
        downgraded: set[tuple[str, str]] = set()
        prompt_only: set[tuple[str, str]] = set()
        for index, target in enumerate(targets):
            key = os.getenv(ENVIRONMENT[target.provider])
            if target.provider == "gemini":
                key = os.getenv("GOOGLE_API_KEY") or key
            health = self.health[target.provider]
            model_health = self.model_health[f"{target.provider}/{target.model}"]
            attempt = {"provider": target.provider, "model": target.model}
            if not key or health.should_skip() or model_health.should_skip():
                self.last_attempts.append(
                    {**attempt, "status": "missing_key" if not key else "cooldown"}
                )
                trace("route_skipped", **self.last_attempts[-1])
                continue
            wire_request = active_request.model_copy(
                update={
                    "prompt_json_mode": request.prompt_json_mode
                    or (target.provider, target.model) in prompt_only,
                    "json_object_mode": request.json_object_mode
                    or (target.provider, target.model) in downgraded,
                    "schema_json": json.dumps(
                        wire_schema(
                            json.loads(active_request.schema_json),
                            gemini=target.provider == "gemini",
                        )
                    ),
                }
            )
            try:
                call_id = budget.reserve(target, wire_request)
            except ValueError:
                trace("route_finished", status="reservation_rejected", **attempt)
                raise
            trace("route_attempt", call_id=call_id, attempt=index + 1, **attempt)
            costs = CostLedger()
            cost_id = costs.reserve(category="model", provider=target.provider, model=target.model)
            try:
                response = await asyncio.wait_for(
                    self.transports[target.provider].generate(
                        wire_request,
                        model=target.model,
                        api_key=key,
                        base_url=BASE_URLS[target.provider],
                    ),
                    timeout=request.timeout_seconds,
                )
            except TimeoutError:
                response = ChatResponse(
                    provider=target.provider,
                    model=target.model,
                    input_tokens=None,
                    output_tokens=None,
                    error_class="timeout",
                    error_code="deadline",
                )
            except Exception:
                response = ChatResponse(
                    provider=target.provider,
                    model=target.model,
                    input_tokens=None,
                    output_tokens=None,
                    error_class="transport",
                    error_code="transport_exception",
                )
            costs.settle(
                cost_id,
                status=response.error_class,
                usage={
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "served_model": response.served_model,
                    "elapsed_seconds": response.elapsed_seconds,
                    "usd": None,
                    "pricing_source": "not_configured",
                },
            )
            self.last_attempts.append(
                {
                    **attempt,
                    "status": response.error_class,
                    "error_code": response.error_code or "",
                }
            )
            if response.error_code == "schema_not_supported" and not wire_request.prompt_json_mode:
                # Separately reserve each compatibility attempt. Local validation
                # remains authoritative even when the API cannot enforce JSON.
                budget.settle(call_id, response)
                trace("route_retry", call_id=call_id, reason="schema_not_supported", **attempt)
                if wire_request.json_object_mode:
                    prompt_only.add((target.provider, target.model))
                else:
                    downgraded.add((target.provider, target.model))
                targets.insert(index + 1, target)
                continue
            if response.ok and validate is not None:
                try:
                    validate(response.text or "")
                except ValueError as exc:
                    response = response.model_copy(
                        update={
                            "error_class": "malformed",
                            "error_code": exc.code
                            if isinstance(exc, RepairableResponseError)
                            else "schema_validation",
                        }
                    )
                    if repair and isinstance(exc, RepairableResponseError):
                        trace("route_retry", call_id=call_id, reason=exc.code, **attempt)
                        repair = False
                        active_request = request.model_copy(
                            update={
                                "system": request.system
                                + "\nYour previous proposal was rejected by local validation. "
                                + exc.feedback
                                + " Return a corrected complete JSON object."
                            }
                        )
                        targets.insert(index + 1, target)
            budget.settle(call_id, response)
            self.last_attempts[-1].update(
                status=response.error_class, error_code=response.error_code or ""
            )
            # Credentials and account quota are provider-wide. Capacity, model
            # availability and invalid outputs must not suppress sibling models.
            scope = (
                health
                if response.error_class in {"auth", "no_credential", "quota"}
                else model_health
            )
            scope.record(response.error_class, retry_after_seconds=response.retry_after_seconds)
            trace(
                "route_outcome",
                call_id=call_id,
                status=response.error_class,
                error_code=response.error_code,
                **attempt,
            )
            if response.ok:
                trace("route_finished", status="ok", call_id=call_id, **attempt)
                return response
        trace("route_finished", status="exhausted")
        return None
