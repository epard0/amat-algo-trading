"""RAM-only retrieval with explicit, bounded excerpt admission.

Admission establishes source provenance and exact quotation, not factual truth.
Retrieved text is untrusted data and cannot become a tool instruction.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, override

from llmrouter.costs import CostLedger

MAX_BYTES = 128 * 1024
MAX_EXCERPT = 1000
MAX_CLAIMS = 20


class NoRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def retrieve(url: str, *, allowed_hosts: frozenset[str]) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("source URL is not allowlisted HTTPS")
    costs = CostLedger()
    identity = costs.reserve(category="search", provider=parsed.hostname or "")
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(url, timeout=20) as response:
            body: bytes = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("source exceeds byte bound")
        text = body.decode("utf-8", errors="strict")
    except Exception:
        costs.settle(identity, status="failed", usage={"requests": 1, "usd": None})
        raise ValueError("web evidence retrieval failed") from None
    costs.settle(identity, status="ok", usage={"requests": 1, "usd": None})
    return text


def admit(
    text: str,
    *,
    url: str,
    excerpt: str,
    claim: str,
    fetched_at: dt.datetime,
    now: dt.datetime,
    max_age: dt.timedelta = dt.timedelta(days=7),
) -> dict[str, Any]:
    if (
        fetched_at.tzinfo is None
        or now.tzinfo is None
        or not dt.timedelta(0) <= now - fetched_at <= max_age
    ):
        raise ValueError("stale or invalid source time")
    if not excerpt.strip() or len(excerpt) > MAX_EXCERPT or excerpt not in text:
        raise ValueError("excerpt must match a bounded source passage")
    if claim.strip() != excerpt.strip() or excerpt == text:
        raise ValueError("admission requires a source excerpt, not a paraphrase or full page")
    if len(text.encode()) > MAX_BYTES:
        raise ValueError("source exceeds byte bound")
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {
        "schema": "admitted-evidence/1",
        "url": url,
        "source_sha256": digest,
        "excerpt": excerpt,
        "claim": claim,
        "fetched_at": fetched_at.isoformat(),
        "admitted_at": now.isoformat(),
        "admission": "exact_excerpt_provenance_only",
        "truth_verified": False,
        "execution_authority": "none",
    }


def persist_admitted(path: Path, records: list[dict[str, Any]]) -> None:
    allowed = {
        "schema",
        "url",
        "source_sha256",
        "excerpt",
        "claim",
        "fetched_at",
        "admitted_at",
        "admission",
        "truth_verified",
        "execution_authority",
    }
    if len(records) > MAX_CLAIMS or any(set(row) != allowed for row in records):
        raise ValueError("invalid admitted evidence projection")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(json.dumps(records, sort_keys=True, allow_nan=False))
