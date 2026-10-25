"""Default research routing avoids OpenAI and paid OpenRouter fallbacks."""

import asyncio
from pathlib import Path

import pytest

from autoresearch.ledger import Ledger
from llmrouter.chain import ENVIRONMENT, Chain, Router
from llmrouter.contracts import ChatRequest, ChatResponse

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("successful_provider", ["groq", "openrouter", "gemini", None])
@pytest.mark.parametrize("unavailable", ["rate_limit", "missing_key"])
def test_default_chain_priority_and_exhaustion(
    tmp_path, monkeypatch, successful_provider, unavailable
):
    chain = Chain.load(ROOT / "configs/autoresearch/llm-chain.example.toml")
    assert [target.provider for target in chain.targets] == [
        "gemini",
        "gemini",
        "gemini",
        "openrouter",
        "openrouter",
        "openrouter",
        "groq",
    ]
    assert chain.for_task("complex") == chain.for_task("coding")
    assert chain.for_task("coding")[0].model == "gpt-5.6-luna"
    assert chain.for_task("coding")[1].model == "gemini-3.8-flash"
    assert all(
        t.model in {"openrouter/free", "stealth/space-bunny-alpha"} or t.model.endswith(":free")
        for t in chain.targets
        if t.provider == "openrouter"
    )
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    attempted = []

    class Transport:
        def __init__(self, provider):
            self.provider = provider

        async def generate(self, request, *, model, api_key, base_url):
            attempted.append(self.provider)
            assert self.provider != "openai"
            return ChatResponse(
                provider=self.provider,
                model=model,
                text="{}",
                input_tokens=10,
                output_tokens=5,
                error_class="ok" if self.provider == successful_provider else "rate_limit",
            )

    for provider, key in ENVIRONMENT.items():
        if unavailable == "missing_key" and provider not in (successful_provider, "openai"):
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, "fixture")
    router = Router(chain, transports={p: Transport(p) for p in ENVIRONMENT})
    request = ChatRequest(
        system="Return JSON",
        user="{}",
        schema_json="{}",
        max_output_tokens=256,
        timeout_seconds=1,
    )
    with Ledger(tmp_path, 100000) as ledger:
        response = asyncio.run(router.complete(request, ledger))
        assert (response.provider if response else None) == successful_provider
        assert ledger.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == len(attempted)
    order = [t.provider for t in chain.targets]
    expected = order[: order.index(successful_provider) + 1] if successful_provider else order
    if unavailable == "missing_key":
        expected = [successful_provider] if successful_provider else []
    assert attempted == expected


def test_simple_start_uses_same_default_chain_for_proposals_and_reviews(tmp_path, monkeypatch):
    from autoresearch.cli import main

    captured = {}

    def initialize(root, *_args, **options):
        root.mkdir(parents=True)
        captured["summaries"] = options["summary_chain"]

    def run_loop(root, _iterations, built_in):
        captured["proposals"] = None if built_in else captured["summaries"]
        (root / "report.html").write_text("report")
        return {
            "status": "batch_completed",
            "batch": {"requested": 1, "completed": 1, "escalated": 0},
        }

    monkeypatch.setattr("autoresearch.cli.active_promotion", lambda _directory: None)
    monkeypatch.setattr("autoresearch.cli.latest_path", lambda _source: tmp_path / "latest.json")
    monkeypatch.setattr("autoresearch.cli.initialize", initialize)
    monkeypatch.setattr("autoresearch.cli.run_frozen_start", run_loop)
    assert (
        main(
            [
                "start",
                "1",
                "--source",
                str(ROOT),
                "--workspace",
                str(tmp_path / "run"),
                "--no-open",
            ]
        )
        == 0
    )
    expected = Chain.load(ROOT / "configs/autoresearch/llm-chain.example.toml")
    assert captured == {"summaries": expected, "proposals": expected}


def test_builtin_start_does_not_load_or_freeze_a_model_chain(tmp_path, monkeypatch):
    from autoresearch.cli import main

    captured = {}

    def initialize(root, *_args, **options):
        root.mkdir(parents=True)
        captured["chain"] = options["summary_chain"]

    def frozen(root, _iterations, built_in):
        assert built_in
        (root / "report.html").write_text("offline report")
        return {
            "status": "batch_completed",
            "batch": {"requested": 1, "completed": 1, "escalated": 0},
        }

    def forbid_load(*_args):
        raise AssertionError("Built-in research must not load a model chain")

    monkeypatch.setattr("autoresearch.cli.Chain.load", forbid_load)
    monkeypatch.setattr("autoresearch.cli.active_promotion", lambda _directory: None)
    monkeypatch.setattr("autoresearch.cli.latest_path", lambda _source: tmp_path / "latest.json")
    monkeypatch.setattr("autoresearch.cli.initialize", initialize)
    monkeypatch.setattr("autoresearch.cli.run_frozen_start", frozen)
    assert (
        main(
            [
                "start",
                "1",
                "--source",
                str(ROOT),
                "--workspace",
                str(tmp_path / "offline"),
                "--built-in",
                "--chain",
                str(tmp_path / "missing.toml"),
                "--no-open",
            ]
        )
        == 0
    )
    assert captured["chain"] is None
