"""Provider-neutral request/response contracts and HTTP error classification.

Mirrors ``portfolio_team.contracts.Record`` (frozen, extra-forbidding, digestible
pydantic models) but does not import it: ``portfolio_team`` depends on
``llmrouter``, never the reverse.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

ProviderName = Literal["openai", "gemini", "openrouter", "nvidia"]

ErrorClass = Literal[
    "ok",
    "rate_limit",
    "quota",
    "auth",
    "timeout",
    "transport",
    "server",
    "malformed",
    "refusal",
    "no_credential",
]


class Record(BaseModel):
    """Reject unknown fields and nonfinite numbers at persistence boundaries."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, revalidate_instances="always"
    )

    def canonical(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()


# ``schema_json`` shadows ``BaseModel.schema_json``, a deprecated pydantic v1
# compatibility method; the interface requires this exact field name (Task 5
# brief), so the spurious warning -- fatal under this repo's
# ``filterwarnings = ["error"]`` -- is suppressed only for this class body.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message='Field name "schema_json"', category=UserWarning)

    class ChatRequest(Record):
        system: str
        user: str
        schema_json: str  # type: ignore[assignment]  # shadows BaseModel.schema_json; see note above
        schema_name: str = "structured_output"
        max_output_tokens: int = Field(ge=256, le=65536)
        timeout_seconds: float = Field(gt=0, le=600)
        reasoning_effort: str | None = None


class ChatResponse(Record):
    provider: str
    model: str
    text: str | None = None
    raw_json: str = "{}"
    input_tokens: int | None
    output_tokens: int | None
    error_code: str | None = None
    error_class: ErrorClass = "ok"
    retry_after_seconds: float | None = None
    elapsed_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error_class == "ok"


_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_REQUEST_TIMEOUT = 408
_HTTP_SERVER_ERROR_FLOOR = 500
_QUOTA_MARKERS = ("insufficient_quota", "exceeded your current quota")


def classify_http(status: int, body: str) -> ErrorClass:
    """Map an HTTP status/body pair from a provider SDK to an :data:`ErrorClass`.

    Providers do not agree on status codes for the same failure (a missing
    model is a 404 for some, a 400 for others), so the body is inspected only
    where the status code alone is ambiguous.
    """
    lowered = body.lower()
    if status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
        return "auth"
    if status == _HTTP_NOT_FOUND:
        return "auth" if "model" in lowered else "transport"
    if status == _HTTP_TOO_MANY_REQUESTS:
        return "quota" if any(marker in lowered for marker in _QUOTA_MARKERS) else "rate_limit"
    if status == _HTTP_REQUEST_TIMEOUT:
        return "timeout"
    if status >= _HTTP_SERVER_ERROR_FLOOR:
        return "server"
    return "transport"


class Transport(Protocol):
    async def generate(
        self, request: ChatRequest, *, model: str, api_key: str, base_url: str
    ) -> ChatResponse: ...
