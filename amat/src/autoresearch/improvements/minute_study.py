"""Frozen matched minute comparisons, with cached prior-daily signal controls."""

from __future__ import annotations

import bisect
import datetime as dt
import json
from pathlib import Path
from typing import Any, cast

import exchange_calendars as xcals  # type: ignore[import-untyped]

from amat.config.loading import load_strategy_config
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.portfolio import decide, precompute_decision_inputs
from amat.risk.active import ActiveRiskConfig
from autoresearch.improvements.minute import iter_batches
from autoresearch.improvements.minute_data import audit_archive
from autoresearch.improvements.replay import MinuteArm, replay_minutes
from autoresearch.improvements.study import digest, metrics, source_hashes, write


def register_minute(  # noqa: PLR0917 - evidence paths plus declared interval
    root: Path, archive: Path, snapshot: Path, output: Path, start: dt.date, end: dt.date
) -> dict[str, Any]:
    if output.exists() or start >= end:
        raise ValueError("new output and ordered dates required")
    strategy = load_strategy_config(root / "configs/strategy/amat_v13_return_target.toml").strategy
    symbols = tuple(map(str, strategy.universe.symbols))
    audit = audit_archive(archive, symbols)
    if audit["missing_symbols"]:
        raise ValueError("minute universe incomplete")
    minute_manifest = json.loads((archive / "manifest.json").read_text())
    protocol = {
        "schema": "minute-improvement-study/1",
        "root": str(root.resolve()),
        "source_sha256": source_hashes(root),
        "archive": str(archive.resolve()),
        "minute_partition_sha256": minute_manifest["partitions"],
        "snapshot": str(snapshot.resolve()),
        "daily_partition_sha256": load_manifest(snapshot).partition_sha256,
        "symbols": symbols,
        "start": str(start),
        "end": str(end),
        "arms": ["M0", "M1", "M2", "M3"],
        "costs_bps": [2, 6, 10],
        "decision_minutes": 5,
        "label_minutes": 15,
        "training_sessions": 20,
        "refit_minutes": 250,
        "max_fits": 64,
        "common_max_positions": 4,
        "rebalance_band": 0.02,
        "minimum_trade_notional": 25,
        "common_symbol_cap": 0.25,
        "common_metals_cap": 0.30,
        "qualified_minute": (
            "one global timestamp with at least one admitted bar; unavailable assets abstain"
        ),
        "seed": 20261014,
        "risk": ActiveRiskConfig().model_dump(),
        "risk_status": "provisional research settings; no account-level authorization",
        "execution": "long-only; flat before close; later open; 1% raw-volume capacity proxy",
        "training": "16-program population; 2 generations; purged forward-label screen",
        "training_completion": "historical completion barrier then later decision boundary",
        "evidence": "previously inspected retrospective mechanism pilot; no promotion",
    }
    output.mkdir(parents=True)
    write(output / "protocol.json", protocol)
    (output / "protocol.sha256").write_text(digest(protocol))
    return protocol


def verify_minute(output: Path) -> dict[str, Any]:
    body = json.loads((output / "protocol.json").read_text())
    if digest(body) != (output / "protocol.sha256").read_text():
        raise ValueError("minute protocol changed")
    if source_hashes(Path(body["root"])) != body["source_sha256"]:
        raise ValueError("minute study source changed")
    manifest = json.loads((Path(body["archive"]) / "manifest.json").read_text())
    if manifest["partitions"] != body["minute_partition_sha256"]:
        raise ValueError("minute partitions changed")
    if load_manifest(Path(body["snapshot"])).partition_sha256 != body["daily_partition_sha256"]:
        raise ValueError("daily context changed")
    return body


def prior_daily_targets(
    root: Path, snapshot: Path, start: dt.date, end: dt.date
) -> dict[dt.date, dict[str, float]]:
    strategy = load_strategy_config(root / "configs/strategy/amat_v13_return_target.toml").strategy
    panel, _ = load_panel(snapshot, list(map(str, strategy.universe.symbols)), with_context=True)
    inputs = precompute_decision_inputs(strategy, panel.adjusted_closes, sessions=panel.sessions)
    result = {}
    calendar = xcals.get_calendar(
        "XNYS", start=str(start - dt.timedelta(days=2)), end=str(end + dt.timedelta(days=2))
    )
    for session in calendar.sessions_in_range(str(start), str(end)):
        date = session.date()
        i = bisect.bisect_left(panel.sessions, date) - 1
        if i < 0:
            continue
        allocation = decide(
            strategy,
            panel.adjusted_closes,
            strategy.to_universe(),
            decision_index=i,
            precomputed=inputs,
        )
        result[date] = {str(s): w for s, w in allocation.as_mapping(strategy.to_universe()).items()}
    return result


def run_minute_arm(output: Path, arm: str, cost: int) -> dict[str, Any]:
    p = verify_minute(output)
    if arm not in p["arms"] or cost not in p["costs_bps"]:
        raise ValueError("unregistered minute comparison")
    destination = output / f"{arm}-{cost}bps.json"
    if destination.exists():
        saved = json.loads(destination.read_text())
        if digest(saved) != destination.with_suffix(".sha256").read_text():
            raise ValueError("minute receipt changed")
        return saved["summary"]
    symbols = tuple(p["symbols"])
    start, end = dt.date.fromisoformat(p["start"]), dt.date.fromisoformat(p["end"])
    root = Path(p["root"])
    snapshot = Path(p["snapshot"])
    context = prior_daily_targets(root, snapshot, start, end) if arm == "M0" else None
    panel, _ = load_panel(snapshot, list(symbols))
    actions = load_corporate_actions(snapshot, list(symbols), sessions=panel.sessions)
    replay = replay_minutes(
        iter_batches(Path(p["archive"]), symbols, start, end),
        symbols,
        cast(MinuteArm, arm),
        cost_bps=cost,
        decision_minutes=p["decision_minutes"],
        label_minutes=p["label_minutes"],
        training_sessions=p["training_sessions"],
        refit_minutes=p["refit_minutes"],
        max_fits=p["max_fits"],
        seed=p["seed"],
        risk_config=ActiveRiskConfig.model_validate(p["risk"]),
        daily_targets=context,
        actions=actions,
    )
    closing = {}
    for row in replay["records"]:
        closing[row["session"]] = row
    rows = []
    for session, last in closing.items():
        events = [r for r in replay["records"] if r["session"] == session]
        rows.append(
            {
                "equity": last["equity"],
                "fills": sum(r["fills"] for r in events),
                "fees": sum(r["fees"] for r in events),
                "missed": sum(r["missed"] for r in events),
                "exposure": sum(r["exposure"] for r in events) / len(events),
            }
        )
    summary = {
        "arm": arm,
        "cost_bps": cost,
        **metrics(rows, replay["initial_equity"]),
        "stale_minutes": replay["stale_minutes"],
        "failed_liquidations": replay["failed_liquidations"],
        "fit_events": len(replay["refits"]),
        "activated_models": sum(
            e["event"] in {"initial_activated", "activated"} for e in replay["refits"]
        ),
        "minimum_cash": min((r["cash"] for r in replay["records"]), default=None),
    }
    value = {"protocol_sha256": digest(p), "summary": summary, "replay": replay}
    write(destination, json.loads(json.dumps(value, default=str, allow_nan=False)))
    retained = json.loads(destination.read_text())
    destination.with_suffix(".sha256").write_text(digest(retained))
    return summary


def minute_report(output: Path) -> Path:
    p = verify_minute(output)
    lines = [
        "# Matched intraday mechanism pilot",
        "",
        p["evidence"],
        "",
        (
            "Provisional research risk settings. M0 is a daily-signal control "
            "under a flat intraday mandate, not unchanged v13."
        ),
        (
            "GP uses a forward-label screening proxy; "
            "all reported performance comes from portfolio replay."
        ),
        "",
        "| Arm | Cost bps | Net return | Drawdown | Fills | Activated models | Stale minutes |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in p["arms"]:
        for cost in p["costs_bps"]:
            path = output / f"{arm}-{cost}bps.json"
            if not path.exists():
                lines.append(f"| {arm} | {cost} | missing | — | — | — | — |")
                continue
            r = run_minute_arm(output, arm, cost)
            lines.append(
                f"| {arm} | {cost} | {r['net_return']:.2%} | {r['max_drawdown']:.2%} | "
                f"{r['fills']} | {r['activated_models']} | {r['stale_minutes']} |"
            )
    lines += [
        "",
        "Raw minute coverage and fit failures remain in the denominator.",
        (
            "Historical finalized-bar/latency and aggregate-capacity assumptions "
            "require prospective shadow verification."
        ),
    ]
    path = output / "report.md"
    path.write_text("\n".join(lines) + "\n")
    return path
