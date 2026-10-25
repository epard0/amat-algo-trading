"""Optional LLM commentary on a finished report. Narrative only, by contract.

Boundary, stated once and enforced by construction
--------------------------------------------------
The project rules forbid an LLM generating signal values, weights, fills, or
accounting entries. This module cannot violate that even in principle: it runs
*after* a study is complete, receives only aggregate statistics derived from the
finished artifacts, and its output is a string embedded in a report page that is
explicitly labelled as machine-generated interpretation. Nothing reads the
string back into any computation.

Credentials
-----------
Default calls use the shared free-first router and its environment credentials.
Explicit Gemini model overrides retain the legacy adapter. Credentials are never
retained in commentary or provenance.

Failure posture
---------------
An unreachable API, a missing key, or a refused request degrades to "no
advisory page, with the reason recorded" -- never to a crashed report. The
measured pages must not depend on a network call succeeding.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from amat.metrics import PerformanceSummary
from amat.reporting import PerformanceReport
from amat.reporting.insights import StrategyInsights

if TYPE_CHECKING:
    from amat.reporting.sleeves import SleeveReport

__all__ = ["DEFAULT_MODEL", "AdvisoryResult", "advisory_payload", "request_advisory"]

#: Shared utility policy; an explicit model retains the legacy Gemini adapter.
DEFAULT_MODEL = "auto"

_KEY_VARIABLE = "GEMINI_API_KEY"

_SYSTEM_INSTRUCTION = (
    "You are reviewing the measured results of a historical backtest of a daily, "
    "long-or-cash, volatility-targeted trend-following ETF strategy. You receive "
    "aggregate statistics only. Your task: (1) summarize where the strategy "
    "behaved as designed and where it paid known trend-following costs; (2) "
    "propose potential improvements WITH rationale, each framed strictly as a "
    "hypothesis that would require a new frozen strategy version and an "
    "adversarial falsification protocol (shuffled, turnover-matched-random, and "
    "static-mean controls) before any conclusion. Rules you must follow: never "
    "claim or imply the strategy is proven, safe, or profitable; never "
    "extrapolate measured past results into expected future returns; prefer "
    "specific, mechanistic reasoning tied to the numbers given; state "
    "uncertainty plainly; do not invent numbers that are not in the payload. "
    "Write plain text only -- no markdown syntax, no asterisks or hash "
    "headings; the output is typeset verbatim on a monospace report page."
)


@dataclass(frozen=True, slots=True)
class AdvisoryResult:
    """What the advisory request produced, or why it could not."""

    text: str | None
    provenance: str
    failure: str | None

    @property
    def available(self) -> bool:
        return self.text is not None


#: Episodes sent per category. A full decade produces hundreds of episodes and
#: an unbounded payload exceeded the API's per-minute input-token quota in one
#: request (observed live 2026-08-11: 250k-token limit, free tier). The counts
#: and totals summarize the rest, so nothing is silently hidden -- only detail
#: rows are capped.
_EPISODES_PER_CATEGORY = 12


def _episode_rows(episodes: Sequence[object], *, key: str) -> list[dict[str, object]]:
    """Top episodes by absolute magnitude, without the boilerplate note text."""
    ranked = sorted(episodes, key=lambda item: abs(float(getattr(item, key))), reverse=True)
    rows = []
    for episode in ranked[:_EPISODES_PER_CATEGORY]:
        row = episode.as_dict()  # type: ignore[attr-defined]
        row.pop("note", None)
        rows.append(row)
    return rows


def advisory_payload(
    report: PerformanceReport,
    metrics: PerformanceSummary,
    insights: StrategyInsights,
    sleeves: Sequence[SleeveReport] = (),
) -> dict[str, object]:
    """Aggregate statistics for the model: derived numbers only, no artifacts.

    Everything here already appears on the report's measured pages. No
    credentials, paths, or raw data rows are included -- by construction the
    payload is publishable, which is the correct bar for anything sent to an
    external service. Episode detail is bounded per category (the full counts
    and totals always ride along), keeping the request inside API token quotas.
    """
    return {
        "strategy": report.strategy,
        "engine": report.engine,
        "sample": {
            "first_session": report.first_session,
            "last_session": report.last_session,
            "sessions": report.sessions,
        },
        "cost_model_one_way_bps": report.one_way_bps,
        "performance": metrics.as_dict(),
        "annual_returns": dict(report.annual_returns),
        "concentration": {
            "best_symbol": report.concentration.best_symbol,
            "best_symbol_share": report.concentration.best_symbol_share,
            "symbol_gate_passed": report.concentration.symbol_gate_passed,
            "best_year": report.concentration.best_year,
            "best_year_share": report.concentration.best_year_share,
            "year_gate_passed": report.concentration.year_gate_passed,
        },
        "per_asset": [asset.as_dict() for asset in report.assets],
        "sleeves": [
            {
                "symbol": sleeve.symbol,
                "total_return": sleeve.total_return,
                "sharpe": sleeve.sharpe,
                "max_drawdown": sleeve.max_drawdown,
            }
            for sleeve in sleeves
            if sleeve.ran
        ],
        "episodes": {
            "counts": {
                "captured_trends": len(insights.captured_trends),
                "whipsaws": len(insights.whipsaws),
                "givebacks": len(insights.givebacks),
                "missed_rallies": len(insights.missed_rallies),
            },
            "whipsaw_total_cost": insights.whipsaw_total_cost,
            "giveback_total_surrendered": insights.giveback_total_surrendered,
            "detail_rows_are_capped_at": _EPISODES_PER_CATEGORY,
            "top_captured_trends": _episode_rows(insights.captured_trends, key="net_pnl"),
            "worst_whipsaws": _episode_rows(insights.whipsaws, key="net_pnl"),
            "largest_givebacks": _episode_rows(insights.givebacks, key="peak_open_pnl"),
            "largest_missed_rallies": [
                rally.as_dict() for rally in insights.missed_rallies[:_EPISODES_PER_CATEGORY]
            ],
        },
        "caveats": list(report.caveats),
    }


def request_advisory(
    payload: Mapping[str, object],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    attempts: int = 3,
    retry_sleep_seconds: float = 5.0,
) -> AdvisoryResult:
    """Request commentary through shared routing or an explicit Gemini override.

    Failures are retried up to ``attempts`` times at this layer because the
    SDK's own retry predicate covers timeouts and connect errors but not every
    transient transport failure (a proxy's 502, observed live on 2026-08-11,
    surfaces as ``httpx.ProxyError`` and is not retried by the SDK).

    Returns:
        The commentary with its provenance line, or ``text=None`` with the
        last recorded reason (missing key, unreachable host, SDK error, empty
        response).
    """
    if model == "auto":
        return _routed_advisory(payload)
    credential = os.environ.get(_KEY_VARIABLE, "")
    if not credential:
        return AdvisoryResult(
            text=None,
            provenance="",
            failure=f"{_KEY_VARIABLE} is not set in the environment; no request was made",
        )

    def scrub(message: str) -> str:
        # Same posture as newsdesk.clients.base: the one reason to hold the
        # value is to guarantee it cannot leave through an error message.
        return message.replace(credential, f"[REDACTED:{_KEY_VARIABLE}]")

    prompt = "Aggregate backtest statistics follow as JSON. Provide your review.\n\n" + json.dumps(
        payload, indent=2, sort_keys=True, default=str
    )

    failure = "no attempt was made"
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            # Deferred import: the SDK pulls in httpx and friends, which `amat
            # --version` and every offline command should not pay for.
            from google import genai  # noqa: PLC0415
            from google.genai import types as genai_types  # noqa: PLC0415

            client = genai.Client()
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    temperature=temperature,
                ),
            )
            text = response.text
        except Exception as exc:
            failure = scrub(f"{type(exc).__name__}: {exc} (attempt {attempt}/{attempts})")
            if attempt < attempts:
                time.sleep(retry_sleep_seconds)
            continue

        if not text or not text.strip():
            failure = f"the model returned an empty response (attempt {attempt}/{attempts})"
            if attempt < attempts:
                time.sleep(retry_sleep_seconds)
            continue

        requested_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
        return AdvisoryResult(
            text=text.strip(),
            provenance=f"model {model}, requested {requested_at}",
            failure=None,
        )

    return AdvisoryResult(text=None, provenance="", failure=failure)


def _routed_advisory(payload: Mapping[str, object]) -> AdvisoryResult:
    """Default utility routing; explicit Gemini calls retain compatibility."""
    from llmrouter.contracts import ChatRequest  # noqa: PLC0415
    from llmrouter.policy import POLICY_VERSION  # noqa: PLC0415
    from llmrouter.utility import complete_json  # noqa: PLC0415

    request = ChatRequest(
        system=_SYSTEM_INSTRUCTION + ' Return a JSON object with a nonempty "commentary" string.',
        user=json.dumps(payload, sort_keys=True, default=str),
        schema_json=json.dumps(
            {
                "type": "object",
                "properties": {"commentary": {"type": "string"}},
                "required": ["commentary"],
                "additionalProperties": False,
            }
        ),
        max_output_tokens=4096,
        timeout_seconds=60,
        persistence="metadata_only",
    )

    def validate(text: str) -> str:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("invalid commentary object")
        commentary = value.get("commentary")
        if not isinstance(commentary, str) or not commentary.strip():
            raise ValueError("missing commentary")
        return commentary.strip()

    try:
        response, calls = complete_json(request, validate=validate)
    except Exception as exc:
        return AdvisoryResult(None, "", f"routing failed: {type(exc).__name__}")
    provenance = json.dumps({"policy": POLICY_VERSION, "attempts": calls}, sort_keys=True)
    if response is None:
        return AdvisoryResult(
            None, provenance, "No configured model route produced valid commentary"
        )
    return AdvisoryResult(validate(response.text or ""), provenance, None)
