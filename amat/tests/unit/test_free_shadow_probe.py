"""Mocked tests: no network, broker, real portfolio, or performance evidence."""

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from autoresearch.improvements.agent_portfolio_arm import hold_contract
from llmrouter.contracts import ChatResponse

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_free_shadow_probe.py"


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("free_probe_test", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def protocol():
    return {
        "schema": "free-shadow-probe/1",
        "model_id": "vendor/model:free",
        "expected_served_model": "vendor/model:free",
        "decision_at": "2026-10-24T00:20:00Z",
        "execution_at": "2026-10-26T13:30:00Z",
        "expires_at": "2026-10-26T13:35:00Z",
        "campaign_deadline_utc": "2026-10-24T04:40:10Z",
        "hard_stop_utc": "2026-10-24T00:56:36Z",
    }


def catalog(protocol):
    return {
        "data": [
            {
                "id": protocol["model_id"],
                "pricing": {"prompt": "0", "completion": "0", "request": "0"},
                "supported_parameters": ["structured_outputs"],
            }
        ]
    }


def setup_probe(mod, protocol, monkeypatch, response=None, key_info=None):
    monkeypatch.setattr(mod, "utcnow", lambda: dt.datetime(2026, 10, 24, 0, 25, tzinfo=dt.UTC))
    key_info = key_info or {
        "data": {
            "is_free_tier": True,
            "free_model_daily_requests": {"used": 1, "limit": 50, "remaining": 49},
            "label": "SECRET PRIVATE LABEL",
        }
    }
    monkeypatch.setattr(
        mod,
        "get_json",
        lambda endpoint, _key="": catalog(protocol) if endpoint == "models" else key_info,
    )
    calls = []

    async def generate(_self, request, **kwargs):
        calls.append((request, kwargs))
        return response

    monkeypatch.setattr(mod.NoRedirectTransport, "generate", generate)
    return calls


def valid_response(mod, protocol, **updates):
    data = dict(  # noqa: C408 -- readable fixture with later overrides
        provider="openrouter",
        model=protocol["model_id"],
        served_model=protocol["expected_served_model"],
        text=json.dumps(
            hold_contract(
                mod.context_for(protocol), model_id=protocol["model_id"], reason="hold synthetic"
            )
        ),
        raw_json='{"provider":"test-host","full":"retained","usage":{"cost":0}}',
        input_tokens=100,
        output_tokens=150,
    )
    data.update(updates)
    return ChatResponse(**data)


def test_synthetic_hold_preserves_contract_and_raw(mod, protocol, monkeypatch, tmp_path):
    calls = setup_probe(mod, protocol, monkeypatch, valid_response(mod, protocol))
    result = mod.probe(tmp_path, "test", protocol, "secret-key")
    assert result["status"] == "synthetic_contract_passed"
    assert (
        result["response"]["raw_json"]
        == '{"provider":"test-host","full":"retained","usage":{"cost":0}}'
    )
    assert result["dispatches"] == len(calls) == 1
    assert (
        result["performance_evidence"]
        is result["promotion_authority"]
        is result["prospective_evidence"]
        is False
    )
    assert result["execution_authority"] == "none"
    assert "SECRET PRIVATE LABEL" not in json.dumps(result)
    assert result["request"]["max_output_tokens"] == 2048
    assert calls[0][0].persistence == "full"
    assert len((tmp_path / "free-model-calls.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("served_model", None),
        ("served_model", "paid/model"),
        ("provider", "unknown"),
        ("model", "other:free"),
        ("text", "not-json"),
    ],
)
def test_identity_and_malformed_fail_closed(mod, protocol, monkeypatch, tmp_path, field, value):  # noqa: PLR0917
    setup_probe(mod, protocol, monkeypatch, valid_response(mod, protocol, **{field: value}))
    result = mod.probe(tmp_path, "test", protocol, "secret-key")
    assert result["status"] == "dispatch_failed"
    assert result["dispatches"] == 1
    assert "validation" not in result


def test_paid_catalog_does_not_reserve_or_dispatch(mod, protocol, monkeypatch, tmp_path):
    calls = setup_probe(mod, protocol, monkeypatch)
    data = catalog(protocol)
    data["data"][0]["pricing"]["request"] = "0.001"
    monkeypatch.setattr(mod, "get_json", lambda *_args: data)
    result = mod.probe(tmp_path, "test", protocol, "secret")
    assert result["status"] == "preflight_failed"
    assert not calls and not (tmp_path / "free-model-calls.jsonl").exists()


def test_exhausted_provider_capacity_no_dispatch(mod, protocol, monkeypatch, tmp_path):
    calls = setup_probe(
        mod,
        protocol,
        monkeypatch,
        key_info={
            "data": {
                "is_free_tier": True,
                "free_model_daily_requests": {"used": 50, "limit": 50, "remaining": 0},
            }
        },
    )
    result = mod.probe(tmp_path, "test", protocol, "secret")
    assert result["dispatches"] == 0
    assert not calls and not (tmp_path / "free-model-calls.jsonl").exists()


def test_rate_limit_retained_and_no_retry(mod, protocol, monkeypatch, tmp_path):
    calls = setup_probe(
        mod,
        protocol,
        monkeypatch,
        valid_response(
            mod, protocol, error_class="rate_limit", error_code="provider_http_429", text=None
        ),
    )
    assert mod.probe(tmp_path, "test", protocol, "secret")["status"] == "provider_failed"
    result = mod.probe(tmp_path, "test", protocol, "secret")
    assert result["status"] == "preflight_failed"
    assert len(calls) == 1


@pytest.mark.parametrize("count,cycle", [(24, "old"), (2, "test")])
def test_budget_all_failures_count(mod, protocol, monkeypatch, tmp_path, count, cycle):  # noqa: PLR0917
    calls = setup_probe(mod, protocol, monkeypatch)
    (tmp_path / "free-model-calls.jsonl").write_text(
        "\n".join(json.dumps({"cycle_id": cycle}) for _ in range(count))
    )
    result = mod.probe(tmp_path, "test", protocol, "secret")
    assert result["dispatches"] == 0 and not calls


def test_deadline_recheck_before_dispatch(mod, protocol, monkeypatch, tmp_path):
    calls = setup_probe(mod, protocol, monkeypatch)
    monkeypatch.setattr(mod, "utcnow", lambda: dt.datetime(2026, 10, 24, 0, 56, tzinfo=dt.UTC))
    result = mod.probe(tmp_path, "test", protocol, "secret")
    assert result["dispatches"] == 0 and not calls


def test_schema_and_fixture_advanced_capabilities_disabled(mod, protocol):
    context, request = mod.request_for(protocol)
    assert context.account_id == "synthetic-shadow-probe-only"
    assert (
        not context.policy.allow_direct_short
        and not context.policy.allow_inverse
        and not context.policy.allow_intraday
    )
    assert context.confirmed_cash == 9000 and context.equity == 10000
    schema = json.loads(request.schema_json)
    assert schema["properties"]["actions"]["maxItems"] == 1
    assert schema["properties"]["actions"]["items"]["properties"]["kind"]["enum"] == ["hold"]


def test_scrub_full_nested_raw_strings(mod):
    value = {
        "raw_json": '{"Authorization":"Bearer abc123", "key":"secret-key"}',
        "text": "a" * 5000,
        "authorization": "private",
        "nested": ["sk-or-v1-secret123"],
    }
    result = mod.scrub(value, "secret-key")
    assert len(result["text"]) == 5000
    assert "abc123" not in result["raw_json"] and "secret-key" not in result["raw_json"]
    assert result["authorization"] == "[REDACTED]" and result["nested"] == ["[REDACTED]"]


def test_output_symlink_and_partial_seal_refused(mod, tmp_path):
    path = tmp_path / "report.json"
    path.with_suffix(".sha256").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="Immutable"):
        mod.publish(path, {"x": 1})
    assert not path.exists()


def test_publication_sealed_and_immutable(mod, tmp_path):
    path = tmp_path / "report.json"
    mod.publish(path, {"x": 1})
    assert path.with_suffix(".sha256").read_text().strip() == mod.digest({"x": 1})
    with pytest.raises(ValueError):
        mod.publish(path, {"x": 2})


def test_unmanaged_run_refused_before_credentials(mod, tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text('{"active_run":null}')
    monkeypatch.delenv("AMAT_RESEARCH_RUN_ID", raising=False)
    with pytest.raises(ValueError, match="Managed active run"):
        mod.verify_context(tmp_path, tmp_path / "protocol.json", tmp_path / "result.json", "test")


def test_authorization_other_schemes_in_raw_body_redacted(mod):
    result = mod.scrub(
        {
            "raw_json": '{"authorization":"Token private-value","api_key":"other-secret"}',
            "text": "Authorization: Basic private-value\nrest",
        },
        "credential",
    )
    assert "private-value" not in json.dumps(result)
    assert "other-secret" not in json.dumps(result)


@pytest.mark.parametrize("cost", ["0.1", -1, "NaN", True])
def test_nonzero_or_invalid_actual_billing_rejected(mod, protocol, monkeypatch, tmp_path, cost):
    calls = setup_probe(
        mod,
        protocol,
        monkeypatch,
        valid_response(mod, protocol, raw_json=json.dumps({"usage": {"cost": cost}})),
    )
    result = mod.probe(tmp_path, "test", protocol, "secret")
    assert result["status"] == "dispatch_failed" and len(calls) == 1
    assert "validation" not in result


def test_actual_cost_missing_is_explicitly_unverified(mod, protocol, monkeypatch, tmp_path):
    setup_probe(mod, protocol, monkeypatch, valid_response(mod, protocol, raw_json="{}"))
    result = mod.probe(tmp_path, "test", protocol, "secret")
    assert result["status"] == "synthetic_contract_cost_unverified"
    assert result["billing"] == {"status": "unavailable", "cost_usd": None}
    assert result["performance_evidence"] is False


def test_late_durable_reservation_counts_but_never_calls_model(
    mod, protocol, monkeypatch, tmp_path
):
    calls = setup_probe(mod, protocol, monkeypatch, valid_response(mod, protocol))
    original = mod.reserve

    def slow_reserve(*args):
        result = original(*args)
        monkeypatch.setattr(mod, "utcnow", lambda: dt.datetime(2026, 10, 24, 0, 56, tzinfo=dt.UTC))
        return result

    monkeypatch.setattr(mod, "reserve", slow_reserve)
    result = mod.probe(tmp_path, "test", protocol, "secret")
    assert result["reservation_consumed"] is True
    assert result["inference_attempted"] is False and result["dispatches"] == 0
    assert not calls
    assert len((tmp_path / "free-model-calls.jsonl").read_text().splitlines()) == 1


def test_sdk_transport_redirects_retries_and_fallbacks_disabled(mod, protocol, monkeypatch):
    import asyncio
    import types

    captured = {}

    def http_client(**kwargs):
        captured["http"] = kwargs
        return "mock-http-client"

    class Client:
        def __init__(self, **kwargs):
            captured["sdk"] = kwargs
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create(self, **kwargs):
            captured["wire"] = kwargs
            return "completion"

    monkeypatch.setattr(mod, "DefaultAsyncHttpxClient", http_client)
    monkeypatch.setattr(mod, "AsyncOpenAI", Client)
    monkeypatch.setattr(mod, "_finalize_chat", lambda **kwargs: kwargs)
    _, request = mod.request_for(protocol)
    asyncio.run(
        mod.NoRedirectTransport().generate(
            request, model=protocol["model_id"], api_key="secret", base_url=mod.BASE
        )
    )
    assert captured["http"] == {"follow_redirects": False}
    assert captured["sdk"]["max_retries"] == 0
    assert captured["sdk"]["timeout"] == 45
    assert captured["wire"]["extra_body"]["provider"] == {
        "allow_fallbacks": False,
        "require_parameters": True,
    }
    assert "tools" not in captured["wire"]


def test_sdk_http_error_full_body_preserved_and_sanitized(mod, protocol, monkeypatch, tmp_path):
    import types

    import httpx

    body = {"error": {"message": "x" * 5000 + " secret-key", "authorization": "Token private"}}

    class Client:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create(self, **kwargs):
            response = httpx.Response(
                429,
                request=httpx.Request("POST", mod.BASE + "/chat/completions"),
                content=json.dumps(body),
            )
            raise mod.APIStatusError("rate limited", response=response, body=body)

    monkeypatch.setattr(mod, "AsyncOpenAI", Client)
    monkeypatch.setattr(mod, "DefaultAsyncHttpxClient", lambda **_kwargs: "mock")
    monkeypatch.setattr(mod, "utcnow", lambda: dt.datetime(2026, 10, 24, 0, 25, tzinfo=dt.UTC))
    monkeypatch.setattr(
        mod,
        "get_json",
        lambda endpoint, _key="": (
            catalog(protocol) if endpoint == "models" else {"data": {"is_free_tier": True}}
        ),
    )
    result = mod.probe(tmp_path, "test", protocol, "secret-key")
    assert result["status"] == "provider_failed"
    raw = result["response"]["raw_json"]
    assert len(raw) > 5000 and "secret-key" not in raw and "Token private" not in raw
    assert result["response"]["error_class"] == "rate_limit"
