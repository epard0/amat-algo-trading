"""Frozen full-history and independent quarterly walk-forward minute campaign."""

# Research protocol literals and owned gzip handles closed in finally.
# ruff: noqa: E501, PLR2004, PLR0915, SIM115
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

import exchange_calendars as xcals  # type: ignore[import-untyped]

from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.risk.active import ActiveRiskConfig
from autoresearch.improvements.feature_cache import FeatureCache
from autoresearch.improvements.minute import iter_batches
from autoresearch.improvements.minute_study import prior_daily_targets
from autoresearch.improvements.raw_cache import RawMinuteCache
from autoresearch.improvements.replay import MinuteArm, replay_minutes
from autoresearch.improvements.study import digest, metrics, source_hashes, write

SYMBOLS = ("SPY", "QQQ", "IWM", "EFA", "EEM", "IEF", "GLD", "SLV", "DBC", "USO", "UUP")


def register_full(root: Path, archive: Path, snapshot: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("new output required")
    calendar = xcals.get_calendar("XNYS", start="2021-11-01", end="2026-10-16")
    sessions = [s.date() for s in calendar.sessions_in_range("2022-01-03", "2026-10-13")]
    runs = []
    for arm in ("M0", "M1", "M2", "M3"):
        for cost in (2, 6, 10):
            runs.append(
                {
                    "id": f"full-{arm}-{cost}",
                    "kind": "full",
                    "arm": arm,
                    "cost": cost,
                    "start": str(sessions[0]),
                    "trade_start": str(sessions[0]),
                    "end": str(sessions[-1]),
                }
            )
    # Fixed calendar folds, independently reset cash/risk and prefix-only training.
    for year in range(2022, 2027):
        for month in (1, 4, 7, 10):
            if year == 2022 and month == 1:
                continue
            next_quarter = dt.date(year + (month == 10), 1 if month == 10 else month + 3, 1)
            test = [s for s in sessions if dt.date(year, month, 1) <= s < next_quarter]
            if not test:
                continue
            first = sessions.index(test[0])
            for arm, cost in [("M1", 2), ("M3", 2), ("M3", 6), ("M3", 10)]:
                runs.append(
                    {
                        "id": f"wf-{year}Q{(month - 1) // 3 + 1}-{arm}-{cost}",
                        "kind": "wf",
                        "arm": arm,
                        "cost": cost,
                        "start": str(sessions[first - 20]),
                        "trade_start": str(test[0]),
                        "end": str(test[-1]),
                    }
                )
    for arm, cost in [("M1", 2), ("M3", 2), ("M3", 6), ("M3", 10)]:
        first = next(i for i, s in enumerate(sessions) if s.year == 2026)
        runs.append(
            {
                "id": f"ytd-{arm}-{cost}",
                "kind": "ytd",
                "arm": arm,
                "cost": cost,
                "start": str(sessions[first - 20]),
                "trade_start": str(sessions[first]),
                "end": str(sessions[-1]),
            }
        )
    p = {
        "schema": "full-minute-walkforward/1",
        "root": str(root.resolve()),
        "archive": str(archive.resolve()),
        "snapshot": str(snapshot.resolve()),
        "sources": source_hashes(root),
        "config_sha256": hashlib.sha256(
            (root / "configs/strategy/amat_v13_return_target.toml").read_bytes()
        ).hexdigest(),
        "minute_partitions": json.loads((archive / "manifest.json").read_text())["partitions"],
        "daily_partitions": load_manifest(snapshot).partition_sha256,
        "symbols": SYMBOLS,
        "runs": runs,
        "initial_equity": 100000,
        "decision_minutes": 5,
        "label_minutes": 15,
        "training_sessions": 20,
        "refit_minutes": 250,
        "max_fits": 10000,
        "seed": 20261014,
        "risk": ActiveRiskConfig().model_dump(),
        "fit": "16 population, 2 generations; chronological 70/30 split with label-maturity purge; positive validation utility gate; incumbent comparison on same validation prefix",
        "walkforward": "Quarterly independent test accounts from 2022Q2; 20 prior exchange sessions nontrading warmup; M3 refits using only labels matured by each trigger during test. No hyperparameter selection across folds.",
        "costs": "One-way combined commission/slippage 2, 6, 10 bps; later raw open; 1% raw minute volume buy capacity proxy; sells uncapped; 1% cash reserve and 0.2% budget buffer",
        "constraints": "Long-only, flat before close, <=4 positions, 25% symbol, 30% metals, designed stress loss <=4%; research risk 10% drawdown, 3% daily loss, half exposure and 5-session cooldown",
        "evidence_status": "Retrospective historical validation; history previously exposed to research; no untouched prospective holdout; no deployment",
        "cutoff": "2026-10-13 last verified completed session; 2026-10-14 excluded",
        "acceptance": "Do not advance unless adaptive net profitability is positive at base and stressed costs, with stable quarterly results and no accounting or liquidation failure. Descriptive comparison, no promotion authority.",
    }
    output.mkdir(parents=True)
    write(output / "protocol.json", p)
    (output / "protocol.sha256").write_text(digest(p))
    return p


def run_full_job(output: Path, job: dict[str, Any]) -> dict[str, Any]:
    p = json.loads((output / "protocol.json").read_text())
    if digest(p) != (output / "protocol.sha256").read_text() or job not in p["runs"]:
        raise ValueError("protocol changed or unregistered run")
    root = Path(p["root"])
    if source_hashes(root) != p["sources"]:
        raise ValueError("runtime changed")
    destination = output / "runs" / job["id"]
    destination.mkdir(parents=True, exist_ok=True)
    receipt = destination / "receipt.json"
    if receipt.exists():
        value = json.loads(receipt.read_text())
        if digest(value) != (destination / "receipt.sha256").read_text():
            raise ValueError("receipt changed")
        return value["summary"]
    start, end, trade_start = (
        dt.date.fromisoformat(job[k]) for k in ("start", "end", "trade_start")
    )
    snapshot, archive, symbols = Path(p["snapshot"]), Path(p["archive"]), tuple(p["symbols"])
    if json.loads((archive / "manifest.json").read_text())["partitions"] != p["minute_partitions"]:
        raise ValueError("archive manifest changed")
    if load_manifest(snapshot).partition_sha256 != p["daily_partitions"]:
        raise ValueError("daily partitions changed")
    panel, _ = load_panel(snapshot, list(symbols))
    actions = load_corporate_actions(snapshot, list(symbols), sessions=panel.sessions)
    context = prior_daily_targets(root, snapshot, start, end) if job["arm"] == "M0" else None
    for key in ("feature_cache", "raw_cache"):
        if p.get(key):
            manifest_path = Path(p[key]) / "manifest.json"
            if (
                hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                != p[key + "_manifest_sha256"]
            ):
                raise ValueError("registered cache manifest changed")
    feature_cache = (
        FeatureCache(Path(p["feature_cache"]), symbols, p["minute_partitions"])
        if p.get("feature_cache")
        else None
    )
    handles = {
        kind: gzip.open(destination / f"{kind}.jsonl.gz", "wt", compresslevel=1)
        for kind in ("records", "fills", "decisions", "missed")
    }
    daily: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    reasons: Counter = Counter()
    peak = 100000.0
    intraday_dd = 0.0
    minimum_cash = 100000.0
    residual_max = 0.0
    first_trade = None

    def sink(kind: str, row: dict[str, Any]) -> None:
        nonlocal peak, intraday_dd, minimum_cash, residual_max, first_trade
        handles[kind].write(
            json.dumps(row, default=str, separators=(",", ":"), allow_nan=False) + "\n"
        )
        if kind == "records":
            if row["session"] < job["trade_start"]:
                return
            r = daily.setdefault(
                row["session"],
                {
                    "session": row["session"],
                    "equity": 100000.0,
                    "fills": 0,
                    "fees": 0.0,
                    "missed": 0,
                    "exposure": 0.0,
                    "minutes": 0,
                    "market_pnl": 0.0,
                    "distributions": 0.0,
                    "incomplete_minutes": 0,
                },
            )
            r["equity"] = row["equity"]
            for k in ("fills", "fees", "missed", "exposure", "market_pnl", "distributions"):
                r[k] += row[k]
            r["minutes"] += 1
            r["incomplete_minutes"] += int(not row["complete"])
            minimum_cash = min(minimum_cash, row["cash"])
            peak = max(peak, row["equity"])
            intraday_dd = min(intraday_dd, row["equity"] / peak - 1)
            residual_max = max(residual_max, abs(float(row["accounting_residual"])))
        elif kind == "decisions" and row["at"][:10] >= job["trade_start"]:
            counts["gp_decisions" if row["model_id"] else "fallback_decisions"] += 1
            reasons.update(row["risk_reasons"])
        elif kind == "fills" and row["at"][:10] >= job["trade_start"]:
            if first_trade is None:
                first_trade = row["at"]
            counts["fill_events"] += 1
            counts["turnover_dollars"] += float(row["quantity"]) * float(row["price"])  # type: ignore[assignment]

    try:
        batches = (
            RawMinuteCache(Path(p["raw_cache"]), symbols, p["minute_partitions"]).batches(
                start, end
            )
            if p.get("raw_cache")
            else iter_batches(archive, symbols, start, end)
        )
        replay = replay_minutes(
            batches,
            symbols,
            cast(MinuteArm, job["arm"]),
            cost_bps=job["cost"],
            decision_minutes=p["decision_minutes"],
            label_minutes=p["label_minutes"],
            training_sessions=p["training_sessions"],
            refit_minutes=p["refit_minutes"],
            max_fits=p["max_fits"],
            seed=p["seed"],
            risk_config=ActiveRiskConfig.model_validate(p["risk"]),
            daily_targets=context,
            actions=actions,
            trade_start=trade_start,
            event_sink=sink,
            feature_provider=feature_cache.values if feature_cache else None,
        )
    finally:
        for h in handles.values():
            h.close()
    rows = list(daily.values())
    for r in rows:
        r["exposure"] /= r["minutes"]
    with (destination / "daily.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(
        id=job["id"],
        kind=job["kind"],
        arm=job["arm"],
        cost_bps=job["cost"],
        start=job["trade_start"],
        end=job["end"],
        **metrics(rows, 100000),
        ending_equity=rows[-1]["equity"],
        intraday_max_drawdown=intraday_dd,
        minimum_cash=minimum_cash,
        accounting_residual_max=residual_max,
        failed_liquidations=replay["failed_liquidations"],
        first_fill=first_trade,
        fit_attempts=replay["refit_checkpoint"]["fit_count"],
        activated_models=sum(
            e["event"] in ("activated", "initial_activated") for e in replay["refits"]
        ),
        refit_events=dict(Counter(e["event"] for e in replay["refits"])),
        decision_counts=dict(counts),
        risk_reason_counts=dict(reasons),
        incomplete_minutes=sum(r["incomplete_minutes"] for r in rows),
    )
    metadata = {
        k: v for k, v in replay.items() if k not in ("records", "fills", "decisions", "missed")
    }
    write(destination / "model-evidence.json", json.loads(json.dumps(metadata, default=str)))
    evidence = {
        f.name: hashlib.sha256(f.read_bytes()).hexdigest()
        for f in destination.iterdir()
        if f.is_file()
    }
    value = {
        "protocol_sha256": digest(p),
        "job": job,
        "summary": summary,
        "evidence_sha256": evidence,
    }
    write(receipt, value)
    (destination / "receipt.sha256").write_text(digest(value))
    return summary
