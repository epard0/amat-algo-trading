"""Metadata-only model diagnostics derived from durable research records."""

from __future__ import annotations

import json
from html import escape
from typing import Any


def model_diagnostics(telemetry: dict[str, Any], *, active: bool) -> dict[str, Any]:
    starts, finishes, routes = {}, {}, {}
    for event in telemetry["events"]:
        payload = json.loads(event["payload"])
        if event["kind"] == "model_call_started":
            starts[payload["call_id"]] = {**payload, "started_at": event["recorded_at"]}
        elif event["kind"] == "model_call_finished":
            finishes[payload["call_id"]] = payload
        elif event["kind"] == "route_attempt":
            routes[payload["call_id"]] = payload
    models: dict[tuple[str, str], dict[str, Any]] = {}
    timeline = []
    for call in telemetry["calls"]:
        response = json.loads(call["response"]) if call["response"] else {}
        request = json.loads(call["request"])
        start, finish, route = (
            starts.get(call["id"], {}),
            finishes.get(call["id"], {}),
            routes.get(call["id"], {}),
        )
        status = response.get("error_class", "pending" if active else "interrupted")
        elapsed = finish.get("elapsed_seconds", response.get("elapsed_seconds"))
        item = {
            "call_id": call["id"],
            "trace_id": route.get("trace_id"),
            "attempt": route.get("attempt"),
            "provider": call["provider"],
            "requested_model": call["model"],
            "served_model": response.get("served_model"),
            "purpose": start.get("purpose", request.get("schema_name", "not_recorded")),
            "task_class": start.get("task_class", request.get("task_class", "routine")),
            "experiment": start.get("experiment"),
            "phase": start.get("phase"),
            "started_at": start.get("started_at"),
            "elapsed_seconds": elapsed,
            "status": status,
            "error_code": finish.get("error_code", response.get("error_code")),
            "input_tokens": response.get("input_tokens"),
            "output_tokens": response.get("output_tokens"),
            "charged_tokens": call["charged"],
        }
        timeline.append(item)
        key = (call["provider"], call["model"])
        group = models.setdefault(
            key,
            {
                "provider": key[0],
                "model": key[1],
                "calls": 0,
                "outcomes": {},
                "known_input_tokens": 0,
                "known_output_tokens": 0,
                "unknown_usage_calls": 0,
                "charged_tokens": 0,
                "latency_samples": 0,
                "total_elapsed_seconds": 0.0,
            },
        )
        group["calls"] += 1
        group["outcomes"][status] = group["outcomes"].get(status, 0) + 1
        group["charged_tokens"] += call["charged"]
        group["known_input_tokens"] += response.get("input_tokens") or 0
        group["known_output_tokens"] += response.get("output_tokens") or 0
        group["unknown_usage_calls"] += int(
            response.get("input_tokens") is None or response.get("output_tokens") is None
        )
        if elapsed is not None:
            group["latency_samples"] += 1
            group["total_elapsed_seconds"] += elapsed
    for group in models.values():
        group["mean_elapsed_seconds"] = (
            group["total_elapsed_seconds"] / group["latency_samples"]
            if group["latency_samples"]
            else None
        )
    route_events = [
        {
            "recorded_at": event["recorded_at"],
            "kind": event["kind"],
            "payload": json.loads(event["payload"]),
        }
        for event in telemetry["events"]
        if event["kind"].startswith("route_")
    ]
    return {
        "models": list(models.values()),
        "call_timeline": timeline,
        "routing_events": route_events,
    }


def agent_panel(report: dict[str, Any]) -> str:
    """Escaped report snapshot; expandable records avoid exposing raw prompts."""
    monitor = report.get("analysis", {}).get("monitoring", {})
    rows = []
    for model in monitor.get("models", []):
        values = [
            model["provider"],
            model["model"],
            model["calls"],
            ", ".join(f"{key}: {value}" for key, value in model["outcomes"].items()),
            model["charged_tokens"],
            model["unknown_usage_calls"],
            model["mean_elapsed_seconds"],
        ]
        rows.append("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in values) + "</tr>")
    calls = []
    for call in reversed(monitor.get("call_timeline", [])):
        label = (
            f"Call {call['call_id']} · {call['purpose']} · "
            f"{call['requested_model']} · {call['status']}"
        )
        calls.append(
            "<details><summary>"
            + escape(label)
            + "</summary><pre>"
            + escape(json.dumps(call, indent=2))
            + "</pre></details>"
        )
    route_details = escape(json.dumps(monitor.get("routing_events", []), indent=2))
    judgments = []
    for record in report.get("research_judgments", []):
        label = f"Experiment {record['experiment']} · {record['stage']} · {record['status']}"
        judgments.append(
            "<details><summary>"
            + escape(label)
            + "</summary><pre>"
            + escape(json.dumps(record, indent=2))
            + "</pre></details>"
        )
    return (
        '<section class="card"><h2>Agent activity and research judgment</h2>'
        "<p>Recorded snapshot · judgments are advisory. Unknown usage is not zero cost.</p>"
        '<div class="table"><table><thead><tr><th>Provider</th><th>Model</th><th>Calls</th>'
        "<th>Outcomes</th><th>Charged tokens</th><th>Unknown usage</th><th>Mean seconds</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        "<details><summary>Correlated model call timeline</summary>" + "".join(calls) + "</details>"
        "<details><summary>Routing, skips, retries, and exhaustion</summary><pre>"
        + route_details
        + "</pre></details>"
        + "<details><summary>Research critiques and outcome judgments</summary>"
        + "".join(judgments)
        + "</details></section>"
    )
