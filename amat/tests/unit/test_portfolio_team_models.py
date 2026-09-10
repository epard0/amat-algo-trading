"""Model transport integration: budgets, cancellation, raw evidence and audit semantics."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_portfolio_team import setup as evidence_fixture

from portfolio_team.cli import main
from portfolio_team.contracts import Assessment, Policy
from portfolio_team.coordinator import run_review
from portfolio_team.evaluation import EvalCase, EvalCatalog, evaluate
from portfolio_team.evidence import EvidenceError, make_bundle
from portfolio_team.model_runtime import (
    GeminiTransport,
    ModelResponse,
    ModelSettings,
    RemoteProvider,
    gemini_schema,
)
from portfolio_team.providers import FakeProvider
from portfolio_team.reports import export_review, render
from portfolio_team.store import Store


@pytest.fixture
def setup(tmp_path):
    return evidence_fixture.__wrapped__(tmp_path)


class Transport:
    def __init__(self, bundle, *, failure=None, delay=0, missing_usage=False):
        self.bundle = bundle
        self.failure = failure
        self.delay = delay
        self.missing_usage = missing_usage
        self.calls = 0
        self.cancelled = 0

    async def generate(self, request):
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.failure == "auth":
            return ModelResponse(error_code="provider_http_401")
        if self.failure == "exception":
            raise ValueError("private-key-must-not-be-recorded")
        if self.failure == "malformed":
            return ModelResponse(
                text="bad-json",
                raw_json='{"retained":"bad-json"}',
                input_tokens=100,
                output_tokens=10,
            )
        contents = json.loads(request.contents)
        previous = tuple(
            Assessment.model_validate(item) for item in contents["previous_assessments"]
        )
        result = FakeProvider().assess(request.role, self.bundle, previous)
        return ModelResponse(
            text=result.canonical(),
            raw_json='{"request_id":"fixture"}',
            input_tokens=None if self.missing_usage else 100,
            output_tokens=None if self.missing_usage else 20,
        )


def test_real_transport_contract_capture_single_baseline_and_replay(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    transport = Transport(bundle)
    provider = RemoteProvider(ModelSettings(model="test-model"), transport)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        decision = run_review(store, bundle, provider)
        assert transport.calls == 4
        assert sum(call["actual_input"] for call in store.model_calls(decision.review_id)) == 400
        target = export_review(store, decision, root / "reports")
        assert "MODEL SHADOW REVIEW" in render(bundle, decision)
        assert "OFFLINE SIMULATION" not in render(bundle, decision)
        for call in store.model_calls(decision.review_id):
            assert (
                json.loads(store.artifact(call["request_hash"]))["settings"]["model"]
                == "test-model"
            )
            assert json.loads(store.artifact(call["response_hash"]))["raw_json"]
        assert (target / "model_calls.json").is_file()
        assert run_review(store, bundle, provider) == decision
        assert transport.calls == 4
        single = run_review(store, bundle, provider, single_reviewer=True)
        assert len(single.assessments) == 1
        assert single.review_id != decision.review_id
        assert transport.calls == 5
        assert (
            json.loads(store.artifact(single.protocol_sha256))["mode"] == "single-reviewer-baseline"
        )


@pytest.mark.parametrize("failure", ["auth", "exception", "malformed"])
def test_failures_preserve_response_or_unknown_reservation(setup, failure):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    transport = Transport(bundle, failure=failure)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        with pytest.raises(EvidenceError):
            run_review(
                store,
                bundle,
                RemoteProvider(ModelSettings(model="test"), transport),
                single_reviewer=True,
            )
        assert transport.calls == (2 if failure == "malformed" else 1)
        calls = store.db.execute("SELECT * FROM model_calls").fetchall()
        assert all(call["response_hash"] for call in calls)
        assert all(call["charged_input"] > 0 for call in calls)
        assert store.db.execute("SELECT decision FROM reviews").fetchone()[0] is None
        dumped = " ".join(
            bytes(row[0]).decode() for row in store.db.execute("SELECT content FROM artifacts")
        )
        assert "private-key-must" not in dumped


def test_deadline_cancels_transport_and_retains_usage(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    transport = Transport(bundle, delay=5)
    settings = ModelSettings(model="test", request_timeout_seconds=0.02, review_timeout_seconds=0.1)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        started = time.monotonic()
        with pytest.raises(EvidenceError):
            run_review(store, bundle, RemoteProvider(settings, transport), single_reviewer=True)
        assert time.monotonic() - started < 1
        assert transport.cancelled == 2
        assert all(
            row[0] == row[1]
            for row in store.db.execute("SELECT charged_output,reserved_output FROM model_calls")
        )


def test_pre_dispatch_budget_and_durable_daily_limit(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    transport = Transport(bundle, missing_usage=True)
    settings = ModelSettings(model="test", max_input_reservation_per_day=1)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        with pytest.raises(EvidenceError, match="deferred_budget"):
            run_review(store, bundle, RemoteProvider(settings, transport), single_reviewer=True)
        assert transport.calls == 0
    settings = ModelSettings(model="test")
    with Store(root / "registry") as store:
        decision = run_review(
            store, store.resolve("test"), RemoteProvider(settings, transport), single_reviewer=True
        )
        call = store.model_calls(decision.review_id)[0]
        assert call["charged_input"] == call["reserved_input"]
        retained = call["charged_input"]
    settings = ModelSettings(model="different-test", max_input_reservation_per_day=retained)
    with Store(root / "registry") as store:
        with pytest.raises(EvidenceError, match="deferred_budget"):
            run_review(
                store,
                store.resolve("test"),
                RemoteProvider(settings, transport),
                single_reviewer=True,
            )
        assert transport.calls == 1


def test_original_offline_protocol_id_is_preserved(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        result = run_review(store, bundle, FakeProvider(), Policy())
        assert result.provider_id == "fake-scripted/1"
        assert "OFFLINE SIMULATION" in render(bundle, result)


def test_endpoint_schema_omits_unsupported_property_but_local_validation_is_strict(setup):
    root, catalog = setup
    assert "additionalProperties" not in json.dumps(gemini_schema())
    bundle, _ = make_bundle(catalog, "test", root)
    request = RemoteProvider(ModelSettings(model="test")).prepare("research", bundle)
    schema = json.loads(request.output_schema_json)
    assert schema["properties"]["role"]["enum"] == ["research"]
    assert schema["properties"]["evidence_sha256"]["enum"] == [bundle.digest()]
    response = FakeProvider().assess("research", bundle).model_dump()
    response["execute_orders"] = True
    with pytest.raises(ValueError):
        Assessment.model_validate(response)


@pytest.mark.parametrize("with_total, expected_output", [(True, 30), (False, 40)])
def test_gemini_interactions_wire_contract_and_usage(
    setup, monkeypatch, with_total, expected_output
):
    root, catalog = setup
    bundle, _ = make_bundle(catalog, "test", root)
    request = RemoteProvider(ModelSettings(model="test")).prepare("research", bundle)
    usage = {"total_input_tokens": 100, "total_output_tokens": 30, "total_thought_tokens": 10}
    if with_total:
        usage["total_tokens"] = 130
    response = MagicMock()
    response.model_dump.return_value = {"usage": usage}
    response.output_text = '{"test":true}'
    create = AsyncMock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = SimpleNamespace(interactions=SimpleNamespace(create=create))
    factory = MagicMock(return_value=SimpleNamespace(aio=context))
    monkeypatch.setenv("GEMINI_API_KEY", "test-private-key")
    monkeypatch.setattr("portfolio_team.model_runtime.genai.Client", factory)

    result = asyncio.run(GeminiTransport().generate(request))

    assert result.error_code is None
    assert result.text == response.output_text
    assert result.input_tokens == 100
    assert result.output_tokens == expected_output
    assert "test-private-key" not in result.canonical()
    options = factory.call_args.kwargs["http_options"]
    assert options.base_url == "https://generativelanguage.googleapis.com"
    assert options.retry_options.attempts == 0
    args = create.call_args.kwargs
    assert args["store"] is False
    assert "tools" not in args
    assert args["model"] == "test"
    assert args["response_format"]["schema"] == json.loads(request.output_schema_json)
    assert args["generation_config"]["max_output_tokens"] == 4096
    assert args["timeout"] == 90
    create.assert_awaited_once()


def test_gemini_transport_scrubs_sdk_auth_error(setup, monkeypatch):
    from google.genai import errors

    root, catalog = setup
    bundle, _ = make_bundle(catalog, "test", root)
    request = RemoteProvider(ModelSettings(model="test")).prepare("research", bundle)
    factory = MagicMock(side_effect=errors.ClientError(401, {"message": "denied test-private-key"}))
    monkeypatch.setenv("GEMINI_API_KEY", "test-private-key")
    monkeypatch.setattr("portfolio_team.model_runtime.genai.Client", factory)
    response = asyncio.run(GeminiTransport().generate(request))
    assert response.error_code == "provider_http_401"
    assert response.retryable is False
    assert "test-private-key" not in response.canonical()
    assert "[REDACTED]" in response.raw_json


def test_interactions_rate_limit_is_retryable_and_scrubbed(setup, monkeypatch):
    import httpx
    from google.genai._gaos.lib.compat_errors import RateLimitError

    root, catalog = setup
    bundle, _ = make_bundle(catalog, "test", root)
    request = RemoteProvider(ModelSettings(model="test")).prepare("research", bundle)
    error = RateLimitError(
        "limited test-private-key",
        response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid")),
        body=None,
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-private-key")
    monkeypatch.setattr("portfolio_team.model_runtime.genai.Client", MagicMock(side_effect=error))
    response = asyncio.run(GeminiTransport().generate(request))
    assert response.error_code == "provider_http_429"
    assert response.retryable is True
    assert "test-private-key" not in response.canonical()
    assert "[REDACTED]" in response.raw_json


def test_rate_limit_retry_preserves_independence_and_bounded_dispatch(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)

    class LimitedTransport(Transport):
        active = 0
        peak = 0
        throttled = False

        async def generate(self, request):
            assert (
                request.role == "chief_pm"
                or not json.loads(request.contents)["previous_assessments"]
            )
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                if not self.throttled:
                    self.throttled = True
                    return ModelResponse(error_code="provider_http_429", retryable=True)
                return await super().generate(request)
            finally:
                self.active -= 1

    transport = LimitedTransport(bundle, delay=0.01)
    settings = ModelSettings(model="test", concurrent_requests=1, rate_limit_retry_seconds=0)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        decision = run_review(store, bundle, RemoteProvider(settings, transport))
        assert len(store.model_calls(decision.review_id)) == 5
        assert transport.peak == 1
        assert len(decision.assessments) == 4


def test_evaluation_labels_are_not_sent_to_model_and_snapshots_replay(setup):
    root, catalog = setup
    bundle, blobs = make_bundle(catalog, "test", root)
    labels = EvalCatalog(
        schema_version="pmteam-reference-eval/1",
        cases=(
            EvalCase(
                id="private-expected-issue-case",
                comparison="test",
                expected_issue_ids=("daily_series",),
            ),
        ),
    )
    transport = Transport(bundle)
    provider = RemoteProvider(ModelSettings(model="test"), transport)
    with Store(root / "registry") as store:
        store.import_bundle(bundle, blobs)
        target = evaluate(store, labels, provider, root / "reports/evaluations")
        payload = json.loads((target / "results.json").read_text())
        assert len(payload["rows"]) == 3
        assert all(row["covered"] == 1 for row in payload["rows"])
        for row in store.db.execute("SELECT request_hash FROM model_calls"):
            request = store.artifact(row[0]).decode()
            assert "private-expected-issue-case" not in request
            assert "expected_issue_ids" not in request
        assert evaluate(store, labels, provider, root / "reports/evaluations") == target
        assert transport.calls == 5


@pytest.mark.parametrize("covered, exit_code", [(None, 1), (1, 0)])
def test_cli_evaluation_failure_exit_status(tmp_path, monkeypatch, covered, exit_code):
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "results.json").write_text(json.dumps({"rows": [{"covered": covered}]}))
    monkeypatch.setattr("portfolio_team.cli.load_cases", lambda _: None)
    monkeypatch.setattr("portfolio_team.cli.evaluate", lambda *_: tmp_path)
    assert main(["--root", str(tmp_path), "evaluate", "unused.toml"]) == exit_code
