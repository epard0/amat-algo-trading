"""Shared llmrouter transports under the committee's per-attempt accounting."""

from __future__ import annotations

import json
import os
from typing import Any

from llmrouter.chain import ENVIRONMENT, wire_schema
from llmrouter.contracts import ChatRequest, ProviderName
from llmrouter.costs import CostLedger
from llmrouter.policy import POLICY_VERSION, default_chain
from llmrouter.transports import BASE_URLS, GeminiTransport, OpenAICompatibleTransport
from portfolio_team.contracts import Assessment, Bundle, Role
from portfolio_team.model_runtime import ModelRequest, ModelResponse, ModelSettings, RemoteProvider


class RoutedRequest(ModelRequest):
    route_provider: ProviderName


class RoutedTransport:
    async def generate(self, request: ModelRequest) -> ModelResponse:
        if not isinstance(request, RoutedRequest):
            return ModelResponse(error_code="invalid_routed_request")
        provider = request.route_provider
        key = os.getenv(ENVIRONMENT[provider])
        if not key:
            return ModelResponse(error_code="missing_route_credential", retryable=True)
        chat = ChatRequest(
            system=request.system_instruction,
            user=request.contents,
            schema_json=json.dumps(
                wire_schema(json.loads(request.output_schema_json), gemini=provider == "gemini")
            ),
            max_output_tokens=request.settings.max_output_tokens,
            timeout_seconds=request.settings.request_timeout_seconds,
            task_class="complex" if request.role == "chief_pm" else "routine",
            reasoning_effort="high" if provider == "openai" else None,
            # Compatible utility models vary in strict-schema support. Local
            # Assessment/reference validation is always enforced by coordinator.
            json_object_mode=provider in {"groq", "openrouter"},
            persistence="metadata_only",
        )
        transport = (
            GeminiTransport()
            if provider == "gemini"
            else OpenAICompatibleTransport("responses" if provider == "openai" else "chat")
        )
        costs = CostLedger()
        cost_id = costs.reserve(category="model", provider=provider, model=request.settings.model)
        response = await transport.generate(
            chat, model=request.settings.model, api_key=key, base_url=BASE_URLS[provider]
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
        return ModelResponse(
            text=response.text,
            raw_json=json.dumps(
                {
                    "provider": provider,
                    "requested_model": response.model,
                    "served_model": response.served_model,
                    "error_class": response.error_class,
                },
                sort_keys=True,
            ),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            error_code=None if response.ok else response.error_code or response.error_class,
            retryable=not response.ok,
        )


class RoutedProvider(RemoteProvider):
    """Each fallback is a separate durable coordinator reservation, never a hidden retry."""

    def __init__(self, settings: ModelSettings) -> None:
        super().__init__(settings, transport=RoutedTransport())
        self.chain = default_chain()
        self.id = f"router/{POLICY_VERSION}/{self.chain.digest()}/{settings.digest()}"

    def protocol(self) -> bytes:
        return super().protocol() + self.chain.canonical().encode() + POLICY_VERSION.encode()

    def assessment_schema(self) -> dict[str, Any]:
        return Assessment.model_json_schema()

    def prepare(
        self, role: Role, bundle: Bundle, previous: tuple[Assessment, ...] = ()
    ) -> ModelRequest:
        return self.prepare_attempt(role, bundle, previous, 1)

    def prepare_attempt(
        self, role: Role, bundle: Bundle, previous: tuple[Assessment, ...], attempt: int
    ) -> ModelRequest:
        request = super().prepare(role, bundle, previous)
        targets = self.chain.for_task("complex" if role == "chief_pm" else "routine")
        available = tuple(t for t in targets if os.getenv(ENVIRONMENT[t.provider])) or targets
        target = available[min(attempt - 1, len(available) - 1)]
        return RoutedRequest(
            **request.model_dump(exclude={"settings"}),
            settings=self.settings.model_copy(update={"model": target.model}),
            route_provider=target.provider,
        )
