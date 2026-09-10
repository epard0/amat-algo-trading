"""OpenAI-compatible (Responses / Chat Completions) and Gemini transports.

Provider-neutral counterparts of ``portfolio_team.openai_runtime.OpenAITransport``
and ``portfolio_team.model_runtime.GeminiTransport``: the same wire shapes, refusal
and incompleteness handling, usage arithmetic, and key scrubbing, but ``model``,
``api_key``, and ``base_url`` are supplied by the caller (the router, Task 7)
instead of being read from environment variables or hard-coded per provider.
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal, cast

from google import genai
from google.genai import errors, types
from google.genai._gaos.lib.compat_errors import APIError as InteractionAPIError
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from llmrouter.contracts import ChatRequest, ChatResponse, ErrorClass, classify_http

BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "gemini": "https://generativelanguage.googleapis.com",
}

_MAX_MESSAGE_CHARS = 2000
_HTTP_BAD_REQUEST = 400
_SCHEMA_REJECTION_MARKERS = ("response_format", "json_schema")


def _provider_for(base_url: str) -> str:
    """Reverse-lookup a configured provider name from its base URL."""
    for name, url in BASE_URLS.items():
        if url == base_url:
            return name
    return "custom"


def _scrub(text: str, api_key: str) -> str:
    """Strip a raw API key out of provider-supplied text before it is stored."""
    scrubbed = text.replace(api_key, "[REDACTED]") if api_key else text
    return scrubbed[:_MAX_MESSAGE_CHARS]


def _retry_after(headers: Any) -> float | None:
    """Parse a numeric ``retry-after`` header, if present."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _body_text(exc: APIStatusError) -> str:
    return str(exc.body) if exc.body is not None else str(exc)


def _status_response(
    *, provider: str, model: str, api_key: str, exc: APIStatusError, start: float
) -> ChatResponse:
    body_text = _scrub(_body_text(exc), api_key)
    return ChatResponse(
        provider=provider,
        model=model,
        input_tokens=None,
        output_tokens=None,
        error_class=classify_http(exc.status_code, body_text),
        error_code=f"provider_http_{exc.status_code}",
        retry_after_seconds=_retry_after(exc.response.headers),
        raw_json=json.dumps({"status_code": exc.status_code, "message": body_text}),
        elapsed_seconds=time.monotonic() - start,
    )


def _openai_exception_response(
    exc: Exception, *, provider: str, model: str, api_key: str, start: float
) -> ChatResponse:
    """Classify any exception raised by an OpenAI-compatible SDK call.

    Order matters: ``APITimeoutError`` subclasses ``APIConnectionError``, so it
    must be checked first.
    """
    if isinstance(exc, APITimeoutError):
        return ChatResponse(
            provider=provider,
            model=model,
            input_tokens=None,
            output_tokens=None,
            error_class="timeout",
            error_code="provider_timeout",
            elapsed_seconds=time.monotonic() - start,
        )
    if isinstance(exc, APIStatusError):
        return _status_response(
            provider=provider, model=model, api_key=api_key, exc=exc, start=start
        )
    if isinstance(exc, APIConnectionError):
        return ChatResponse(
            provider=provider,
            model=model,
            input_tokens=None,
            output_tokens=None,
            error_class="transport",
            error_code="provider_connection_error",
            elapsed_seconds=time.monotonic() - start,
        )
    # No exception text is stored here: an arbitrary exception's message can
    # contain request URLs or credentials that never went through `_scrub`.
    return ChatResponse(
        provider=provider,
        model=model,
        input_tokens=None,
        output_tokens=None,
        error_class="transport",
        error_code=f"transport_{type(exc).__name__}",
        elapsed_seconds=time.monotonic() - start,
    )


class OpenAICompatibleTransport:
    """OpenAI SDK transport shared by OpenAI, OpenRouter, and NVIDIA NIM."""

    def __init__(self, api_style: Literal["responses", "chat"]) -> None:
        self.api_style = api_style

    async def generate(
        self, request: ChatRequest, *, model: str, api_key: str, base_url: str
    ) -> ChatResponse:
        if self.api_style == "responses":
            return await _generate_responses(
                request, model=model, api_key=api_key, base_url=base_url
            )
        return await _generate_chat(request, model=model, api_key=api_key, base_url=base_url)


async def _generate_responses(
    request: ChatRequest, *, model: str, api_key: str, base_url: str
) -> ChatResponse:
    provider = _provider_for(base_url)
    start = time.monotonic()
    schema = json.loads(request.schema_json)
    try:
        async with AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=0, timeout=request.timeout_seconds
        ) as client:
            if request.reasoning_effort:
                response = await client.responses.create(
                    model=model,
                    instructions=request.system,
                    input=request.user,
                    max_output_tokens=request.max_output_tokens,
                    store=False,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": request.schema_name,
                            "strict": True,
                            "schema": schema,
                        }
                    },
                    # ChatRequest.reasoning_effort is a plain `str` (validated by the
                    # router/provider config, not this transport); the SDK's `Reasoning`
                    # TypedDict requires a specific Literal, so this one field is the
                    # transport's SDK-boundary `Any` escape hatch.
                    reasoning=cast(Any, {"effort": request.reasoning_effort}),
                )
            else:
                response = await client.responses.create(
                    model=model,
                    instructions=request.system,
                    input=request.user,
                    max_output_tokens=request.max_output_tokens,
                    store=False,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": request.schema_name,
                            "strict": True,
                            "schema": schema,
                        }
                    },
                )
    except Exception as exc:  # classified by _openai_exception_response
        return _openai_exception_response(
            exc, provider=provider, model=model, api_key=api_key, start=start
        )
    return _finalize_responses(provider=provider, model=model, response=response, start=start)


def _finalize_responses(*, provider: str, model: str, response: Any, start: float) -> ChatResponse:
    data = response.model_dump(mode="json", exclude_none=True)
    refusal = any(
        part.get("type") == "refusal"
        for item in data.get("output", [])
        for part in item.get("content", [])
    )
    usage = response.usage
    error_class: ErrorClass = "ok"
    error_code: str | None = None
    if refusal:
        error_class = "refusal"
        error_code = "provider_refusal"
    elif response.status != "completed":
        error_class = "malformed"
        error_code = "provider_incomplete_response"
    return ChatResponse(
        provider=provider,
        model=model,
        text=response.output_text,
        raw_json=json.dumps(data, sort_keys=True),
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        error_class=error_class,
        error_code=error_code,
        elapsed_seconds=time.monotonic() - start,
    )


def _schema_rejected(exc: APIStatusError) -> bool:
    body_text = _body_text(exc).lower()
    return any(marker in body_text for marker in _SCHEMA_REJECTION_MARKERS)


async def _generate_chat(
    request: ChatRequest, *, model: str, api_key: str, base_url: str
) -> ChatResponse:
    provider = _provider_for(base_url)
    start = time.monotonic()
    schema = json.loads(request.schema_json)
    downgraded = False
    try:
        async with AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=0, timeout=request.timeout_seconds
        ) as client:
            try:
                completion = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": request.system},
                        {"role": "user", "content": request.user},
                    ],
                    max_tokens=request.max_output_tokens,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": request.schema_name,
                            "strict": True,
                            "schema": schema,
                        },
                    },
                )
            except APIStatusError as exc:
                if exc.status_code != _HTTP_BAD_REQUEST or not _schema_rejected(exc):
                    raise
                downgraded = True
                downgrade_system = (
                    request.system
                    + "\nRespond with a JSON object matching this schema: "
                    + json.dumps(schema)
                )
                completion = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": downgrade_system},
                        {"role": "user", "content": request.user},
                    ],
                    max_tokens=request.max_output_tokens,
                    response_format={"type": "json_object"},
                )
    except Exception as exc:  # classified by _openai_exception_response
        return _openai_exception_response(
            exc, provider=provider, model=model, api_key=api_key, start=start
        )
    return _finalize_chat(
        provider=provider, model=model, completion=completion, downgraded=downgraded, start=start
    )


def _chat_usage(*, provider: str, usage: Any) -> tuple[int | None, int | None]:
    if usage is None:
        return None, None
    input_tokens = usage.prompt_tokens
    output_tokens = usage.completion_tokens
    details = usage.completion_tokens_details
    if provider == "nvidia" and details is not None and details.reasoning_tokens is not None:
        # OpenRouter already folds reasoning tokens into completion_tokens;
        # NVIDIA NIM reports them separately and they must be added back in.
        output_tokens += details.reasoning_tokens
    return input_tokens, output_tokens


def _finalize_chat(
    *, provider: str, model: str, completion: Any, downgraded: bool, start: float
) -> ChatResponse:
    choice = completion.choices[0]
    text = choice.message.content
    input_tokens, output_tokens = _chat_usage(provider=provider, usage=completion.usage)
    data = completion.model_dump(mode="json", exclude_none=True)
    error_class: ErrorClass = "ok"
    error_code: str | None = None
    if downgraded:
        data["schema_downgraded"] = True
        try:
            json.loads(text or "")
        except json.JSONDecodeError:
            error_class = "malformed"
            error_code = "provider_malformed_response"
    if choice.finish_reason == "length":
        error_class = "malformed"
        error_code = "provider_incomplete_response"
    return ChatResponse(
        provider=provider,
        model=model,
        text=text,
        raw_json=json.dumps(data, sort_keys=True),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error_class=error_class,
        error_code=error_code,
        elapsed_seconds=time.monotonic() - start,
    )


class GeminiTransport:
    """One Interactions API call, fixed host, no implicit SDK retries."""

    async def generate(
        self, request: ChatRequest, *, model: str, api_key: str, base_url: str
    ) -> ChatResponse:
        provider = _provider_for(base_url)
        start = time.monotonic()
        try:
            async with genai.Client(
                api_key=api_key,
                vertexai=False,
                http_options=types.HttpOptions(
                    base_url=base_url,
                    timeout=int(request.timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=0),
                ),
            ).aio as client:
                response: Any = await client.interactions.create(
                    model=model,
                    input=request.user,
                    system_instruction=request.system,
                    store=False,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": json.loads(request.schema_json),
                    },
                    generation_config={
                        "max_output_tokens": request.max_output_tokens,
                        "thinking_level": "minimal",
                    },
                    timeout=request.timeout_seconds,
                )
        except (errors.APIError, InteractionAPIError) as exc:
            return _gemini_status_response(
                exc, provider=provider, model=model, api_key=api_key, start=start
            )
        except Exception as exc:  # no exception text is ever stored here
            return ChatResponse(
                provider=provider,
                model=model,
                input_tokens=None,
                output_tokens=None,
                error_class="transport",
                error_code=f"transport_{type(exc).__name__}",
                elapsed_seconds=time.monotonic() - start,
            )
        return _finalize_gemini(provider=provider, model=model, response=response, start=start)


def _finalize_gemini(*, provider: str, model: str, response: Any, start: float) -> ChatResponse:
    data = response.model_dump(mode="json", exclude_none=True)
    usage = data.get("usage") or {}
    output = usage.get("total_output_tokens")
    if usage.get("total_tokens") is not None and usage.get("total_input_tokens") is not None:
        # Use total-minus-input to include reasoning without double counting
        # providers that already include thoughts in output tokens.
        output = max(output or 0, usage["total_tokens"] - usage["total_input_tokens"])
    elif output is not None:
        output += usage.get("total_thought_tokens") or 0
    return ChatResponse(
        provider=provider,
        model=model,
        text=response.output_text,
        raw_json=json.dumps(data, sort_keys=True),
        input_tokens=usage.get("total_input_tokens"),
        output_tokens=output,
        elapsed_seconds=time.monotonic() - start,
    )


def _gemini_status_response(
    exc: errors.APIError | InteractionAPIError,
    *,
    provider: str,
    model: str,
    api_key: str,
    start: float,
) -> ChatResponse:
    code = int(getattr(exc, "code", None) or getattr(exc, "status_code", None) or 0)
    message = _scrub(str(getattr(exc, "message", "") or str(exc)), api_key)
    return ChatResponse(
        provider=provider,
        model=model,
        input_tokens=None,
        output_tokens=None,
        error_class=classify_http(code, message),
        error_code=f"provider_http_{code}",
        raw_json=json.dumps({"status_code": code, "message": message}),
        elapsed_seconds=time.monotonic() - start,
    )
