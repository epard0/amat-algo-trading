"""No-network acceptance for OpenAI chief routing and scheduled review cycles."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import AuthenticationError, RateLimitError
from test_portfolio_team import setup as evidence_fixture
from test_portfolio_team_models import Transport

from portfolio_team.automation import run_cycle
from portfolio_team.cli import _parser, _provider
from portfolio_team.contracts import Policy
from portfolio_team.coordinator import review_identity, run_review
from portfolio_team.evidence import EvidenceError, make_bundle
from portfolio_team.model_runtime import ModelSettings, RemoteProvider
from portfolio_team.openai_runtime import (
    OpenAIProvider,
    OpenAIRequest,
    OpenAISettings,
    OpenAITransport,
    ReviewTeam,
)
from portfolio_team.providers import FakeProvider
from portfolio_team.store import Store


@pytest.fixture
def setup(tmp_path):
    return evidence_fixture.__wrapped__(tmp_path)


def request_for(setup):
    root, catalog = setup
    bundle, _ = make_bundle(catalog, "test", root)
    return OpenAIProvider(OpenAISettings(model="gpt-6-astra")).prepare("chief_pm", bundle)


@pytest.mark.parametrize(
    "status,refusal,error",
    [
        ("completed", False, None),
        ("incomplete", False, "provider_incomplete_response"),
        ("completed", True, "provider_refusal"),
    ],
)
def test_openai_wire_contract_usage_and_incomplete(setup, monkeypatch, status, refusal, error):
    request = request_for(setup)
    assert isinstance(request, OpenAIRequest)
    assert json.loads(request.canonical())["settings"]["reasoning_effort"] == "high"
    schema = json.loads(request.output_schema_json)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["role"]["enum"] == ["chief_pm"]
    response = MagicMock()
    response.status = status
    response.output_text = "{}"
    response.usage = SimpleNamespace(input_tokens=100, output_tokens=200)
    response.model_dump.return_value = {
        "status": status,
        "output": [{"content": [{"type": "refusal" if refusal else "output_text"}]}],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 200,
            "output_tokens_details": {"reasoning_tokens": 150},
        },
    }
    create = AsyncMock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(create=create))
    factory = MagicMock(return_value=context)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")
    monkeypatch.setattr("portfolio_team.openai_runtime.AsyncOpenAI", factory)
    result = asyncio.run(OpenAITransport().generate(request))
    assert result.error_code == error
    assert result.output_tokens == 200  # reasoning is already included
    assert result.input_tokens == 100
    assert "test-secret" not in result.canonical()
    assert factory.call_args.kwargs["base_url"] == "https://api.openai.com/v1"
    assert factory.call_args.kwargs["max_retries"] == 0
    sent = create.call_args.kwargs
    assert sent["model"] == "gpt-6-astra"
    assert sent["reasoning"] == {"effort": "high"}
    assert sent["text"]["format"]["strict"] is True
    assert sent["store"] is False
    assert "tools" not in sent
    assert sent["max_output_tokens"] == 8192
    create.assert_awaited_once()


@pytest.mark.parametrize(
    "status,error_class,retry", [(401, AuthenticationError, False), (429, RateLimitError, True)]
)
def test_openai_errors_are_redacted(setup, monkeypatch, status, error_class, retry):
    request = request_for(setup)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    exception = error_class(
        "private test-secret",
        response=httpx.Response(status, request=httpx.Request("POST", "https://example.invalid")),
        body={"private": "test-secret"},
    )
    monkeypatch.setattr(
        "portfolio_team.openai_runtime.AsyncOpenAI", MagicMock(side_effect=exception)
    )
    result = asyncio.run(OpenAITransport().generate(request))
    assert result.error_code == f"provider_http_{status}"
    assert result.retryable is retry
    assert "test-secret" not in result.canonical()


def test_routed_chief_capture_identity_reservations_and_cache(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    worker_transport = Transport(bundle)
    chief_transport = Transport(bundle)
    workers = RemoteProvider(ModelSettings(model="gemini-test"), worker_transport)
    chief = OpenAIProvider(OpenAISettings(model="gpt-6-astra"), chief_transport)
    team = ReviewTeam(workers, chief)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        result = run_review(store, bundle, team)
        assert worker_transport.calls == 3
        assert chief_transport.calls == 1
        assert len(result.assessments[-1].addressed_findings) > 0
        for call in store.model_calls(result.review_id):
            sent = json.loads(store.artifact(call["request_hash"]))
            if call["role"] == "chief_pm":
                assert sent["settings"]["model"] == "gpt-6-astra"
                assert sent["settings"]["reasoning_effort"] == "high"
                assert call["reserved_output"] == 8192
                assert len(json.loads(sent["contents"])["previous_assessments"]) == 3
            else:
                assert sent["settings"]["model"] == "gemini-test"
                assert call["reserved_output"] == 4096
                assert not json.loads(sent["contents"])["previous_assessments"]
        assert run_review(store, bundle, team) == result
        assert chief_transport.calls == 1
        other = ReviewTeam(workers, OpenAIProvider(OpenAISettings(model="another-model")))
        assert (
            review_identity(bundle, team, Policy())[0]
            != review_identity(bundle, other, Policy())[0]
        )
        assert result.execution_authority == "none"


def test_cli_explicit_routing_and_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-secret")
    args = _parser().parse_args(
        [
            "review",
            "test",
            "--provider",
            "gemini",
            "--model",
            "gemini-test",
            "--chief-model",
            "gpt-6-astra",
        ]
    )
    provider = _provider(args)
    assert isinstance(provider, ReviewTeam)
    assert provider.chief.settings.model == "gpt-6-astra"
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(EvidenceError, match="OPENAI_API_KEY"):
        _provider(args)
    args.provider = "fake"
    with pytest.raises(EvidenceError, match="scripted"):
        _provider(args)


def test_cycle_idempotency_backlog_and_failed_job_not_resubmitted(setup, monkeypatch):
    root, catalog = setup
    monkeypatch.setattr("portfolio_team.automation.load_catalog", lambda _: catalog)
    with Store(root / "registry") as store:
        result = run_cycle(store, root, root / "catalog.toml", FakeProvider())
        assert result["rows"][0]["status"] == "completed"
        rid = result["rows"][0]["review_id"]
        backlog = json.loads(
            (root / "outputs/governance" / rid / "research_backlog.json").read_text()
        )
        assert backlog["status"] == "proposed_not_authorized"
        assert (
            run_cycle(store, root, root / "catalog.toml", FakeProvider())["dispatched_reviews"] == 0
        )
        bundle = store.resolve("test")
        broken = Transport(bundle, failure="auth")
        provider = OpenAIProvider(OpenAISettings(model="gpt-6-astra"), broken)
        first = run_cycle(store, root, root / "catalog.toml", provider)
        assert first["incomplete"] is True
        before = broken.calls
        second = run_cycle(store, root, root / "catalog.toml", provider)
        assert second["rows"][0]["status"] == "needs_operator_retry"
        assert broken.calls == before


def test_cycle_bounds_and_quarantines_changed_evidence(setup, monkeypatch):
    root, catalog = setup
    monkeypatch.setattr("portfolio_team.automation.load_catalog", lambda _: catalog)
    with Store(root / "registry") as store:
        with pytest.raises(EvidenceError):
            run_cycle(store, root, root / "catalog.toml", FakeProvider(), max_reviews=0)
        run_cycle(store, root, root / "catalog.toml", FakeProvider())
        (root / catalog.runs[0].path / "results.json").write_text('{"partial":true}')
        result = run_cycle(store, root, root / "catalog.toml", FakeProvider())
        assert result["rows"][0]["status"] == "quarantined"
        assert result["dispatched_reviews"] == 0
        with pytest.raises(EvidenceError):
            store.resolve("test")


def test_cycle_limit_defers_then_picks_up_next_comparison(setup, monkeypatch):
    root, catalog = setup
    extra = catalog.comparisons[0].model_copy(update={"id": "second"})
    catalog = catalog.model_copy(update={"comparisons": (*catalog.comparisons, extra)})
    monkeypatch.setattr("portfolio_team.automation.load_catalog", lambda _: catalog)
    with Store(root / "registry") as store:
        first = run_cycle(store, root, root / "catalog.toml", FakeProvider(), max_reviews=1)
        assert [row["status"] for row in first["rows"]] == ["completed", "deferred_cycle_limit"]
        assert first["dispatched_reviews"] == 1
        second = run_cycle(store, root, root / "catalog.toml", FakeProvider(), max_reviews=1)
        assert [row["status"] for row in second["rows"]] == ["cached", "completed"]
        assert second["dispatched_reviews"] == 1


def test_openai_cannot_bypass_existing_shared_daily_reservations(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        gemini = RemoteProvider(ModelSettings(model="test"), Transport(bundle, missing_usage=True))
        run_review(store, bundle, gemini, single_reviewer=True)
        transport = Transport(bundle)
        settings = OpenAISettings(model="gpt-6-astra", max_output_reservation_per_day=4096 + 8191)
        with pytest.raises(EvidenceError, match="deferred_budget"):
            run_review(store, bundle, OpenAIProvider(settings, transport), single_reviewer=True)
        assert transport.calls == 0
        assert store.db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 1
