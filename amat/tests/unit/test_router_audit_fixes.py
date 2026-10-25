import asyncio
import json

from autoresearch.ledger import Ledger
from llmrouter.chain import Chain, Router, Target
from llmrouter.contracts import ChatRequest, ChatResponse


def test_schema_rejection_has_two_reservations_and_releases_rejected_charge(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")

    class Transport:
        async def generate(self, request, **_kwargs):
            if not request.json_object_mode:
                return ChatResponse(
                    provider="openrouter",
                    model="free",
                    input_tokens=None,
                    output_tokens=None,
                    error_class="transport",
                    error_code="schema_not_supported",
                )
            return ChatResponse(
                provider="openrouter",
                model="free",
                served_model="actual/model",
                input_tokens=5,
                output_tokens=3,
                text="{}",
            )

    router = Router(
        Chain(targets=(Target(provider="openrouter", model="free"),)),
        transports={"openrouter": Transport()},
    )
    request = ChatRequest(
        system="Return JSON", user="{}", schema_json="{}", max_output_tokens=256, timeout_seconds=1
    )
    with Ledger(tmp_path, 100000) as ledger:
        response = asyncio.run(router.complete(request, ledger))
        assert response.served_model == "actual/model"
        calls = ledger.db.execute("SELECT request, charged FROM calls ORDER BY id").fetchall()
        assert len(calls) == 2
        assert calls[0][1] == 0
        assert calls[1][1] == 8
        assert json.loads(calls[1][0])["json_object_mode"]


def test_metadata_only_never_persists_prompt_response_or_provider_echo(tmp_path):
    marker = "RAW_PAGE_DO_NOT_PERSIST_abcdef"
    request = ChatRequest(
        system="Return JSON",
        user=marker,
        schema_json="{}",
        max_output_tokens=256,
        timeout_seconds=1,
        persistence="metadata_only",
    )
    with Ledger(tmp_path, 100000) as ledger:
        call = ledger.reserve(Target(provider="openrouter", model="free"), request)
        ledger.settle(
            call,
            ChatResponse(
                provider="openrouter",
                model="free",
                text=marker,
                raw_json=json.dumps({"echo": marker}),
                input_tokens=2,
                output_tokens=None,
            ),
        )
        rows = ledger.db.execute("SELECT request,response,charged FROM calls").fetchall()
        assert marker not in str(rows)
        assert rows[0][2] > 256
    assert marker.encode() not in (tmp_path / "research.sqlite3").read_bytes()


def test_legacy_request_canonical_excludes_new_default_fields():
    request = ChatRequest(
        system="s", user="u", schema_json="{}", max_output_tokens=256, timeout_seconds=1
    )
    assert "persistence" not in request.canonical()
    assert "json_object_mode" not in request.canonical()
