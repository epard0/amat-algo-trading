"""Explicit data-vintage reconciliation and recalibration of inert shadow patterns."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from amat.data.alpaca import AlpacaMarketData, DailyBar, DividendEvent, SplitEvent, WindowBar
from amat.data.snapshot import load_panel, write_snapshot
from autoresearch.calendar_audit import audit_sessions
from autoresearch.contracts import Candidate
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.reconciliation import reconciliation_report
from autoresearch.genetic.shadow_artifact import _candidate, freeze_shadow_artifact
from autoresearch.genetic.shadow_feed import _append_windows
from autoresearch.genetic.study import write_json
from autoresearch.market_scenarios import panel_digest


def _envelope(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text())
    if (
        set(value) != {"payload", "payload_sha256"}
        or digest_json(value["payload"]) != value["payload_sha256"]
    ):
        raise ValueError(f"receipt or artifact checksum mismatch: {path.name}")
    return value


def _receipt(
    path: Path, kind: str, symbols: list[str], start: dt.date, end: dt.date
) -> dict[str, Any]:
    envelope = _envelope(path)
    payload = envelope["payload"]
    received = dt.datetime.fromisoformat(payload["received_at"])
    if (
        payload["kind"] != kind
        or payload["provider"] != "alpaca"
        or payload["feed"] != "sip"
        or payload["symbols"] != symbols
        or dt.date.fromisoformat(payload["start"]) != start
        or dt.date.fromisoformat(payload["end"]) < end
        or received.tzinfo is None
        or received.utcoffset() != dt.timedelta(0)
        or received.date() <= end
        or received > dt.datetime.now(dt.UTC)
    ):
        raise ValueError("receipt coverage, universe, provider, or completion mismatch")
    return envelope


def _bars(payload: dict[str, Any], end: dt.date) -> list[DailyBar]:
    result = []
    for row in payload["records"]:
        session = dt.date.fromisoformat(row["session_date"])
        if session <= end:
            result.append(DailyBar(**{**row, "session_date": session}))
    return result


def refresh_shadow(
    previous_artifact: Path,
    old_snapshot: Path,
    receipts: Path,
    *,
    root: Path,
    snapshot_id: str,
    baseline_end: dt.date,
    output: Path,
    client: AlpacaMarketData | None = None,
) -> dict[str, Any]:
    """Create a new snapshot and artifact only after strict reconciliation passes.

    ``output`` is a new evidence directory. The original artifact's formula and
    calibration dates remain fixed. Runtime source hashes are deliberately
    refreshed, while the formula's registry binding must still validate today.
    Historical execution windows retain their original provenance; missing new
    sessions require a read-only provider client. No campaign is registered here.
    """
    if not snapshot_id or Path(snapshot_id).name != snapshot_id or snapshot_id in {".", ".."}:
        raise ValueError("snapshot_id must be one path component")
    destination = root / "data/snapshots" / snapshot_id
    if output.resolve() == destination.resolve():
        raise ValueError("refresh evidence directory must differ from snapshot")
    if output.exists() or destination.exists():
        raise FileExistsError("refresh output or snapshot already exists")
    envelope = _envelope(previous_artifact)
    previous = envelope["payload"]
    if (
        previous["schema_version"] != "shadow-pattern/1"
        or previous["diagnostic_only"] is not True
        or previous["execution_authority"] != "none"
        or previous["promotion_authority"] != "none"
        or previous["candidate_sha256"] != digest_json(previous["candidate"])
        or previous["provenance_sha256"] != digest_json(previous["provenance"])
    ):
        raise ValueError("invalid previous shadow authority or identity")
    candidate = _candidate(Candidate.model_validate(previous["candidate"]))
    symbols = previous["universe"]
    if symbols != list(map(str, candidate.config.strategy.universe.symbols)):
        raise ValueError("previous candidate universe mismatch")
    calibration = previous["calibration"]
    start, end = (dt.date.fromisoformat(calibration[key]) for key in ("start", "end"))
    original, manifest = load_panel(old_snapshot, symbols, start=start, end=end, with_context=True)
    if (
        original.sessions[0] != start
        or original.sessions[-1] != end
        or len(original) != calibration["sessions"]
        or panel_digest(original) != calibration["panel_sha256"]
    ):
        raise ValueError("original calibration panel mismatch")
    if baseline_end < manifest.last_session or baseline_end <= end:
        raise ValueError("refresh baseline cannot truncate original snapshot or calibration")
    bundles = {
        kind: _receipt(
            receipts / f"{kind}.json", kind, symbols, manifest.first_session, baseline_end
        )
        for kind in ("raw", "all", "actions")
    }
    raw, adjusted = (_bars(bundles[kind]["payload"], baseline_end) for kind in ("raw", "all"))
    dividends, splits = _actions(bundles["actions"]["payload"]["records"], baseline_end)
    supplement = _supplement(receipts, manifest.last_session, baseline_end, symbols, dividends)
    report = reconciliation_report(
        old_snapshot, raw, adjusted, dividends, splits, baseline_end, symbols=symbols
    )
    if report["status"] != "passed":
        raise ValueError(f"shadow reconciliation refused: {report['issues']}")
    sessions = tuple(sorted({bar.session_date for bar in raw}))
    calendar = audit_sessions(list(sessions), start=manifest.first_session, end=baseline_end)
    windows = _windows(old_snapshot, symbols, sessions, manifest, client)
    received_at = max(
        dt.datetime.fromisoformat(bundle["payload"]["received_at"]) for bundle in bundles.values()
    )
    if supplement is not None:
        received_at = max(
            received_at, dt.datetime.fromisoformat(supplement["payload"]["received_at"])
        )
    provenance = {
        "schema_version": "shadow-data-refresh/1",
        "previous_artifact_sha256": envelope["payload_sha256"],
        "previous_provenance": previous["provenance"],
        "previous_snapshot": manifest.model_dump(mode="json"),
        "receipt_sha256": {kind: bundle["payload_sha256"] for kind, bundle in bundles.items()},
        "action_supplement": supplement,
        "reconciliation_sha256": digest_json(report),
        "calendar_sha256": digest_json(calendar),
        "historical_validation_approved": False,
        "forward_observation_status": "not_yet_prospective",
        "execution_windows": {
            "historical_source": str(old_snapshot.resolve()),
            "historical_through": manifest.last_session.isoformat(),
            "new_sessions": len(
                [session for session in sessions if session > manifest.last_session]
            ),
        },
        "calibration_interval_preserved": True,
        "candidate_unchanged": True,
    }
    # All reconciliation and collection gates above precede the first new write.
    directory, fresh_manifest = write_snapshot(
        root,
        snapshot_id,
        raw_bars=raw,
        adjusted_bars=adjusted,
        window_bars=windows,
        dividends=dividends,
        splits=splits,
        provider=manifest.provider,
        feed=manifest.feed,
        as_of=received_at,
        execution_window_start=manifest.execution_window_start,
        execution_window_end=manifest.execution_window_end,
        exchange_timezone=manifest.exchange_timezone,
    )
    panel, _ = load_panel(directory, symbols, start=start, end=end, with_context=True)
    output.mkdir(parents=True, exist_ok=False)
    artifact = freeze_shadow_artifact(
        candidate, panel, provenance=provenance, output=output / "artifact.json"
    )
    result = {
        "schema_version": "shadow-refresh-result/1",
        "status": "completed",
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "snapshot": str(directory.resolve()),
        "snapshot_manifest": fresh_manifest.model_dump(mode="json"),
        "artifact_sha256": artifact["payload_sha256"],
        "candidate_sha256": artifact["payload"]["candidate_sha256"],
        "calibration": artifact["payload"]["calibration"],
        "provenance": provenance,
    }
    write_json(output / "reconciliation.json", report)
    write_json(output / "refresh.json", result)
    return result


def _windows(
    old_snapshot: Path,
    symbols: list[str],
    sessions: tuple[dt.date, ...],
    manifest: Any,
    client: AlpacaMarketData | None,
) -> list[WindowBar]:
    zone = ZoneInfo(manifest.exchange_timezone)
    frame = pl.read_parquet(old_snapshot / "window.parquet").filter(pl.col("symbol").is_in(symbols))
    result = [
        WindowBar(
            row["symbol"],
            row["session_date"],
            dt.datetime.combine(row["session_date"], manifest.execution_window_start, zone),
            dt.datetime.combine(row["session_date"], manifest.execution_window_end, zone),
            row["vwap"],
            row["volume"],
        )
        for row in frame.iter_rows(named=True)
    ]
    old_panel, _ = load_panel(old_snapshot, symbols, with_context=True)
    if sessions[: len(old_panel)] != old_panel.sessions:
        raise ValueError("refresh session prefix mismatch")
    if len(sessions) > len(old_panel):
        if client is None:
            raise ValueError("refresh requires a provider client for new execution windows")
        prices, volumes = _append_windows(
            old_panel,
            client,
            sessions,
            window_start=manifest.execution_window_start,
            window_end=manifest.execution_window_end,
            exchange_timezone=manifest.exchange_timezone,
        )
        for index in range(len(old_panel), len(sessions)):
            session = sessions[index]
            for column, symbol in enumerate(symbols):
                result.append(
                    WindowBar(
                        symbol,
                        session,
                        dt.datetime.combine(session, manifest.execution_window_start, zone),
                        dt.datetime.combine(session, manifest.execution_window_end, zone),
                        float(prices[index, column]),
                        float(volumes[index, column]),
                    )
                )
    return result


def _supplement(
    receipts: Path,
    previous_end: dt.date,
    through: dt.date,
    symbols: list[str],
    dividends: list[DividendEvent],
) -> dict[str, Any] | None:
    """Accept auditable, explicitly supplied new issuer/exchange dividend evidence."""
    path = receipts / "action_supplements.json"
    if not path.exists():
        return None
    envelope = _envelope(path)
    payload = envelope["payload"]
    received = dt.datetime.fromisoformat(payload["received_at"])
    if (
        payload["schema_version"] != "shadow-action-supplements/1"
        or received.tzinfo is None
        or received.utcoffset() != dt.timedelta(0)
        or received > dt.datetime.now(dt.UTC)
    ):
        raise ValueError("invalid action supplement schema or receipt time")
    existing = {(event.symbol, event.ex_date) for event in dividends}
    for row in payload["records"]:
        date = dt.date.fromisoformat(row["ex_date"])
        source = (receipts / row["source_file"]).resolve()
        rate = float(row["rate"])
        if (
            not source.is_relative_to(receipts.resolve())
            or not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() != row["source_sha256"]
            or not row["source_url"].startswith("https://")
            or not isinstance(row["source_record"], dict)
            or not row["source_record"]
            or row["symbol"] not in symbols
            or not previous_end < date <= through
            or not 0 < rate < float("inf")
            or (row["symbol"], date) in existing
        ):
            raise ValueError("unverified, duplicate, historical, or invalid action supplement")
        existing.add((row["symbol"], date))
        dividends.append(DividendEvent(row["symbol"], date, rate))
    return {
        "payload_sha256": envelope["payload_sha256"],
        "payload": payload,
        "authority": "explicit issuer/exchange supplement; not an Alpaca action receipt",
    }


def _actions(
    records: list[dict[str, Any]], baseline_end: dt.date
) -> tuple[list[DividendEvent], list[SplitEvent]]:
    dividends: list[DividendEvent] = []
    splits: list[SplitEvent] = []
    for row in records:
        event = {key: value for key, value in row.items() if key != "kind"}
        event["ex_date"] = dt.date.fromisoformat(event["ex_date"])
        if row["kind"] not in {"cash_dividend", "split"}:
            raise ValueError("unknown receipt corporate action kind")
        if event["ex_date"] <= baseline_end:
            if row["kind"] == "cash_dividend":
                dividends.append(DividendEvent(**event))
            else:
                splits.append(SplitEvent(**event))
    return dividends, splits
