"""Bounded synthetic free-model availability probe. No execution or promotion authority."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.request import HTTPRedirectHandler, Request, build_opener

from openai import APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient

from autoresearch.improvements.agent_portfolio_arm import (
    ArmPolicy,
    MarketEvidence,
    ResearchContext,
    hold_contract,
    validate_action_contract,
)
from autoresearch.improvements.incremental_gates import read_sealed
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, source_hashes
from llmrouter.contracts import ChatRequest, ChatResponse, classify_http
from llmrouter.transports import _finalize_chat, _openai_exception_response

BASE = "https://openrouter.ai/api/v1"
MAX_BYTES = 16 * 1024 * 1024
CAMPAIGN_CALLS = 24
CYCLE_CALLS = 2
REQUIRED_SECONDS = 100


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def instant(value: str) -> dt.datetime:
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Aware UTC clock required")
    return result


def scrub(value: Any, key: str) -> Any:
    """Preserve full responses while removing credential and authorization material."""
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if k.lower() in {"authorization", "api_key", "apikey"}
            else scrub(v, key)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v, key) for v in value]
    if isinstance(value, str):
        # Raw JSON may embed another JSON body as an escaped string. Walk its
        # decoded fields before regex scrubbing, keeping unchanged strings exact.
        if value.lstrip().startswith(("{", "[", '"')):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, (dict, list, str)):
                cleaned = scrub(decoded, key)
                if cleaned != decoded:
                    return json.dumps(cleaned, ensure_ascii=False)
        text = value.replace(key, "[REDACTED]") if key else value
        text = re.sub(
            r'(?i)("(?:authorization|api_key|apikey)"\s*:\s*")[^"\r\n]*(")',
            r"\1[REDACTED]\2",
            text,
        )
        text = re.sub(r"(?im)^authorization\s*:[^\r\n]*", "Authorization: [REDACTED]", text)
        return re.sub(r"(?i)(?:bearer\s+|sk-or-v1-)[A-Za-z0-9._~+/=-]+", "[REDACTED]", text)
    return value


class NoRedirect(HTTPRedirectHandler):
    """Never forward authenticated metadata reads to another endpoint."""

    def redirect_request(
        self, _req: Any, _fp: Any, _code: int, _msg: str, _headers: Any, _newurl: str
    ) -> None:
        raise ValueError("Provider metadata redirect is forbidden")


def get_json(endpoint: str, key: str = "") -> dict:
    if endpoint not in {"models", "key"}:
        raise ValueError("Only fixed read-only catalog/key endpoints permitted")
    request = Request(  # noqa: S310 -- fixed HTTPS host and allowlisted endpoints
        BASE + "/" + endpoint, headers={"Authorization": "Bearer " + key} if key else {}
    )
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("Provider metadata exceeds bounded response size")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Provider metadata must be an object")
    return value


def verified_model(catalog: dict, model_id: str) -> dict:
    rows = catalog.get("data")
    if (
        not isinstance(rows, list)
        or not model_id.endswith(":free")
        or model_id == "openrouter/free"
    ):
        raise ValueError("Pinned named free model required")
    matching = [r for r in rows if isinstance(r, dict) and r.get("id") == model_id]
    if len(matching) != 1:
        raise ValueError("Pinned model absent or ambiguous")
    model = matching[0]
    pricing = model.get("pricing")
    if not isinstance(pricing, dict) or not {"prompt", "completion"} <= pricing.keys():
        raise ValueError("Complete current pricing unavailable")
    if any(not Decimal(str(v)).is_finite() or Decimal(str(v)) != 0 for v in pricing.values()):
        raise ValueError("Every reported price must be exactly zero")
    if "structured_outputs" not in model.get("supported_parameters", []):
        raise ValueError("Pinned model does not advertise structured outputs")
    return {
        "id": model_id,
        "pricing": pricing,
        "supported_parameters": model["supported_parameters"],
        "catalog_row_sha256": digest(model),
    }


def filtered_key(raw: dict) -> dict:
    data = raw.get("data")
    if not isinstance(data, dict) or type(data.get("is_free_tier")) is not bool:
        raise ValueError("Account tier information unavailable")
    return {
        k: data[k]
        for k in (
            "is_free_tier",
            "limit_remaining",
            "usage_daily",
            "rate_limit",
            "free_model_daily_requests",
        )
        if k in data
    }


def context_for(protocol: dict) -> ResearchContext:
    decision = instant(protocol["decision_at"])
    execution = instant(protocol["execution_at"])
    expiry = instant(protocol["expires_at"])
    if execution != instant("2026-10-26T13:30:00Z") or expiry != instant("2026-10-26T13:35:00Z"):
        raise ValueError("Synthetic next-session fixture times differ")
    return ResearchContext(
        decision_at=decision,
        account_observed_at=decision,
        execution_at=execution,
        execution_clock="next_session_open",
        expires_at=expiry,
        account_id="synthetic-shadow-probe-only",
        equity=Decimal("10000"),
        confirmed_cash=Decimal("9000"),
        restricted_cash=Decimal("0"),
        positions=(("QQQ", Decimal("10")),),
        evidence=tuple(
            MarketEvidence(s, "synthetic-" + s, decision, Decimal("100"), Decimal("100"))
            for s in ("QQQ", "SPY")
        ),
        authorized_intents=(),
        policy=ArmPolicy(),
    )


def schema_for(template: dict) -> dict:
    def fixed(value: Any) -> dict:
        if isinstance(value, dict):
            return {
                "type": "object",
                "properties": {k: fixed(v) for k, v in value.items()},
                "required": list(value),
                "additionalProperties": False,
            }
        if isinstance(value, bool):
            return {"type": "boolean", "enum": [value]}
        return {"type": "string", "enum": [value]}

    properties = {k: fixed(v) for k, v in template.items() if k != "actions"}
    properties["actions"] = {
        "type": "array",
        "minItems": 1,
        "maxItems": 1,
        "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["hold"]},
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            "required": ["kind", "reason"],
            "additionalProperties": False,
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(template),
        "additionalProperties": False,
    }


def request_for(protocol: dict) -> tuple[ResearchContext, ChatRequest]:
    if (
        not isinstance(protocol.get("expected_served_model"), str)
        or not protocol["expected_served_model"]
    ):
        raise ValueError("An exact served-model identity must be preregistered")
    context = context_for(protocol)
    template = hold_contract(context, model_id=protocol["model_id"], reason="Synthetic hold only")
    validate_action_contract(template, context)
    request = ChatRequest(
        system=(
            "Synthetic interface availability test only. You have no tools, broker access, "
            "actual account data, execution or promotion authority. Return the exact supplied "
            "bindings and one hold action with a brief reason. Never trade or change bindings."
        ),
        user=json.dumps(
            {
                "fixture": (
                    "SYNTHETIC: QQQ 10 shares at 100, cash 9000, NAV 10000; QQQ/SPY "
                    "synthetic marks 100 and past-observed proposal caps 100; all advanced "
                    "capabilities disabled. No real observations or performance evidence."
                ),
                "required_contract": template,
            },
            sort_keys=True,
        ),
        schema_json=json.dumps(schema_for(template), sort_keys=True),
        schema_name="synthetic_shadow_hold",
        max_output_tokens=2048,
        timeout_seconds=45,
        reasoning_effort="none",
        persistence="full",
    )
    return context, request


def reserve(campaign: Path, cycle: str, request: ChatRequest, protocol: dict) -> dict:
    """Reserve before dispatch; failures and interrupted attempts consume budget."""
    with (campaign / "free-model-budget.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = campaign / "free-model-calls.jsonl"
        records = (
            [json.loads(line) for line in ledger.read_text().splitlines()]
            if ledger.exists()
            else []
        )
        if (
            len(records) >= CAMPAIGN_CALLS
            or sum(r.get("cycle_id", "initial-foundation") == cycle for r in records) >= CYCLE_CALLS
        ):
            raise ValueError("Free-model dispatch budget exhausted")
        # A frozen synthetic probe gets exactly one attempt, including interruptions.
        if any(r.get("probe_protocol_sha256") == digest(protocol) for r in records):
            raise ValueError("Frozen probe already attempted; no retry")
        result = {
            "cycle_id": cycle,
            "attempt": len(records) + 1,
            "at": utcnow().isoformat(),
            "model": protocol["model_id"],
            "request_sha256": request.digest(),
            "probe_protocol_sha256": digest(protocol),
            "timeout_seconds": 45,
            "max_output_tokens": 2048,
            "paid_fallback": False,
            "purpose": "synthetic full action-contract availability only",
        }
        with ledger.open("a") as handle:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return result


def credential() -> str:
    value = os.environ.get("OPENROUTER_API_KEY")
    if value:
        return value
    try:
        from dotenv import dotenv_values  # noqa: PLC0415 -- optional existing credential loader
    except ImportError as exc:
        raise ValueError(
            "OpenRouter credential unavailable; optional dotenv loader absent"
        ) from exc
    repo = Path(__file__).resolve().parents[1]
    for name in (".env.local", ".env", ".env.production.local", ".env.production"):
        value = dotenv_values(repo / name).get("OPENROUTER_API_KEY")
        if value:
            return value
    raise ValueError("OpenRouter credential unavailable; no fallback")


def assert_time(protocol: dict, minimum: int = 0) -> None:
    stop = min(instant(protocol["campaign_deadline_utc"]), instant(protocol["hard_stop_utc"]))
    if (stop - utcnow()).total_seconds() <= minimum:
        raise ValueError("Probe deadline or bounded execution budget unavailable")


class NoRedirectTransport:
    """Driver-only one-call adapter; generic provider/router behavior is unchanged."""

    async def generate(
        self, request: ChatRequest, *, model: str, api_key: str, base_url: str
    ) -> ChatResponse:
        if base_url != BASE or request.json_object_mode or request.prompt_json_mode:
            raise ValueError("Only the frozen strict-schema endpoint is permitted")
        start = time.monotonic()
        try:
            async with AsyncOpenAI(
                api_key=api_key,
                base_url=BASE,
                max_retries=0,
                timeout=request.timeout_seconds,
                http_client=DefaultAsyncHttpxClient(follow_redirects=False),
            ) as client:
                completion = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": request.system},
                        {"role": "user", "content": request.user},
                    ],
                    max_tokens=request.max_output_tokens,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": request.schema_name,
                            "strict": True,
                            "schema": json.loads(request.schema_json),
                        },
                    },
                    extra_body={
                        "reasoning": {"effort": "none"},
                        "provider": {"allow_fallbacks": False, "require_parameters": True},
                    },
                )
        except APIStatusError as exc:
            # Generic router errors are shortened; this probe needs archival evidence.
            raw_body = exc.response.text
            encoded = raw_body.encode("utf-8")
            complete = len(encoded) <= MAX_BYTES
            retry_after = exc.response.headers.get("retry-after", "")
            retry_seconds = (
                float(retry_after)
                if re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,3})?", retry_after)
                else None
            )
            return ChatResponse(
                provider="openrouter",
                model=model,
                input_tokens=None,
                output_tokens=None,
                raw_json=json.dumps(
                    {
                        "status_code": exc.status_code,
                        "body_text": raw_body if complete else None,
                        "body_sha256": hashlib.sha256(encoded).hexdigest(),
                        "body_bytes": len(encoded),
                        "body_complete": complete,
                    }
                ),
                error_class=classify_http(exc.status_code, raw_body[:MAX_BYTES]),
                error_code=f"provider_http_{exc.status_code}"
                if complete
                else "provider_error_body_exceeds_bound",
                retry_after_seconds=retry_seconds,
                elapsed_seconds=time.monotonic() - start,
            )
        except Exception as exc:
            return _openai_exception_response(
                exc, provider="openrouter", model=model, api_key=api_key, start=start
            )
        return _finalize_chat(
            provider="openrouter", model=model, completion=completion, downgraded=False, start=start
        )


def billing_evidence(raw_json: str) -> dict:
    """Current catalog pricing is not a receipt for actual inference billing."""
    payload = json.loads(raw_json)
    usage = payload.get("usage") if isinstance(payload, dict) else None
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if cost is None:
        return {"status": "unavailable", "cost_usd": None}
    if type(cost) not in (str, int, float):
        raise ValueError("Invalid actual billed cost")
    amount = Decimal(str(cost))
    if not amount.is_finite() or amount != 0:
        raise ValueError("Actual billed cost is not verified zero")
    return {"status": "verified_zero", "cost_usd": str(amount)}


def probe(campaign: Path, cycle: str, protocol: dict, key: str) -> dict:
    """One model call maximum; metadata failures consume no inference reservation."""
    context, request = request_for(protocol)
    result: dict[str, Any] = {
        "schema": "free-shadow-probe-result/1",
        "protocol_sha256": digest(protocol),
        "request": request.model_dump(mode="json"),
        "request_sha256": request.digest(),
        "execution_authority": "none",
        "promotion_authority": False,
        "performance_evidence": False,
        "prospective_evidence": False,
        "fixture": "synthetic",
        "dispatches": 0,
        "reservation_consumed": False,
        "inference_attempted": False,
        "started_at": utcnow().isoformat(),
        "status": "preflight_failed",
    }
    try:
        assert_time(protocol, REQUIRED_SECONDS)
        result["catalog"] = verified_model(get_json("models"), protocol["model_id"])
        result["account_limits"] = filtered_key(get_json("key", key))
        daily = result["account_limits"].get("free_model_daily_requests")
        if daily is not None:
            if (
                not isinstance(daily, dict)
                or set(daily) != {"used", "limit", "remaining"}
                or any(type(v) is not int or v < 0 for v in daily.values())
            ):
                raise ValueError("Invalid free-model daily capacity")
            if daily["remaining"] <= 0:
                raise ValueError("Free-model daily capacity exhausted; no dispatch")
        assert_time(protocol, 60)
        result["reservation"] = reserve(campaign, cycle, request, protocol)
        result["reservation_consumed"] = True
        assert_time(protocol, 60)
        result["dispatches"] = 1
        result["inference_attempted"] = True
        result["inference_started_at"] = utcnow().isoformat()
        response = asyncio.run(
            asyncio.wait_for(
                NoRedirectTransport().generate(
                    request, model=protocol["model_id"], api_key=key, base_url=BASE
                ),
                timeout=60,
            )
        )
        result["response"] = response.model_dump(mode="json")
        result["inference_finished_at"] = utcnow().isoformat()
        result["billing"] = billing_evidence(response.raw_json)
        result["status"] = "provider_failed"
        if response.ok:
            if (
                not response.served_model
                or response.provider != "openrouter"
                or response.model != protocol["model_id"]
                or response.served_model != protocol["expected_served_model"]
            ):
                raise ValueError("Actual served/provider model identity differs or is unavailable")
            proposal = json.loads(response.text or "")
            if (
                proposal.get("model_id") != protocol["model_id"]
                or len(proposal.get("actions", [])) != 1
                or proposal["actions"][0].get("kind") != "hold"
            ):
                raise ValueError("Synthetic probe permits only pinned-model hold")
            result["validation"] = validate_action_contract(proposal, context)
            result["status"] = (
                "synthetic_contract_passed"
                if result["billing"]["status"] == "verified_zero"
                else "synthetic_contract_cost_unverified"
            )
    except Exception as exc:
        result["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        if result["dispatches"]:
            result["status"] = "dispatch_failed"
    result["completed_at"] = utcnow().isoformat()
    return scrub(result, key)


def verify_context(
    campaign: Path, protocol_path: Path, output: Path, cycle: str
) -> tuple[dict, dict]:
    state = json.loads((campaign / "state.json").read_text())
    active = state.get("active_run")
    if (
        not active
        or active.get("run_id") != os.environ.get("AMAT_RESEARCH_RUN_ID")
        or not os.environ.get("AMAT_RESEARCH_RUN_ID")
        or active.get("cycle_id") != cycle
    ):
        raise ValueError("Managed active run identity required")
    manifest = read_sealed(Path(active["manifest_path"]))
    campaign_protocol = read_sealed(campaign / "protocol.json")
    protocol = read_sealed(protocol_path)
    if (
        digest(manifest) != active["manifest_sha256"]
        or digest(campaign_protocol) != active["protocol_sha256"]
        or protocol["campaign_protocol_sha256"] != digest(campaign_protocol)
        or protocol.get("schema") != "free-shadow-probe/1"
    ):
        raise ValueError("Managed protocol/manifest identity mismatch")
    if protocol["campaign_deadline_utc"] != campaign_protocol["deadline_utc"]:
        raise ValueError("Campaign deadline differs")
    if (
        manifest.get("campaign_root") != str(campaign)
        or output.parent != Path(active["output"])
        or output.parent != campaign / "cycles" / cycle
        or output.resolve() != output
    ):
        raise ValueError("Output outside managed cycle")
    required = dict(protocol["source_files"])
    required[str(protocol_path)] = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    if str(Path(__file__).resolve()) not in required:
        raise ValueError("Probe driver not frozen")
    pins = {item["path"]: item["sha256"] for item in manifest["inputs"]}
    for raw, expected in required.items():
        path = Path(raw)
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            or pins.get(raw) != expected
        ):
            raise ValueError("Frozen source/manifest pin mismatch")
    library = Path(__file__).resolve().parents[1]
    sources = source_hashes(library)
    if any(required.get(str(library / raw)) != value for raw, value in sources.items()):
        raise ValueError("Entire native source set must be pinned")
    attest_loaded_sources(library / "src", sources, dict(sys.modules))
    assert_time(protocol)
    return protocol, active


def publish(path: Path, result: dict) -> None:
    """Exclusive publication, including seals; no overwrite or symlink following."""
    seal = path.with_suffix(".sha256")
    if path.exists() or path.is_symlink() or seal.exists() or seal.is_symlink():
        raise ValueError("Immutable output already present")
    for target, body in (
        (path, json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"),
        (seal, digest(result) + "\n"),
    ):
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("campaign", "protocol", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cycle-id", required=True)
    args = parser.parse_args()
    if not all(
        p.is_absolute() for p in (args.campaign, args.protocol, args.output)
    ) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", args.cycle_id):
        raise ValueError("Absolute paths and bounded cycle identity required")
    protocol, active = verify_context(args.campaign, args.protocol, args.output, args.cycle_id)
    try:
        key = credential()
    except Exception as exc:
        publish(
            args.output,
            {
                "status": "credential_unavailable",
                "failure_type": type(exc).__name__,
                "dispatches": 0,
                "execution_authority": "none",
                "promotion_authority": False,
            },
        )
        return
    result = probe(args.campaign, args.cycle_id, protocol, key)
    _, after = verify_context(args.campaign, args.protocol, args.output, args.cycle_id)
    if after != active:
        raise ValueError("Managed run changed during probe")
    publish(args.output, result)
    print(f"Free synthetic shadow probe: {result['status']}; no execution authority", flush=True)


if __name__ == "__main__":
    main()
