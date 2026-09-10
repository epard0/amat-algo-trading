"""Shared read-only HTTP machinery for news vendors.

This is the only module in ``newsdesk`` that touches a socket. Everything above
it -- the vendor clients, the ingest orchestration, the store -- handles bytes
and dictionaries.

The credential-in-the-URL problem
---------------------------------
Marketaux authenticates with a query parameter, so the secret travels inside the
URL itself. That makes every error path a potential leak: an exception that
echoes the URL, a log line that records the request, a vendor error message that
reflects the caller's parameters back. Three rules close it:

* error messages name the *path* only, never the query string;
* every vendor-derived string that enters a message passes through
  :meth:`ApiCredential.scrub`, which replaces the exact secret value wherever it
  appears -- including inside a URL the vendor reflected back;
* the credential's ``repr`` is redacted, so a stack trace that renders the
  client cannot render the secret.

Fail-closed transport
---------------------
A truncated body, an oversized body, a non-JSON body, and a non-object JSON body
each raise rather than returning something partial: guardrail 9 forbids treating
a damaged response as coverage. Retries apply only to statuses that are
transient by contract (429 and the 5xx family); a 401/402/403 is a fact about
the account, not the weather, and repeating it would burn quota for nothing.
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from newsdesk.errors import ConfigurationError, VendorResponseError

__all__ = ["ApiCredential", "FetchedPage", "ReadOnlyJsonClient"]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A 3xx is a terminal answer, never something to follow.

    The default opener follows redirects across hosts and carries the request
    headers with it -- including an ``X-API-Key`` -- so a single
    vendor-controlled ``302 Location: https://collector.example`` would defeat
    the host pin and hand the credential to an arbitrary server. Returning
    ``None`` makes ``urlopen`` surface the 3xx as an ``HTTPError``, which the
    retry logic treats as non-retryable and reports by status code.
    """

    def redirect_request(
        self,
        _req: urllib.request.Request,
        _fp: object,
        _code: int,
        _msg: str,
        _headers: object,
        _newurl: str,
    ) -> None:
        return None


#: Module-level opener so every client shares the no-redirect policy; using
#: bare ``urlopen`` would silently reinstate the default redirect-following.
_OPENER = urllib.request.build_opener(_RefuseRedirects())

#: Hard ceiling on one response body. The raw payload archive stores every page
#: verbatim, so an unbounded read is an unbounded database. 8 MB is two orders
#: of magnitude above a normal news page.
MAX_RESPONSE_BYTES = 8_000_000

#: Transient by contract: rate limiting and server-side failure.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 5

#: Self-throttle ceiling. Both vendors document per-minute limits at or above
#: this; staying under it means a 429 indicates a quota problem, not a pacing
#: one. Note it cannot protect *monthly* quotas (Adanos free tier: 250
#: requests/month) -- only restraint in what is requested can.
_REQUESTS_PER_MINUTE = 60

#: The rolling window the per-minute allowance is measured over.
_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class ApiCredential:
    """A secret held in memory, named by its environment variable.

    The variable *name* is public and appears in error messages; the value never
    does. ``scrub`` exists because the value can end up embedded in strings this
    package did not build -- a reflected URL in a vendor error, most likely --
    and replacing the exact value is the one redaction that cannot miss.
    """

    variable: str
    value: str = field(repr=False)

    @classmethod
    def from_environment(cls, variable: str) -> ApiCredential:
        """Load by name, failing with a message that names only the variable."""
        raw = os.environ.get(variable)
        if not raw:
            message = (
                f"missing credential: {variable} is not set. Export it in your shell or "
                "secret manager; this repository never stores credential values."
            )
            raise ConfigurationError(message)
        return cls(variable=variable, value=raw)

    def scrub_bytes(self, data: bytes) -> bytes:
        """Scrub a response body before anything downstream can persist it.

        The raw-payload archive stores pages verbatim and forever. A vendor
        that echoes the request URL into a response body (an error page, a
        debug field) would therefore write the query-string credential to disk
        permanently. Scrubbing the exact value -- in plain and percent-encoded
        forms -- at the transport boundary means "verbatim" excludes exactly
        one thing: our own secret.
        """
        for form in (
            self.value,
            urllib.parse.quote(self.value, safe=""),
            urllib.parse.quote_plus(self.value),
        ):
            data = data.replace(form.encode("utf-8"), b"<redacted>")
        return data

    def scrub(self, text: str) -> str:
        """Remove every occurrence of the secret from ``text`` -- in every encoding
        it travels in.

        The raw value alone is not enough. The token rides in a query string,
        where ``urlencode`` percent-escapes it, so a vendor that reflects the
        request URL back in an error message reflects the *encoded* form -- and
        ``replace(raw)`` walks straight past it. Measured 2026-08-11: a token
        containing ``+/=`` survived a raw-value scrub untouched inside a
        reflected URL. Every encoding the transport can produce is scrubbed.
        """
        scrubbed = text.replace(self.value, "<redacted>")
        for encoded in (
            urllib.parse.quote(self.value, safe=""),
            urllib.parse.quote_plus(self.value),
        ):
            if encoded != self.value:
                scrubbed = scrubbed.replace(encoded, "<redacted>")
        return scrubbed

    def __repr__(self) -> str:
        return f"ApiCredential(variable={self.variable!r}, value=<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """One page as received: parsed for the caller, verbatim for the archive."""

    number: int
    payload: dict[str, Any]
    raw: bytes


class ReadOnlyJsonClient:
    """GET-only JSON client with a pinned host and a path allowlist."""

    def __init__(
        self,
        *,
        host: str,
        credential: ApiCredential,
        additional_credentials: tuple[ApiCredential, ...] = (),
        permitted_prefixes: tuple[str, ...],
        denied_prefixes: tuple[str, ...] = (),
        timeout_seconds: float = 30.0,
        retry_sleep_scale: float = 1.0,
        requests_per_minute: int = _REQUESTS_PER_MINUTE,
    ) -> None:
        #: Multiplier on retry backoff. 1.0 in production; tests set 0.0 so an
        #: exhausted-retry case takes milliseconds instead of 31 real seconds.
        self._retry_sleep_scale = retry_sleep_scale
        #: Per-minute request allowance. Production default protects the
        #: vendor; tests raise it so a 200-page pagination case is not obliged
        #: to spend real minutes proving it stops at page 200.
        self._requests_per_minute = requests_per_minute
        self._host = host
        self._credential = credential
        self._credentials = (credential, *additional_credentials)
        self._permitted = permitted_prefixes
        self._denied = denied_prefixes
        self._timeout = timeout_seconds
        self._request_times: list[float] = []

    # -- path policy --------------------------------------------------------

    def _check_path(self, path: str) -> None:
        """Deny first, then require an allowlisted prefix.

        The deny list exists for route families that are known to exist but must
        never be reachable even if a future edit widens the allowlist -- Adanos
        documents an internal ``/auth/v1/*`` family whose contents are
        deliberately unpublished, so the deny is by prefix rather than endpoint.
        """
        if any(path.startswith(prefix) for prefix in self._denied):
            message = f"refusing a request to a denied route family: {path!r}"
            raise ConfigurationError(message)
        if not path.startswith(self._permitted):
            message = f"refusing a request outside the read-only surface: {path!r}"
            raise ConfigurationError(message)

    # -- pacing -------------------------------------------------------------

    def _throttle(self) -> None:
        """Block until another request fits inside the per-minute allowance."""
        now = time.monotonic()
        self._request_times = [
            stamp for stamp in self._request_times if now - stamp < _WINDOW_SECONDS
        ]
        if len(self._request_times) >= self._requests_per_minute:
            time.sleep(max(0.0, _WINDOW_SECONDS - (now - self._request_times[0])) + 0.05)
            self._throttle()
            return
        self._request_times.append(now)

    # -- transport ----------------------------------------------------------

    def _transport(self, url: str, headers: Mapping[str, str]) -> bytes:
        """One GET, returning the body bytes. Overridden by tests with canned data.

        The method is a literal here and nowhere else in the package;
        ``tests/unit/test_newsdesk_isolation.py`` asserts no mutating verb
        literal exists anywhere in ``newsdesk``, so this client cannot grow a
        write path without failing the suite.
        """
        request = urllib.request.Request(  # noqa: S310 - scheme/host pinned by the caller
            url, headers=dict(headers), method="GET"
        )
        with _OPENER.open(request, timeout=self._timeout) as response:
            body: bytes = response.read(MAX_RESPONSE_BYTES + 1)
        return body

    def _get_json(
        self,
        path: str,
        params: Mapping[str, str | int],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        """Issue one GET and return ``(decoded object, verbatim bytes)``.

        Raises:
            ConfigurationError: for a path outside the allowlist.
            VendorResponseError: for a non-retryable HTTP status, exhausted
                retries, an oversized body, or a body that is not a JSON object.
                Messages carry the path and status, never the query string, and
                every vendor-derived fragment is scrubbed of the credential.
        """
        self._check_path(path)
        url = f"https://{self._host}{path}?{urllib.parse.urlencode(params)}"
        request_headers = {"Accept": "application/json", **(headers or {})}

        last_error = ""
        for attempt in range(_MAX_ATTEMPTS):
            self._throttle()
            try:
                raw = self._transport(url, request_headers)
            except urllib.error.HTTPError as exc:
                detail = self._error_detail(exc)
                if exc.code not in _RETRY_STATUSES:
                    message = f"request to {path} failed: HTTP {exc.code}{detail}"
                    raise VendorResponseError(self._scrub(message)) from None
                last_error = f"HTTP {exc.code}{detail}"
            except (
                http.client.HTTPException,
                urllib.error.URLError,
                TimeoutError,
                OSError,
            ) as exc:
                last_error = self._scrub(f"{type(exc).__name__}: {exc}")
            else:
                raw = self._scrub_bytes(raw)
                if len(raw) > MAX_RESPONSE_BYTES:
                    # Enforced here rather than in _transport: the cap is policy,
                    # and policy inside a substitutable transport method is
                    # policy that vanishes in every test double and subclass.
                    message = f"response from {path} exceeded the {MAX_RESPONSE_BYTES}-byte cap"
                    raise VendorResponseError(message)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    # Indistinguishable from a truncated transfer at this layer;
                    # discard and retry rather than parsing a partial body.
                    last_error = "undecodable body"
                else:
                    if not isinstance(payload, dict):
                        message = f"request to {path} returned a non-object JSON body"
                        raise VendorResponseError(message)
                    return payload, raw
            time.sleep(min(2.0**attempt, 30.0) * self._retry_sleep_scale)

        message = (
            f"request to {path} failed after {_MAX_ATTEMPTS} attempts: "
            f"{self._scrub(last_error)}"
        )
        raise VendorResponseError(message)

    def _scrub(self, value: str) -> str:
        """Redact every credential held by a multi-header client."""
        for credential in self._credentials:
            value = credential.scrub(value)
        return value

    def _scrub_bytes(self, value: bytes) -> bytes:
        """Redact every credential before raw vendor bytes can be archived."""
        for credential in self._credentials:
            value = credential.scrub_bytes(value)
        return value

    def _error_detail(self, exc: urllib.error.HTTPError) -> str:
        """Extract the vendor's error code from an error body, safely.

        Both vendors document a JSON error envelope. The code (e.g.
        ``usage_limit_reached``) turns an opaque 402 into an actionable message.
        The body is untrusted: bounded read, tolerant parse, scrubbed output,
        and only short printable fragments survive.
        """
        try:
            body = exc.read(4096)
        except (OSError, AttributeError):
            return ""
        try:
            envelope = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ""
        if not isinstance(envelope, dict):
            return ""
        error = envelope.get("error")
        if not isinstance(error, dict):
            return ""
        code = error.get("code")
        message = error.get("message")
        parts = []
        if isinstance(code, str) and code:
            parts.append(self._scrub(code[:64]))
        if isinstance(message, str) and message:
            parts.append(self._scrub(message[:200]))
        return f" ({': '.join(parts)})" if parts else ""
