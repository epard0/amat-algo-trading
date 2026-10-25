"""Frozen, manually polled prospective campaigns with durable market-data receipts."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.data.alpaca import AlpacaMarketData
from amat.data.snapshot import load_panel
from amat.domain.types import Symbol
from autoresearch.calendar_audit import audit_sessions
from autoresearch.contracts import Candidate
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.multitimeframe import (
    freeze_intraday_reference,
    intraday_timeframe_report,
    multi_timeframe_report,
)
from autoresearch.genetic.shadow import _prefix
from autoresearch.genetic.shadow_artifact import load_shadow_artifact, verify_calibration
from autoresearch.genetic.shadow_feed import extend_shadow_panel
from autoresearch.genetic.shadow_inference import infer_shadow
from autoresearch.genetic.shadow_intraday import aggregate_intraday, load_intraday
from autoresearch.genetic.shadow_ledger import (
    OBSERVATION_VERSION,
    read_shadow_ledger,
    record_shadow_observation,
)
from autoresearch.market_scenarios import panel_digest


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _immutable(path: Path, value: dict[str, Any]) -> None:
    """Publish once; an interrupted write cannot replace an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError(f"immutable output differs: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {"payload": payload, "payload_sha256": digest_json(payload)}


def _read(path: Path, schema: str) -> dict[str, Any]:
    envelope = json.loads(path.read_text())
    if (
        set(envelope) != {"payload", "payload_sha256"}
        or envelope["payload_sha256"] != digest_json(envelope["payload"])
        or envelope["payload"].get("schema_version") != schema
    ):
        raise ValueError("campaign or receipt checksum/schema mismatch")
    return cast("dict[str, Any]", envelope)


def _baseline(artifact: dict[str, Any], snapshot: Path) -> tuple[MarketPanel, Any]:
    payload = artifact["payload"]
    panel, manifest = load_panel(
        snapshot,
        tuple(payload["universe"]),
        start=dt.date.fromisoformat(payload["calibration"]["start"]),
        with_context=True,
    )
    if manifest.feed != "sip":
        raise ValueError("shadow campaigns require the SIP snapshot/feed")
    verify_calibration(
        artifact, _prefix(panel, dt.date.fromisoformat(payload["calibration"]["end"]))
    )
    audit_sessions(list(panel.sessions), start=panel.sessions[0], end=panel.sessions[-1])
    return panel, manifest


def register_campaign(
    artifact_path: Path,
    snapshot: Path,
    *,
    output: Path,
    intraday_data: Path | None = None,
    intraday_calibration_end: dt.date | None = None,
) -> dict[str, Any]:
    """Register the actual UTC freeze time, after validating the complete baseline."""
    if output.suffix != ".json" or output.resolve() == artifact_path.resolve():
        raise ValueError("campaign requires a distinct JSON output")
    if output.exists():
        raise ValueError("campaign output already exists")
    artifact = load_shadow_artifact(artifact_path)
    panel, manifest = _baseline(artifact, snapshot)
    registered = _now()
    calendar = audit_sessions(list(panel.sessions), start=panel.sessions[0], end=panel.sessions[-1])
    if dt.datetime.fromisoformat(calendar["sessions"][-1]["close"]) >= registered:
        raise ValueError("baseline must contain only completed sessions before registration")
    intraday_reference = None
    if intraday_data is not None:
        if intraday_calibration_end is None or intraday_calibration_end > panel.sessions[-1]:
            raise ValueError("intraday calibration must end by the baseline last session")
        intraday_reference = freeze_intraday_reference(intraday_data, intraday_calibration_end)
        _verify_intraday_history(
            load_intraday(intraday_data),
            [],
            received=_now(),
            reference=intraday_reference,
        )
        if intraday_reference["universe"] != list(panel.symbols):
            raise ValueError("intraday universe differs from campaign")
    elif intraday_calibration_end is not None:
        raise ValueError("intraday calibration requires an intraday data source")
    payload = {
        "schema_version": "genetic-shadow-campaign/1",
        "diagnostic_only": True,
        "execution_authority": "none",
        "promotion_authority": "none",
        "registered_at": registered.isoformat(),
        "artifact": str(artifact_path.resolve()),
        "artifact_sha256": artifact["payload_sha256"],
        "snapshot": str(snapshot.resolve()),
        "snapshot_manifest_sha256": digest_json(manifest.model_dump(mode="json")),
        "baseline_panel_sha256": panel_digest(panel),
        "baseline_last_session": panel.sessions[-1].isoformat(),
        "universe": list(panel.symbols),
        "prospective_rule": "session open strictly after campaign registration",
        "timeframes": ["15m", "1h", "1d", "1w", "1mo"],
        "intraday_reference": intraday_reference,
    }
    payload["registered_at"] = _now().isoformat()
    envelope = _envelope(payload)
    _immutable(output, envelope)
    return envelope


def _encode_panel(panel: MarketPanel) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sessions": [day.isoformat() for day in panel.sessions],
        "symbols": list(panel.symbols),
        "source": panel.source,
    }
    for field in fields(panel):
        value = getattr(panel, field.name)
        if isinstance(value, np.ndarray):
            encoded = value.astype(object)
            encoded[~np.isfinite(value)] = None
            result[field.name] = encoded.tolist()
        elif value is None:
            result[field.name] = None
    return result


def _decode_panel(value: dict[str, Any]) -> MarketPanel:
    array_names = {field.name for field in fields(MarketPanel)} - {"sessions", "symbols", "source"}
    arrays: dict[str, Any] = {
        name: None if value[name] is None else np.asarray(value[name], dtype=float)
        for name in array_names
    }
    return MarketPanel(
        sessions=tuple(dt.date.fromisoformat(day) for day in value["sessions"]),
        symbols=tuple(Symbol(symbol) for symbol in value["symbols"]),
        source=value["source"],
        **arrays,
    )


def _verify_receipt(path: Path, expected: str | None = None) -> dict[str, Any]:
    if expected is not None and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("market-data receipt changed")
    envelope = _read(path, "genetic-shadow-receipt/1")
    payload = envelope["payload"]
    if panel_digest(_decode_panel(payload["panel"])) != payload["panel_sha256"]:
        raise ValueError("receipt panel digest mismatch")
    return cast("dict[str, Any]", payload)


def _verify_intraday_history(
    current: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    received: dt.datetime,
    reference: dict[str, Any],
) -> None:
    collected = dt.datetime.fromisoformat(current["collected_at"])
    cutoff = dt.datetime.combine(
        dt.date.fromisoformat(reference["calibration_end"]) + dt.timedelta(days=1),
        dt.time(),
        tzinfo=dt.UTC,
    )
    if collected.tzinfo is None or not cutoff <= collected <= received:
        raise ValueError(
            "intraday collection timestamp must follow calibration and precede receipt"
        )
    # Missing intraday observations do not reset previously consumed minute history.
    for record in reversed(records):
        report = record["report"]
        receipt = _verify_receipt(Path(report["receipt_path"]), report["receipt_sha256"])
        prior = receipt.get("intraday_input")
        if prior is None:
            continue
        observed_through = dt.datetime.fromisoformat(report["session_close"])
        for timeframe in ("15m", "1h"):
            before = aggregate_intraday(prior["payload"], timeframe, as_of=observed_through)
            after = aggregate_intraday(current, timeframe, as_of=observed_through)
            if panel_digest(before) != panel_digest(after):
                raise ValueError("previously observed intraday history changed")
        break


def poll_campaign(
    campaign_path: Path,
    *,
    through: dt.date,
    ledger: Path,
    output: Path,
    client: AlpacaMarketData | None = None,
    intraday_data: Path | None = None,
) -> dict[str, Any]:
    """Poll once; persist complete input before recording any diagnostic observation."""
    campaign = _read(campaign_path, "genetic-shadow-campaign/1")
    payload = campaign["payload"]
    receipt_path = output.with_suffix(".receipt.json")
    lock = campaign_path.with_suffix(campaign_path.suffix + ".lock")
    reserved = {
        campaign_path.resolve(),
        lock.resolve(),
        Path(payload["artifact"]).resolve(),
        ledger.resolve(),
        ledger.with_suffix(ledger.suffix + ".lock").resolve(),
    }
    if (
        output.suffix != ".json"
        or output.resolve() in reserved
        or receipt_path.resolve() in reserved
    ):
        raise ValueError("campaign outputs must use distinct JSON paths")
    if ledger.resolve() in {
        campaign_path.resolve(),
        lock.resolve(),
        Path(payload["artifact"]).resolve(),
    }:
        raise ValueError("ledger must use a distinct path")
    minute_path = receipt_path.with_suffix(".minutes.json")
    if minute_path.resolve() in reserved or (
        intraday_data is not None
        and intraday_data.resolve()
        in reserved | {output.resolve(), receipt_path.resolve(), minute_path.resolve()}
    ):
        raise ValueError("intraday source and receipt paths must be distinct")
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return _poll(campaign, through, ledger, output, receipt_path, client, intraday_data)


def _poll(  # noqa: PLR0912, PLR0915, PLR0917
    campaign: dict[str, Any],
    through: dt.date,
    ledger: Path,
    output: Path,
    receipt_path: Path,
    client: AlpacaMarketData | None,
    intraday_data: Path | None,
) -> dict[str, Any]:
    payload = campaign["payload"]
    if client is not None and getattr(client, "_feed", "sip") != "sip":
        raise ValueError("campaign market-data client must use SIP")
    if payload["diagnostic_only"] is not True or any(
        payload[key] != "none" for key in ("execution_authority", "promotion_authority")
    ):
        raise ValueError("campaign cannot grant authority")
    artifact = load_shadow_artifact(Path(payload["artifact"]))
    if artifact["payload_sha256"] != payload["artifact_sha256"]:
        raise ValueError("campaign artifact changed")
    panel, manifest = _baseline(artifact, Path(payload["snapshot"]))
    if (
        panel_digest(panel) != payload["baseline_panel_sha256"]
        or digest_json(manifest.model_dump(mode="json")) != payload["snapshot_manifest_sha256"]
    ):
        raise ValueError("campaign baseline snapshot changed")
    if through <= panel.sessions[-1]:
        raise ValueError("campaign observations must follow the baseline snapshot")
    if intraday_data is not None and payload["intraday_reference"] is None:
        raise ValueError("intraday source requires a reference frozen at registration")
    records = read_shadow_ledger(ledger)
    for record in records:
        report = record["report"]
        if report.get("campaign_sha256") != campaign["payload_sha256"]:
            raise ValueError("ledger belongs to a different campaign")
        _verify_receipt(Path(report["receipt_path"]), report["receipt_sha256"])
    if records and records[-1]["report"]["as_of"] >= through.isoformat():
        record = records[-1]
        if record["report"]["as_of"] != through.isoformat():
            raise ValueError("campaign cannot rewind")
        _immutable(output, record)
        return record
    if output.exists():
        raise ValueError("observation output already exists")
    if receipt_path.exists():
        receipt = _verify_receipt(receipt_path)
        if (
            receipt["campaign_sha256"] != campaign["payload_sha256"]
            or receipt["as_of"] != through.isoformat()
        ):
            raise ValueError("receipt belongs to another observation")
        extended = _decode_panel(receipt["panel"])
    else:
        extended = extend_shadow_panel(
            panel,
            client if client is not None else AlpacaMarketData(),
            through=through,
            now=_now(),
            window_start=manifest.execution_window_start,
            window_end=manifest.execution_window_end,
            exchange_timezone=manifest.exchange_timezone,
        )
        receipt = {
            "schema_version": "genetic-shadow-receipt/1",
            "campaign_sha256": campaign["payload_sha256"],
            "as_of": through.isoformat(),
            "received_at": _now().isoformat(),
            "panel": _encode_panel(extended),
            "panel_sha256": panel_digest(extended),
            "provider": "alpaca",
            "feed": "sip",
            "intraday_input": json.loads(intraday_data.read_text())
            if intraday_data is not None
            else None,
        }
        _immutable(receipt_path, _envelope(receipt))
    if extended.sessions[-1] != through:
        raise ValueError("requested completed session is unavailable")
    if panel_digest(_prefix(extended, panel.sessions[-1])) != panel_digest(panel):
        raise ValueError("receipt changed frozen baseline")
    if records:
        previous = records[-1]["report"]
        if (
            panel_digest(_prefix(extended, dt.date.fromisoformat(previous["as_of"])))
            != previous["panel_sha256"]
        ):
            raise ValueError("previously received market history changed")
    calendar = audit_sessions(list(extended.sessions), start=extended.sessions[0], end=through)
    session = calendar["sessions"][-1]
    opened = dt.datetime.fromisoformat(session["open"])
    closed = dt.datetime.fromisoformat(session["close"])
    registered = dt.datetime.fromisoformat(payload["registered_at"])
    received = dt.datetime.fromisoformat(receipt["received_at"])
    if (
        any(moment.tzinfo is None for moment in (opened, closed, registered, received))
        or received < closed
        or received < registered
        or received > _now()
    ):
        raise ValueError("invalid campaign/receipt completed-session timestamps")
    report = infer_shadow(artifact, extended)
    candidate = Candidate.model_validate(artifact["payload"]["candidate"])
    if candidate.genetic_pattern is None:
        raise ValueError("campaign artifact requires a genetic pattern")
    report["multi_timeframe"] = multi_timeframe_report(
        candidate.genetic_pattern.pattern,
        _prefix(extended, dt.date.fromisoformat(artifact["payload"]["calibration"]["end"])),
        extended,
        timeframes=("1d", "1w", "1mo"),
    )
    minute_input = receipt.get("intraday_input")
    if minute_input is not None:
        minute_path = receipt_path.with_suffix(".minutes.json")
        _immutable(minute_path, minute_input)
        current_minutes = load_intraday(minute_path)
        _verify_intraday_history(
            current_minutes,
            records,
            received=received,
            reference=payload["intraday_reference"],
        )
        report["intraday_timeframes"] = intraday_timeframe_report(
            candidate.genetic_pattern.pattern,
            payload["intraday_reference"],
            minute_path,
            as_of=closed,
        )
    else:
        report["intraday_timeframes"] = {
            "diagnostic_only": True,
            "execution_authority": "none",
            "promotion_authority": "none",
            "views": {
                timeframe: {"status": "unavailable", "reason": "no frozen intraday source supplied"}
                for timeframe in ("15m", "1h")
            },
        }
    report.update(
        {
            "schema_version": OBSERVATION_VERSION,
            "as_of": through.isoformat(),
            "panel_sha256": panel_digest(extended),
            "campaign_sha256": campaign["payload_sha256"],
            "received_at": receipt["received_at"],
            "registered_at": payload["registered_at"],
            "session_open": session["open"],
            "session_close": session["close"],
            "evidence_level": "prospective_shadow_observation"
            if opened > registered
            else "backfill_shadow_diagnostic",
            "receipt_path": str(receipt_path.resolve()),
            "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            "calendar_sha256": digest_json(calendar),
            "alerts": report["monitor"]["alerts"],
            "latency_ms": report.pop("latency_ns") / 1_000_000,
        }
    )
    record = record_shadow_observation(ledger, report)
    _immutable(output, record)
    return record
