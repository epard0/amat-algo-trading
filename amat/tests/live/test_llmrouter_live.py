"""Opt-in live smoke test for llmrouter transports.

Skipped unless ``LLMROUTER_LIVE=1``. This is the only place that exercises the
real provider SDK wire shapes; every unit test in ``tests/unit`` mocks the SDK
clients entirely. Never part of the default gate: excluded from ``testpaths``
collection via ``addopts`` in ``pyproject.toml``.

For each provider whose credential environment variable is set, this lists
models through the real SDK and asserts the configured chain model is present,
then performs one tiny structured generation (``{"x": integer}``) capped at
``max_output_tokens=64``.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from google import genai
from openai import AsyncOpenAI

from llmrouter.contracts import ChatRequest
from llmrouter.transports import BASE_URLS, GeminiTransport, OpenAICompatibleTransport

pytestmark = pytest.mark.skipif(
    os.getenv("LLMROUTER_LIVE") != "1",
    reason="live smoke test: set LLMROUTER_LIVE=1 to run against real providers",
)

_SCHEMA_JSON = json.dumps(
    {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    }
)

# The chain models this smoke test exercises; kept in sync with the strategy's
# configured provider chain (Task 7 router config), not with any single fixed
# release -- update alongside that config when it moves.
_CHAIN_MODELS = {
    "openai": "gpt-5.4-mini",
    "gemini": "gemini-3.6-flash",
    "openrouter": "deepseek/deepseek-v4-flash",
    "nvidia": "nvidia/nemotron-3-super-120b-a12b",
}

_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}


def _request() -> ChatRequest:
    return ChatRequest(
        system="Respond with a JSON object matching the schema. No prose.",
        user='Return {"x": 7}.',
        schema_json=_SCHEMA_JSON,
        max_output_tokens=64,
        timeout_seconds=30,
    )


async def _openai_compatible_model_ids(api_key: str, base_url: str) -> list[str]:
    async with AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=30) as client:
        ids = []
        async for item in await client.models.list():
            ids.append(item.id)
        return ids


async def _gemini_model_names(api_key: str) -> list[str]:
    async with genai.Client(api_key=api_key, vertexai=False).aio as client:
        names = []
        async for item in await client.models.list():
            if item.name:
                names.append(item.name)
        return names


@pytest.mark.parametrize(
    "provider,api_style", [("openai", "responses"), ("openrouter", "chat"), ("nvidia", "chat")]
)
def test_openai_compatible_provider_live(provider: str, api_style: str) -> None:
    key = os.getenv(_ENV_VARS[provider])
    if not key:
        pytest.skip(f"{_ENV_VARS[provider]} not set")
    base_url = BASE_URLS[provider]
    model = _CHAIN_MODELS[provider]

    model_ids = asyncio.run(_openai_compatible_model_ids(key, base_url))
    assert model in model_ids

    response = asyncio.run(
        OpenAICompatibleTransport(api_style).generate(
            _request(), model=model, api_key=key, base_url=base_url
        )
    )
    assert response.ok, response.raw_json
    assert response.text is not None
    json.loads(response.text)


def test_gemini_provider_live() -> None:
    key = os.getenv(_ENV_VARS["gemini"])
    if not key:
        pytest.skip("GEMINI_API_KEY not set")
    model = _CHAIN_MODELS["gemini"]

    model_names = asyncio.run(_gemini_model_names(key))
    assert any(model in name for name in model_names)

    response = asyncio.run(
        GeminiTransport().generate(
            _request(), model=model, api_key=key, base_url=BASE_URLS["gemini"]
        )
    )
    assert response.ok, response.raw_json
    assert response.text is not None
    json.loads(response.text)
