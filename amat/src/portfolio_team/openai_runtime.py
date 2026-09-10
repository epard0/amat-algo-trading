"""OpenAI Responses adapter and explicit chief/reviewer routing. No execution tools."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from pydantic import Field

from portfolio_team.contracts import Assessment, Bundle, Role
from portfolio_team.evidence import sha256
from portfolio_team.model_runtime import (
    SYSTEM_RULES,
    ModelRequest,
    ModelResponse,
    ModelSettings,
    RemoteProvider,
    Transport,
)
from portfolio_team.providers import PROMPTS

CHIEF_INSTRUCTIONS = (
    " Act as head of the research team. Resolve disagreements using the supplied evidence. "
    "Order evidence requests by decision value, most important first. For each request specify "
    "the responsible research specialty, the bounded experiment or artifact needed, and measurable "
    "acceptance criteria. Distinguish research rejection from operational unreadiness. "
    "Do not claim to have run experiments, read news, inspected the broker, or assigned executable "
    "jobs. Your requests are a proposed research backlog, not commands or trading mandates."
)


class OpenAISettings(ModelSettings):
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "high"
    max_output_tokens: int = Field(default=8192, ge=256, le=8192)


class OpenAIRequest(ModelRequest):
    settings: OpenAISettings


class OpenAITransport:
    async def generate(self, request: ModelRequest) -> ModelResponse:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            return ModelResponse(error_code="missing_openai_credential")
        if not isinstance(request, OpenAIRequest):
            return ModelResponse(error_code="invalid_openai_request")
        try:
            async with AsyncOpenAI(
                api_key=key,
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=request.settings.request_timeout_seconds,
            ) as client:
                response = await client.responses.create(
                    model=request.settings.model,
                    instructions=request.system_instruction,
                    input=request.contents,
                    reasoning={"effort": request.settings.reasoning_effort},
                    max_output_tokens=request.settings.max_output_tokens,
                    store=False,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "research_assessment",
                            "strict": True,
                            "schema": json.loads(request.output_schema_json),
                        }
                    },
                )
            data = response.model_dump(mode="json", exclude_none=True)
            refusal = any(
                part.get("type") == "refusal"
                for item in data.get("output", [])
                for part in item.get("content", [])
            )
            error = None
            if refusal:
                error = "provider_refusal"
            elif response.status != "completed":
                error = "provider_incomplete_response"
            return ModelResponse(
                text=response.output_text,
                raw_json=json.dumps(data, sort_keys=True),
                input_tokens=response.usage.input_tokens if response.usage else None,
                # Responses output_tokens already includes reasoning tokens.
                output_tokens=response.usage.output_tokens if response.usage else None,
                error_code=error,
            )
        except APIStatusError as exc:
            return ModelResponse(
                error_code=f"provider_http_{exc.status_code}",
                retryable=exc.status_code in (429, 500, 502, 503, 504),
                raw_json=json.dumps({"status_code": exc.status_code}),
            )
        except APIConnectionError:
            return ModelResponse(error_code="provider_connection_error", retryable=True)
        except Exception as exc:
            return ModelResponse(error_code=f"transport_{type(exc).__name__}")


class OpenAIProvider(RemoteProvider):
    request_type = OpenAIRequest

    def __init__(self, settings: OpenAISettings, transport: Transport | None = None) -> None:
        super().__init__(settings, transport or OpenAITransport())
        self.id = f"openai/{settings.model}/{settings.digest()}"

    def assessment_schema(self) -> dict[str, Any]:
        return Assessment.model_json_schema()

    def prepare(
        self, role: Role, bundle: Bundle, previous: tuple[Assessment, ...] = ()
    ) -> ModelRequest:
        request = super().prepare(role, bundle, previous)
        if role == "chief_pm":
            request = request.model_copy(
                update={"system_instruction": request.system_instruction + CHIEF_INSTRUCTIONS}
            )
        return request

    def protocol(self) -> bytes:
        return json.dumps(
            {
                "protocol": "openai-research-committee/1",
                "api": "responses",
                "store": False,
                "sdk_retries": 0,
                "rules": SYSTEM_RULES,
                "prompts": PROMPTS,
                "chief_instructions": CHIEF_INSTRUCTIONS,
                "schema": self.assessment_schema(),
                "identity_constraints": "singleton role and evidence SHA256 enums",
            },
            sort_keys=True,
        ).encode()


class ReviewTeam(RemoteProvider):
    """Same bounded coordinator, separate specialist and chief model configurations."""

    def __init__(self, reviewers: RemoteProvider, chief: OpenAIProvider) -> None:
        self.reviewers = reviewers
        self.chief = chief
        self.settings = reviewers.settings
        self.id = f"team/{sha256(self.protocol())}"

    def protocol(self) -> bytes:
        return json.dumps(
            {
                "protocol": "role-routed-research-team/1",
                "reviewers": self.reviewers.id,
                "chief": self.chief.id,
                "reviewer_protocol": json.loads(self.reviewers.protocol()),
                "chief_protocol": json.loads(self.chief.protocol()),
            },
            sort_keys=True,
        ).encode()

    def prepare(
        self, role: Role, bundle: Bundle, previous: tuple[Assessment, ...] = ()
    ) -> ModelRequest:
        provider = self.chief if role == "chief_pm" else self.reviewers
        return provider.prepare(role, bundle, previous)

    async def invoke(self, request: ModelRequest, remaining_seconds: float) -> ModelResponse:
        provider = self.chief if request.role == "chief_pm" else self.reviewers
        return await provider.invoke(request, remaining_seconds)
