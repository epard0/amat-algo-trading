"""No-network acceptance for the OpenAI-compatible and Gemini transports."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
from openai import APIConnectionError, APIStatusError

from llmrouter.contracts import ChatRequest
from llmrouter.transports import BASE_URLS, GeminiTransport, OpenAICompatibleTransport


def _request(**overrides: Any) -> ChatRequest:
    payload: dict[str, Any] = {
        "system": "rules",
        "user": "{}",
        "schema_json": json.dumps(
            {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
                "additionalProperties": False,
            }
        ),
        "max_output_tokens": 512,
        "timeout_seconds": 30,
    }
    payload.update(overrides)
    return ChatRequest(**payload)


def _fake_client(*, chat: Any = None, responses: Any = None) -> MagicMock:
    namespace_kwargs: dict[str, Any] = {}
    if chat is not None:
        namespace_kwargs["chat"] = SimpleNamespace(completions=chat)
    if responses is not None:
        namespace_kwargs["responses"] = responses
    context = AsyncMock()
    context.__aenter__.return_value = SimpleNamespace(**namespace_kwargs)
    return MagicMock(return_value=context)


def _completion(
    text: str,
    *,
    prompt: int,
    completion: int,
    finish: str,
    reasoning_tokens: int | None = None,
) -> MagicMock:
    response = MagicMock()
    response.choices = [
        SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)
    ]
    details = (
        SimpleNamespace(reasoning_tokens=reasoning_tokens) if reasoning_tokens is not None else None
    )
    response.usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, completion_tokens_details=details
    )
    response.model_dump.return_value = {
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }
    return response


def _responses_result(
    *, status: str, output_text: str, refusal: bool, input_tokens: int = 10, output_tokens: int = 5
) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.output_text = output_text
    response.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    content_type = "refusal" if refusal else "output_text"
    response.model_dump.return_value = {
        "status": status,
        "output": [{"content": [{"type": content_type}]}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    return response


def _status_error(status: int, body: str, retry_after: str | None = None) -> APIStatusError:
    headers = httpx.Headers({"retry-after": retry_after} if retry_after else {})
    request = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(status, headers=headers, request=request, content=body.encode())
    return APIStatusError(body, response=response, body=body)


def test_chat_style_sends_json_schema_and_reads_usage(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Completions:
        async def create(self, **kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return _completion('{"x": 1}', prompt=10, completion=5, finish="stop")

    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = asyncio.run(
        OpenAICompatibleTransport("chat").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["nvidia"]
        )
    )
    assert response.ok
    assert response.text == '{"x": 1}'
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["messages"][0]["role"] == "system"


def test_chat_style_downgrades_to_json_object_when_schema_is_rejected(monkeypatch: Any) -> None:
    calls: list[str] = []

    class _Completions:
        async def create(self, **kwargs: Any) -> MagicMock:
            calls.append(kwargs["response_format"]["type"])
            if kwargs["response_format"]["type"] == "json_schema":
                body = '{"error":{"message":"response_format json_schema not supported"}}'
                raise _status_error(400, body)
            return _completion('{"x": 2}', prompt=1, completion=1, finish="stop")

    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = asyncio.run(
        OpenAICompatibleTransport("chat").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["openrouter"]
        )
    )
    assert calls == ["json_schema", "json_object"]
    assert response.ok
    assert json.loads(response.raw_json)["schema_downgraded"] is True


def test_status_errors_are_classified_and_scrubbed(monkeypatch: Any) -> None:
    class _Completions:
        async def create(self, **kwargs: Any) -> MagicMock:
            body = '{"error":{"type":"insufficient_quota","message":"key k leaked"}}'
            raise _status_error(429, body, retry_after="17")

    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = asyncio.run(
        OpenAICompatibleTransport("chat").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["openrouter"]
        )
    )
    assert response.error_class == "quota"
    assert response.retry_after_seconds == 17
    assert "k leaked" not in response.raw_json
    assert "[REDACTED]" in response.raw_json


def test_chat_style_finish_reason_length_is_malformed(monkeypatch: Any) -> None:
    class _Completions:
        async def create(self, **kwargs: Any) -> MagicMock:
            return _completion('{"x": 1}', prompt=1, completion=1, finish="length")

    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = asyncio.run(
        OpenAICompatibleTransport("chat").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["openrouter"]
        )
    )
    assert response.error_class == "malformed"
    assert response.error_code == "provider_incomplete_response"


def test_chat_style_nvidia_adds_reasoning_tokens_to_output(monkeypatch: Any) -> None:
    class _Completions:
        async def create(self, **kwargs: Any) -> MagicMock:
            return _completion(
                '{"x": 1}', prompt=10, completion=5, finish="stop", reasoning_tokens=7
            )

    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = asyncio.run(
        OpenAICompatibleTransport("chat").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["nvidia"]
        )
    )
    assert response.output_tokens == 12


def test_chat_style_openrouter_does_not_double_count_reasoning_tokens(monkeypatch: Any) -> None:
    class _Completions:
        async def create(self, **kwargs: Any) -> MagicMock:
            return _completion(
                '{"x": 1}', prompt=10, completion=5, finish="stop", reasoning_tokens=7
            )

    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = asyncio.run(
        OpenAICompatibleTransport("chat").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["openrouter"]
        )
    )
    assert response.output_tokens == 5


def test_chat_style_connection_error_is_transport(monkeypatch: Any) -> None:
    class _Completions:
        async def create(self, **kwargs: Any) -> MagicMock:
            raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))

    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = asyncio.run(
        OpenAICompatibleTransport("chat").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["openrouter"]
        )
    )
    assert response.error_class == "transport"
    assert response.error_code == "provider_connection_error"


def test_responses_style_refusal_and_incomplete(monkeypatch: Any) -> None:
    class _Responses:
        def __init__(self, result: MagicMock) -> None:
            self._result = result

        async def create(self, **kwargs: Any) -> MagicMock:
            return self._result

    refusal_result = _responses_result(status="completed", output_text="", refusal=True)
    monkeypatch.setattr(
        "llmrouter.transports.AsyncOpenAI", _fake_client(responses=_Responses(refusal_result))
    )
    refusal_response = asyncio.run(
        OpenAICompatibleTransport("responses").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["openai"]
        )
    )
    assert refusal_response.error_class == "refusal"
    assert refusal_response.error_code == "provider_refusal"

    incomplete_result = _responses_result(status="incomplete", output_text="{}", refusal=False)
    monkeypatch.setattr(
        "llmrouter.transports.AsyncOpenAI", _fake_client(responses=_Responses(incomplete_result))
    )
    incomplete_response = asyncio.run(
        OpenAICompatibleTransport("responses").generate(
            _request(), model="m", api_key="k", base_url=BASE_URLS["openai"]
        )
    )
    assert incomplete_response.error_class == "malformed"
    assert incomplete_response.error_code == "provider_incomplete_response"


def test_responses_style_completed_is_ok_and_sends_reasoning_effort(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Responses:
        async def create(self, **kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return _responses_result(status="completed", output_text='{"x": 1}', refusal=False)

    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(responses=_Responses()))
    response = asyncio.run(
        OpenAICompatibleTransport("responses").generate(
            _request(reasoning_effort="low"), model="m", api_key="k", base_url=BASE_URLS["openai"]
        )
    )
    assert response.ok
    assert response.provider == "openai"
    assert captured["reasoning"] == {"effort": "low"}
    assert captured["text"]["format"]["strict"] is True


def test_gemini_transport_success_reads_usage(monkeypatch: Any) -> None:
    usage = {"total_input_tokens": 100, "total_output_tokens": 30, "total_thought_tokens": 10}
    response = MagicMock()
    response.model_dump.return_value = {"usage": usage}
    response.output_text = '{"x": 1}'
    create = AsyncMock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = SimpleNamespace(interactions=SimpleNamespace(create=create))
    factory = MagicMock(return_value=SimpleNamespace(aio=context))
    monkeypatch.setattr("llmrouter.transports.genai.Client", factory)

    result = asyncio.run(
        GeminiTransport().generate(
            _request(), model="gemini-test", api_key="gemini-secret", base_url=BASE_URLS["gemini"]
        )
    )

    assert result.ok
    assert result.provider == "gemini"
    assert result.text == '{"x": 1}'
    assert result.input_tokens == 100
    assert result.output_tokens == 40
    assert "gemini-secret" not in result.canonical()
    options = factory.call_args.kwargs["http_options"]
    assert options.base_url == BASE_URLS["gemini"]
    assert options.retry_options.attempts == 0
    args = create.call_args.kwargs
    assert args["store"] is False
    assert args["model"] == "gemini-test"
    assert args["response_format"]["schema"] == json.loads(_request().schema_json)
    create.assert_awaited_once()


def test_gemini_transport_classifies_api_error_and_scrubs(monkeypatch: Any) -> None:
    from google.genai import errors

    factory = MagicMock(side_effect=errors.ClientError(401, {"message": "denied gemini-secret"}))
    monkeypatch.setattr("llmrouter.transports.genai.Client", factory)
    response = asyncio.run(
        GeminiTransport().generate(
            _request(), model="gemini-test", api_key="gemini-secret", base_url=BASE_URLS["gemini"]
        )
    )
    assert response.error_class == "auth"
    assert response.error_code == "provider_http_401"
    assert "gemini-secret" not in response.canonical()
    assert "[REDACTED]" in response.raw_json


def test_gemini_transport_classifies_interaction_api_error(monkeypatch: Any) -> None:
    from google.genai._gaos.lib.compat_errors import RateLimitError

    error = RateLimitError(
        "limited gemini-secret",
        response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid")),
        body=None,
    )
    monkeypatch.setattr("llmrouter.transports.genai.Client", MagicMock(side_effect=error))
    response = asyncio.run(
        GeminiTransport().generate(
            _request(), model="gemini-test", api_key="gemini-secret", base_url=BASE_URLS["gemini"]
        )
    )
    assert response.error_class == "rate_limit"
    assert response.error_code == "provider_http_429"
    assert "gemini-secret" not in response.canonical()


def test_gemini_transport_other_exception_is_transport(monkeypatch: Any) -> None:
    factory = MagicMock(side_effect=RuntimeError("boom gemini-secret"))
    monkeypatch.setattr("llmrouter.transports.genai.Client", factory)
    response = asyncio.run(
        GeminiTransport().generate(
            _request(), model="gemini-test", api_key="gemini-secret", base_url=BASE_URLS["gemini"]
        )
    )
    assert response.error_class == "transport"
    assert response.error_code == "transport_RuntimeError"
    assert "boom" not in response.canonical()
