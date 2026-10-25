"""Route correlation, privacy, and interrupted-call diagnostics."""

import asyncio
import json

from autoresearch.ledger import Ledger
from autoresearch.telemetry import monitoring, read_telemetry
from llmrouter.chain import Chain, Router, Target
from llmrouter.contracts import ChatRequest, ChatResponse


def test_failover_trace_correlates_calls_without_prompt_content(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    chain = Chain(
        targets=(
            Target(provider="openrouter", model="first"),
            Target(provider="openrouter", model="second"),
        )
    )

    class Transport:
        async def generate(self, request, *, model, **kwargs):
            return ChatResponse(
                provider="openrouter",
                model=model,
                served_model=model,
                text="{}",
                input_tokens=2,
                output_tokens=3,
                error_class="rate_limit" if model == "first" else "ok",
            )

    with Ledger(tmp_path, 100000) as ledger:
        ledger.phase("engineering", 7)
        request = ChatRequest(
            system="private-system",
            user="private-user",
            schema_json="{}",
            schema_name="patch",
            task_class="coding",
            max_output_tokens=256,
            timeout_seconds=1,
            persistence="metadata_only",
        )
        response = asyncio.run(
            Router(chain, transports={"openrouter": Transport()}).complete(request, ledger)
        )
        assert response.model == "second"
        snapshot = read_telemetry(tmp_path)
        result = monitoring(snapshot, tmp_path)
        timeline = result["call_timeline"]
        assert [call["status"] for call in timeline] == ["rate_limit", "ok"]
        assert [call["attempt"] for call in timeline] == [1, 2]
        assert len({call["trace_id"] for call in timeline}) == 1
        assert timeline[0]["trace_id"]
        assert all(call["task_class"] == "coding" and call["experiment"] == 7 for call in timeline)
        assert sum(model["charged_tokens"] for model in result["models"]) == 10
        for secret in ("private-system", "private-user", "secret-key"):
            assert secret not in json.dumps(result)
            assert secret not in json.dumps(snapshot["events"])


def test_unsettled_call_is_interrupted_after_writer_exits(tmp_path):
    with Ledger(tmp_path, 100000) as ledger:
        ledger.reserve(
            Target(provider="openrouter", model="test"),
            ChatRequest(
                system="private",
                user="private",
                schema_json="{}",
                max_output_tokens=256,
                timeout_seconds=1,
            ),
        )
        assert (
            monitoring(read_telemetry(tmp_path), tmp_path)["call_timeline"][0]["status"]
            == "pending"
        )
    result = monitoring(read_telemetry(tmp_path), tmp_path)
    assert result["call_timeline"][0]["status"] == "interrupted"
    assert result["models"][0]["unknown_usage_calls"] == 1
    assert result["models"][0]["mean_elapsed_seconds"] is None


def test_coding_route_is_independent_and_legacy_chains_are_unchanged():
    routine = Target(provider="gemini", model="routine")
    complex_target = Target(provider="openai", model="judge")
    coding = Target(provider="openrouter", model="stealth/space-bunny-alpha")
    chain = Chain(targets=(routine,), complex_targets=(complex_target,), coding_targets=(coding,))
    assert chain.for_task("coding") == (coding,)
    assert chain.for_task("complex") == (complex_target,)
    assert chain.for_task("routine") == (routine,)
    assert "openrouter/stealth/space-bunny-alpha" in Router(chain).model_health
    legacy = Chain(targets=(routine,))
    assert "coding_targets" not in legacy.model_dump()
    assert legacy.for_task("coding") == (routine,)


def test_coding_falls_back_from_luna_to_gemini(tmp_path, monkeypatch):
    from llmrouter.policy import default_chain

    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(name, "fixture")
    seen = []

    class Transport:
        async def generate(self, request, *, model, **kwargs):
            seen.append(model)
            return ChatResponse(
                provider="openai" if len(seen) == 1 else "gemini",
                model=model,
                text="{}",
                input_tokens=1,
                output_tokens=1,
                error_class="rate_limit" if len(seen) == 1 else "ok",
            )

    router = Router(
        default_chain(), transports={name: Transport() for name in ("openai", "gemini")}
    )
    with Ledger(tmp_path, 100000) as ledger:
        response = asyncio.run(
            router.complete(
                ChatRequest(
                    system="s",
                    user="u",
                    schema_json="{}",
                    max_output_tokens=256,
                    timeout_seconds=1,
                    task_class="coding",
                ),
                ledger,
            )
        )
    assert response.model == "gemini-3.8-flash"
    assert seen == ["gpt-5.6-luna", "gemini-3.8-flash"]
