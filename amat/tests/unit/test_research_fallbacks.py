"""Research recovery keeps sibling models usable and accounting conservative."""

import asyncio

import pytest

from autoresearch.ledger import Ledger
from llmrouter.chain import Chain, Router, Target
from llmrouter.contracts import ChatRequest, ChatResponse


def request():
    return ChatRequest(
        system="Return JSON", user="{}", schema_json="{}", max_output_tokens=256, timeout_seconds=1
    )


@pytest.mark.parametrize("failure", ["rate_limit", "malformed", "timeout", "transport"])
def test_sibling_model_survives_and_cooldown_roundtrips(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-fixture")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    seen = []

    class Transport:
        async def generate(self, req, *, model, api_key, **kwargs):
            assert api_key == "google-fixture"
            seen.append(model)
            return ChatResponse(
                provider="gemini",
                model=model,
                text="{}" if model == "second" else None,
                input_tokens=0,
                output_tokens=0,
                error_class=failure if model == "first" else "ok",
            )

    chain = Chain(targets=tuple(Target(provider="gemini", model=m) for m in ("first", "second")))
    router = Router(chain, transports={"gemini": Transport()})
    with Ledger(tmp_path, 10000) as ledger:
        assert asyncio.run(router.complete(request(), ledger)).model == "second"
    assert seen == ["first", "second"]
    restored = Router(chain, transports={})
    restored.restore_cooldowns(router.cooldowns())
    assert not restored.health["gemini"].should_skip()
    if failure == "rate_limit":
        assert restored.model_health["gemini/first"].should_skip()
    assert not restored.model_health["gemini/second"].should_skip()


def test_three_format_levels_are_bounded_and_rejections_refunded(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    modes = []

    class Transport:
        async def generate(self, req, *, model, **kwargs):
            modes.append((req.json_object_mode, req.prompt_json_mode))
            if not req.prompt_json_mode:
                return ChatResponse(
                    provider="openrouter",
                    model=model,
                    input_tokens=None,
                    output_tokens=None,
                    error_class="transport",
                    error_code="schema_not_supported",
                )
            return ChatResponse(
                provider="openrouter", model=model, text="{}", input_tokens=4, output_tokens=2
            )

    router = Router(
        Chain(targets=(Target(provider="openrouter", model="openrouter/free"),)),
        transports={"openrouter": Transport()},
    )
    with Ledger(tmp_path, 4000) as ledger:
        assert asyncio.run(router.complete(request(), ledger)) is not None
        assert [r[0] for r in ledger.db.execute("SELECT charged FROM calls")] == [0, 0, 6]
    assert modes == [(False, False), (True, False), (True, True)]


@pytest.mark.parametrize(
    "code,refunded",
    [
        ("provider_http_429", True),
        ("provider_http_404", True),
        ("deadline", False),
        ("transport_exception", False),
        ("provider_http_503", False),
    ],
)
def test_only_definite_rejections_release_unknown_usage(tmp_path, code, refunded):
    with Ledger(tmp_path, 10000) as ledger:
        call = ledger.reserve(Target(provider="gemini", model="fixture"), request())
        ledger.settle(
            call,
            ChatResponse(
                provider="gemini",
                model="fixture",
                input_tokens=None,
                output_tokens=None,
                error_class="transport",
                error_code=code,
            ),
        )
        charged = ledger.db.execute("SELECT charged FROM calls").fetchone()[0]
        assert (charged == 0) is refunded
