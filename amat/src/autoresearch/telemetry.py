"""Recorded-run diagnostics and analysis exports; never selection authority."""

from __future__ import annotations

import csv
import datetime as dt
import fcntl
import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from amat.reporting.trade_monitor import SCHEMA as TRADE_SCHEMA
from amat.reporting.trade_monitor import document, write_monitor
from autoresearch.failures import export_failures
from autoresearch.observability import model_diagnostics
from autoresearch.periods import period_reports
from autoresearch.workspace import digest_file, write_json

MIN_DAILY_RECORDS = 2
RETURN_TOLERANCE = 1e-12


@contextmanager
def analysis_lock(root: Path) -> Iterator[None]:
    with (root / "writer.lock").open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "analysis exports update automatically during a run; retry after it finishes"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def daily_metrics(daily: list[dict[str, Any]]) -> dict[str, Any]:
    if len(daily) < MIN_DAILY_RECORDS:
        return {"available": False, "reason": "daily account history unavailable"}
    equity = np.array([float(row["equity"]) for row in daily])
    if not np.isfinite(equity).all() or np.any(equity <= 0):
        raise ValueError("invalid equity history in analysis input")
    dates = [row["session"] for row in daily]
    if dates != sorted(set(dates)):
        raise ValueError("analysis sessions must be unique and chronological")
    returns = equity[1:] / equity[:-1] - 1
    if not np.allclose(
        returns, [row["return"] for row in daily[1:]], atol=RETURN_TOLERANCE, rtol=1e-10
    ):
        raise ValueError("recorded returns disagree with equity")
    downside = float(np.sqrt(np.mean(np.minimum(returns, 0) ** 2)) * np.sqrt(252))
    drawdown = equity / np.maximum.accumulate(equity) - 1
    longest = current = 0
    for value in drawdown:
        current = current + 1 if value < -RETURN_TOLERANCE else 0
        longest = max(longest, current)
    annual = float((equity[-1] / equity[0]) ** (252 / len(returns)) - 1)
    cutoff = float(np.quantile(returns, 0.05))
    # Average exactly 5% of empirical probability mass, including fractional
    # boundary weight. A conditional <= quantile mean dilutes tails with ties.
    ordered = np.sort(returns)
    tail_size = len(returns) * 0.05
    whole = int(tail_size)
    tail_mean = (ordered[:whole].sum() + (tail_size - whole) * ordered[whole]) / tail_size
    turnover = None
    if all("fills" in row for row in daily[1:]):
        turnover = (
            sum(
                sum(abs(float(fill["price"]) * float(fill["quantity"])) for fill in row["fills"])
                / previous
                for previous, row in zip(equity[:-1], daily[1:], strict=True)
            )
            * 252
            / len(returns)
        )
    return {
        "available": True,
        "return_observations": len(returns),
        "sortino_mar0": float(returns.mean() * 252 / downside) if downside > 0 else None,
        "calmar": annual / abs(float(drawdown.min())) if drawdown.min() < 0 else None,
        "daily_var95_loss": max(0.0, -cutoff),
        "daily_expected_shortfall95_loss": max(0.0, -float(tail_mean)),
        "worst_day": float(returns.min()),
        "best_day": float(returns.max()),
        "longest_underwater_sessions": longest,
        "annualized_one_way_turnover": turnover,
    }


def read_telemetry(root: Path) -> dict[str, Any]:
    """No writer lock or Ledger constructor: safe while a research process is active."""
    with sqlite3.connect(f"file:{root / 'research.sqlite3'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")  # Read state, calls, and events from one consistent snapshot.
        state = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM state")}
        calls = [dict(row) for row in db.execute("SELECT * FROM calls ORDER BY id")]
        events = (
            [dict(row) for row in db.execute("SELECT * FROM events ORDER BY id")]
            if db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
            ).fetchone()
            else []
        )
        experiments = [dict(row) for row in db.execute("SELECT * FROM experiments ORDER BY id")]
    return {"state": state, "calls": calls, "events": events, "experiments": experiments}


def monitoring(telemetry: dict[str, Any], root: Path) -> dict[str, Any]:
    state, calls = telemetry["state"], telemetry["calls"]
    progress = state.get("progress", {})
    phase_age = None
    if progress.get("started_at"):
        phase_age = max(
            0.0,
            (
                dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(progress["started_at"])
            ).total_seconds(),
        )
    known_input = known_output = unknown = errors = pending = 0
    for call in calls:
        response = json.loads(call["response"]) if call["response"] else {}
        known_input += response.get("input_tokens") or 0
        known_output += response.get("output_tokens") or 0
        unknown += int(
            response.get("input_tokens") is None or response.get("output_tokens") is None
        )
        pending += int(not call["response"])
        errors += int(bool(response) and response.get("error_class") != "ok")
    active = False
    if (root / "writer.lock").exists():
        with (root / "writer.lock").open("r") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
            except BlockingIOError:
                active = True
    status = state.get("status", "not_recorded")
    if status == "running" and not active:
        status = "interrupted_or_inactive"
    return {
        "status": status,
        "writer_active": active,
        "phase": progress.get("phase", "not_recorded"),
        "phase_started_at": progress.get("started_at"),
        "phase_elapsed_seconds": phase_age if status == "running" else None,
        "experiment": progress.get("experiment"),
        "provider_calls": len(calls),
        "provider_errors": errors,
        "pending_calls": pending,
        "known_input_tokens": known_input,
        "known_output_tokens": known_output,
        "unknown_usage_calls": unknown,
        "budget_charged_tokens": sum(call["charged"] for call in calls),
        "event_count": len(telemetry["events"]),
        **model_diagnostics(telemetry, active=active),
    }


def csv_value(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        try:
            if math.isfinite(float(value)):
                return value
        except ValueError:
            pass
        return "'" + value
    return value


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = {
        "strategies": ["decision", "score", "primary_annualized_return"],
        "daily": ["variant", "session", "equity", "return", "is_anchor"],
        "fills": ["variant", "session", "symbol", "side", "quantity", "price", "cost_cash"],
        "missed": ["variant", "session", "symbol", "reason"],
        "folds": ["year", "annualized_return", "sharpe"],
        "scenarios": ["scenario", "kind", "role", "sharpe"],
        "timings": ["phase", "started_at", "recorded_at", "elapsed_seconds", "interrupted"],
    }
    base = ["program_sha256", "experiment_id", "candidate_id", *columns.get(path.stem, [])]
    if path.stem == "providers":
        base = ["call_id", "provider", "model", "status", "input_tokens", "output_tokens"]
    fields = list(dict.fromkeys([*base, *(key for row in rows for key in row)]))
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            # Prevent spreadsheet formula execution from model-authored prose.
            safe = {key: csv_value(value) for key, value in row.items()}
            writer.writerow(safe)
    temporary.replace(path)


def export_analysis(  # noqa: PLR0912, PLR0915 - distinct verified analysis exports
    root: Path, report: dict[str, Any], telemetry: dict[str, Any]
) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "strategies",
            "daily",
            "periods",
            "fills",
            "missed",
            "folds",
            "scenarios",
            "providers",
            "timings",
        )
    }
    inputs: dict[str, str] = {}
    diagnostics = []
    trade_events: dict[tuple[str, str, str], dict[str, Any]] = {}
    trade_inputs = {}
    for row in report["experiments"]:
        if not row["result"]:
            continue
        result = json.loads(row["result"])
        for relative, expected in result.get("artifact_hashes", {}).items():
            path = root / relative
            if not path.resolve().is_relative_to(root.resolve()) or digest_file(path) != expected:
                raise ValueError("analysis evidence changed: " + relative)
            inputs[relative] = expected
        identity = {
            "program_sha256": report["program_sha256"],
            "experiment_id": row["id"],
            "candidate_id": row["candidate"],
        }
        proposal = json.loads(row["proposal"]) if row["proposal"] != "baseline" else {}
        strategy = {
            **identity,
            "decision": row["status"],
            "hypothesis": proposal.get("hypothesis", "Baseline"),
            "proposal_source": result.get(
                "proposal_source", "baseline" if row["status"] == "baseline" else "not_recorded"
            ),
            "score": result.get("score"),
            "score_version": result.get("score_version", "annual-fold-consistency/1"),
            "absolute_performance_score": result.get("absolute_performance_score"),
            **{"score_" + key: value for key, value in result.get("score_components", {}).items()},
            "selection_control_id": result.get("selection_control"),
            "selection_control_score": result.get("selection_control_score"),
            "required_improvement": result.get("required_improvement"),
            "failed_gates": json.dumps(result.get("failed_gates", [])),
        }
        for variant, key in (("primary", "train"), ("stress", "stress")):
            trade_path = f"outputs/runs/{row['candidate']}/{variant}/trade-monitor.json"
            if trade_path in inputs:
                payload = json.loads((root / trade_path).read_text())
                if payload.get("schema") != TRADE_SCHEMA:
                    raise ValueError("unsupported recorded trade evidence: " + trade_path)
                trade_inputs[trade_path] = inputs[trade_path]
                for event in payload["events"]:
                    event_key = (event["source"], event["run_id"], event["event_id"])
                    if event_key in trade_events and trade_events[event_key] != event:
                        raise ValueError("conflicting recorded trade identity")
                    trade_events[event_key] = event
            strategy.update(
                {f"{variant}_{key}": value for key, value in result.get(key, {}).items()}
            )
            relative = f"outputs/runs/{row['candidate']}/{variant}/daily.json"
            daily = json.loads((root / relative).read_text()) if relative in inputs else []
            tables["periods"].extend(
                {**identity, "variant": variant, **period} for period in period_reports(daily)
            )
            strategy.update(
                {f"{variant}_{key}": value for key, value in daily_metrics(daily).items()}
            )
            peak = 0.0
            for session_index, point in enumerate(daily):
                peak = max(peak, float(point["equity"]))
                tables["daily"].append(
                    {
                        **identity,
                        "variant": variant,
                        "is_anchor": session_index == 0,
                        **{
                            key: point.get(key)
                            for key in (
                                "session",
                                "equity",
                                "return",
                                "cash",
                                "gross_exposure",
                                "forecast_volatility_pre",
                                "forecast_volatility_post",
                            )
                        },
                        "equity_index_100": 100
                        * float(point["equity"])
                        / float(daily[0]["equity"]),
                        "drawdown": float(point["equity"]) / peak - 1,
                        "binding_constraints": json.dumps(point.get("binding_constraints")),
                        **{
                            f"attribution_{key}": value
                            for key, value in point.get("attribution", {}).items()
                        },
                    }
                )
                for kind in ("fills", "missed"):
                    for index, item in enumerate(point.get(kind, [])):
                        tables[kind].append(
                            {
                                **identity,
                                "variant": variant,
                                "session": point["session"],
                                "record_index": index,
                                **item,
                            }
                        )
        tables["strategies"].append(strategy)
        diagnostics.append(
            {
                **identity,
                "proposal": proposal,
                "diagnostics": result.get("diagnostics", {}),
                "controls": result.get("control_summary", {}),
                "fit_count": result.get("fit_count"),
            }
        )
        tables["folds"].extend({**identity, **fold} for fold in result.get("folds", []))
        for name, case in result.get("robustness", {}).get("scenarios", {}).items():
            for role in ("candidate", "baseline"):
                tables["scenarios"].append(
                    {
                        **identity,
                        "scenario": name,
                        "kind": case["kind"],
                        "role": role,
                        "seed": case.get("seed"),
                        "distribution": case.get("distribution"),
                        "path_index": case.get("path_index"),
                        "coverage_complete": case.get("coverage_complete"),
                        "one_way_bps": case.get("one_way_bps"),
                        "sharpe_difference": case.get("sharpe_difference"),
                        **{
                            f"return_{key}": value
                            for key, value in case[role].get("return_distribution", {}).items()
                            if isinstance(value, (int, float)) or value is None
                        },
                        **{
                            key: value
                            for key, value in case[role].items()
                            if not isinstance(value, (list, dict))
                        },
                    }
                )
    for event in telemetry["events"]:
        if event["kind"] in ("phase_finished", "phase_interrupted"):
            payload = json.loads(event["payload"])
            tables["timings"].append(
                {
                    "program_sha256": report["program_sha256"],
                    "experiment_id": payload.get("experiment"),
                    "phase": payload["phase"],
                    "started_at": payload["started_at"],
                    "recorded_at": event["recorded_at"],
                    "elapsed_seconds": payload.get("elapsed_seconds"),
                    "interrupted": event["kind"] == "phase_interrupted",
                }
            )
    endings = {
        json.loads(event["payload"]).get("call_id"): json.loads(event["payload"])
        for event in telemetry["events"]
        if event["kind"] == "model_call_finished"
    }
    for call in telemetry["calls"]:
        response = json.loads(call["response"]) if call["response"] else {}
        request = json.loads(call["request"])
        tables["providers"].append(
            {
                "call_id": call["id"],
                "day": call["day"],
                "provider": call["provider"],
                "model": call["model"],
                "purpose": request.get("schema_name"),
                "status": response.get("error_class", "pending"),
                "input_tokens": response.get("input_tokens"),
                "output_tokens": response.get("output_tokens"),
                "budget_charged_tokens": call["charged"],
                "elapsed_seconds": endings.get(call["id"], {}).get("elapsed_seconds"),
            }
        )
    target = root / "analysis"
    target.mkdir(exist_ok=True)
    for name, records in tables.items():
        write_csv(target / f"{name}.csv", records)
    event_path = target / "events.jsonl"
    temporary = target / "events.jsonl.tmp"
    temporary.write_text(
        "".join(
            json.dumps({**event, "payload": json.loads(event["payload"])}) + "\n"
            for event in telemetry["events"]
        )
    )
    temporary.replace(event_path)
    failures = export_failures(root, report)
    write_monitor(
        target / "trades",
        document(list(trade_events.values()), source_artifacts=trade_inputs),
    )
    summary = {
        "failure_count": len(failures),
        "schema_version": "research-analysis/1",
        "selection_use": "diagnostics_only",
        "strategies": tables["strategies"],
        "monitoring": monitoring(telemetry, root),
        "rows": {key: len(value) for key, value in tables.items()},
        "trade_monitor": {"recorded_runs": len(trade_inputs), "events": len(trade_events)},
    }
    write_json(target / "metrics.json", summary)
    write_json(target / "feature_diagnostics.json", diagnostics)
    write_json(
        target / "manifest.json",
        {
            "schema_version": "research-analysis/1",
            "program_sha256": report["program_sha256"],
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "analysis_source_sha256": digest_file(Path(__file__)),
            "trade_monitor_source_sha256": digest_file(
                Path(__file__).parents[1] / "amat/reporting/trade_monitor.py"
            ),
            "source_artifacts": inputs,
            "rows": summary["rows"],
            "exports": {
                path.name: digest_file(path)
                for path in sorted(target.iterdir())
                if path.suffix in (".json", ".jsonl", ".csv", ".html")
                and path.name != "manifest.json"
            },
            "trade_exports": {
                str(path.relative_to(target)): digest_file(path)
                for path in sorted((target / "trades").iterdir())
                if path.is_file()
            },
            "definitions": {
                "sortino_mar0": (
                    "252 * mean(daily returns) / (sqrt(252) * RMS(min(return, 0))); null "
                    "if no downside."
                ),
                "calmar": "CAGR / absolute maximum drawdown; null if no drawdown.",
                "daily_var95_loss": (
                    "max(0, -5th percentile daily return); observed daily loss fraction."
                ),
                "daily_expected_shortfall95_loss": (
                    "Negative mean of worst 5% of empirical return mass, with fractional "
                    "boundary weight; floored at zero. Tied quantiles do not dilute the tail."
                ),
                "annualized_one_way_turnover": (
                    "252/N times sum(abs(fill price * quantity)/previous equity), "
                    "excluding initial anchor."
                ),
                "longest_underwater_sessions": (
                    "Longest consecutive observed sessions below prior peak; unrecovered "
                    "intervals are included."
                ),
                "equity_index_100": "Daily equity rebased to 100 at the measured-period anchor.",
                "missing": "Null JSON / blank CSV means unavailable or undefined, never zero.",
                "budget_charged_tokens": (
                    "Budget accounting includes conservative reservations when actual "
                    "usage is unknown; not a dollar cost."
                ),
            },
        },
    )
    return summary
