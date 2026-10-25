"""Shared routing and durable per-attempt accounting."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_portfolio_team import setup as evidence_fixture

from autoresearch.ledger import Ledger
from llmrouter.chain import ENVIRONMENT, Chain, Router
from llmrouter.contracts import ChatRequest, ChatResponse
from llmrouter.policy import default_chain
from portfolio_team.contracts import Policy
from portfolio_team.coordinator import run_review
from portfolio_team.evidence import make_bundle
from portfolio_team.model_runtime import ModelResponse, ModelSettings
from portfolio_team.providers import FakeProvider
from portfolio_team.routing import RoutedProvider, RoutedTransport
from portfolio_team.store import Store


@pytest.fixture
def setup(tmp_path):
    return evidence_fixture.__wrapped__(tmp_path)


def test_research_policy_has_its_own_role_fallbacks():
    root = Path(__file__).resolve().parents[2]
    research = Chain.load(root / "configs/autoresearch/llm-chain.example.toml")
    assert research.targets[0].provider == "gemini"
    assert [t.model for t in research.for_task("complex")][:2] == [
        "gpt-5.6-luna",
        "gemini-3.8-flash",
    ]
    assert default_chain().complex_targets[0].provider == "openai"


@pytest.mark.parametrize("task", ["coding", "complex"])
def test_complex_tasks_only_attempt_luna(tmp_path, monkeypatch, task):
    for key in ENVIRONMENT.values():
        monkeypatch.setenv(key, "fixture")
    transports = {
        key: type(
            "Transport",
            (),
            {
                "generate": AsyncMock(
                    return_value=ChatResponse(
                        provider=key,
                        model="gpt-5.6-luna",
                        served_model="gpt-5.6-luna",
                        text="{}",
                        input_tokens=1,
                        output_tokens=1,
                    )
                )
            },
        )()
        for key in ENVIRONMENT
    }
    request = ChatRequest(
        system="s",
        user="u",
        schema_json="{}",
        max_output_tokens=256,
        timeout_seconds=1,
        task_class=task,
    )
    with Ledger(tmp_path, 10000) as ledger:
        response = asyncio.run(
            Router(default_chain(), transports=transports).complete(request, ledger)
        )
    assert response.model == "gpt-5.6-luna"
    assert transports["openai"].generate.await_count == 1
    assert all(t.generate.await_count == 0 for p, t in transports.items() if p != "openai")


def test_missing_luna_key_does_not_downgrade(tmp_path, monkeypatch):
    for key in ENVIRONMENT.values():
        monkeypatch.setenv(key, "fixture")
    monkeypatch.delenv("OPENAI_API_KEY")
    request = ChatRequest(
        system="s",
        user="u",
        schema_json="{}",
        max_output_tokens=256,
        timeout_seconds=1,
        task_class="complex",
    )
    with Ledger(tmp_path, 10000) as ledger:
        assert asyncio.run(Router(default_chain(), transports={}).complete(request, ledger)) is None
        assert ledger.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_legacy_chain_and_request_serialization_unchanged():
    chain = Chain.model_validate({"targets": [{"provider": "gemini", "model": "legacy"}]})
    assert "complex_targets" not in chain.model_dump()
    request = ChatRequest(
        system="s", user="u", schema_json="{}", max_output_tokens=256, timeout_seconds=1
    )
    assert "task_class" not in request.model_dump()
    assert chain.for_task("coding") == chain.targets


def test_committee_defaults_route_reviewers_and_chief(setup, monkeypatch):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    for key in ENVIRONMENT.values():
        monkeypatch.setenv(key, "fixture")
    provider = RoutedProvider(ModelSettings(model="shared-policy"))
    assert provider.prepare("research", bundle).route_provider == "groq"
    assert provider.prepare_attempt("research", bundle, (), 2).route_provider == "gemini"
    assert provider.prepare("chief_pm", bundle).settings.model == "gpt-5.6-luna"
    seen, prior = [], []

    async def invoke(request, _remaining_seconds):
        seen.append((request.role, request.route_provider, request.settings.model))
        if request.route_provider == "groq":
            return ModelResponse(error_code="rate_limit", retryable=True)
        assessment = FakeProvider().assess(
            request.role, bundle, tuple(prior) if request.role == "chief_pm" else ()
        )
        if request.role != "chief_pm":
            prior.append(assessment)
        return ModelResponse(text=assessment.canonical(), input_tokens=10, output_tokens=5)

    monkeypatch.setattr(provider, "invoke", invoke)
    with Store(root / "committee") as store:
        store.import_bundle(bundle, blobs)
        decision = run_review(store, bundle, provider, policy=Policy())
        calls = store.model_calls(decision.review_id)
        assert len(calls) == 7
        assert len({(c["role"], c["attempt"]) for c in calls}) == 7
        for call in calls:
            request = json.loads(store.artifact(call["request_hash"]))
            assert request["route_provider"] in {"groq", "gemini", "openai"}
    assert seen[-1] == ("chief_pm", "openai", "gpt-5.6-luna")


def test_committee_wire_records_served_model(setup, monkeypatch):
    root, catalog = setup
    bundle, _ = make_bundle(catalog, "test", root)
    monkeypatch.setenv("GROQ_API_KEY", "fixture")
    provider = RoutedProvider(ModelSettings(model="shared-policy"))
    request = provider.prepare("research", bundle)
    generate = AsyncMock(
        return_value=ChatResponse(
            provider="groq",
            model=request.settings.model,
            served_model="actual-model",
            text="{}",
            input_tokens=3,
            output_tokens=2,
        )
    )
    monkeypatch.setattr("portfolio_team.routing.OpenAICompatibleTransport.generate", generate)
    response = asyncio.run(RoutedTransport().generate(request))
    assert json.loads(response.raw_json)["served_model"] == "actual-model"
    assert generate.await_args.kwargs["base_url"] == "https://api.groq.com/openai/v1"


def test_advisory_auto_prefers_shared_policy(monkeypatch):
    from amat.reporting.advisory import request_advisory

    seen = []

    async def complete(self, request, budget, **_kwargs):
        seen.extend(self.chain.targets)
        response = ChatResponse(
            provider="openrouter",
            model="openrouter/free",
            served_model="actual",
            text='{"commentary":"Measured evidence only."}',
            input_tokens=4,
            output_tokens=5,
        )
        call = budget.reserve(self.chain.targets[1], request)
        budget.settle(call, response)
        return response

    monkeypatch.setattr(Router, "complete", complete)
    result = request_advisory({"return": 0.1})
    assert result.text == "Measured evidence only."
    assert seen[0].provider == "groq"
    assert "actual" in result.provenance


def test_global_config_override_is_shared(tmp_path, monkeypatch):
    path = tmp_path / "models.toml"
    path.write_text('[[targets]]\nprovider="openrouter"\nmodel="openrouter/free"\n')
    monkeypatch.setenv("AMAT_MODEL_CHAIN", str(path))
    assert default_chain() == Chain.load(path)


def test_primary_research_help_stays_small(capsys):
    from test_research_shortcuts import script

    assert script("research_cli").main(["--help"]) == 0
    text = capsys.readouterr().out
    assert "./research run [N]" in text
    assert "./research status" in text
    assert "./research report" in text
    assert "./research data update" not in text
