"""Extend immutable research datasets through the read-only Alpaca client."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from amat.data import AlpacaMarketData, IngestionPlan, ingest_snapshot
from amat.data.alpaca import WindowBar
from amat.data.ingest import _write_checkpoint
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from autoresearch.intraday.data import Panel, fetch
from autoresearch.workspace import digest_file, write_json

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data/research-datasets.json"
ORIGINAL_DAILY = ROOT / "data/snapshots/alpaca-sip-2026-08-11"
ORIGINAL_MINUTE = ROOT.parent.parent / "amat-research/intraday-data-sip-2022-2025"


def extend_daily(base: Path, end: dt.date) -> tuple[Path, dict]:
    manifest = load_manifest(base)
    target = ROOT / "data/snapshots" / f"alpaca-sip-core-{end}"
    if target.exists():
        return normalize_daily(target, base)
    if end <= manifest.last_session:
        raise ValueError("End must follow the base dataset's last session")
    # Refresh the entire adjusted series; never append differently adjusted histories.
    plan = IngestionPlan(
        manifest.symbols,
        manifest.first_session,
        end,
        window_start=manifest.execution_window_start,
        window_end=manifest.execution_window_end,
        exchange_timezone=manifest.exchange_timezone,
    )
    checkpoint = ROOT / "data/interim" / f"{target.name}.windows.json"
    if not checkpoint.exists():
        windows = pl.read_parquet(base / "window.parquet")
        collected = {}
        timezone = ZoneInfo(plan.exchange_timezone)
        for key, rows in windows.partition_by("session_date", as_dict=True).items():
            session = key[0]
            if set(rows["symbol"]) == set(plan.symbols):
                collected[session] = [
                    WindowBar(
                        symbol=row["symbol"],
                        session_date=session,
                        window_start=dt.datetime.combine(session, plan.window_start, timezone),
                        window_end=dt.datetime.combine(session, plan.window_end, timezone),
                        vwap=row["vwap"],
                        volume=row["volume"],
                    )
                    for row in rows.to_dicts()
                ]
        _write_checkpoint(checkpoint, plan, collected)
    target, _updated = ingest_snapshot(
        AlpacaMarketData(feed=manifest.feed),
        plan,
        root=ROOT,
        snapshot_id=target.name,
        feed=manifest.feed,
        checkpoint_path=checkpoint,
        progress=lambda msg: print(msg, flush=True),
    )
    return normalize_daily(target, base)


def normalize_daily(raw: Path, base: Path) -> tuple[Path, dict]:
    """Apply one issuer-confirmed correction in a NEW derived snapshot."""
    manifest = load_manifest(raw)
    original = load_manifest(base)
    target = raw.with_name(raw.name + "-verified")
    if not target.exists():
        actions = pl.read_parquet(raw / "actions.parquet")
        match = (
            (pl.col("symbol") == "SHY")
            & (pl.col("ex_date") == dt.date(2021, 12, 1))
            & (pl.col("kind") == "cash_dividend")
        )
        observed = sorted(actions.filter(match)["value"].to_list())
        if observed not in ([0.018007, 0.031906], [0.049913]):
            raise ValueError(f"SHY issuer correction no longer matches source values: {observed}")
        corrected = pl.concat(
            [
                actions.filter(~match),
                pl.DataFrame(
                    {
                        "symbol": ["SHY"],
                        "ex_date": [dt.date(2021, 12, 1)],
                        "kind": ["cash_dividend"],
                        "value": [0.049913],
                    },
                    schema=actions.schema,
                ),
            ]
        ).sort("ex_date", "symbol", "kind")
        staging = target.with_name(target.name + ".partial")
        staging.mkdir(parents=True, exist_ok=True)
        for name in ("daily.parquet", "window.parquet"):
            shutil.copy2(raw / name, staging / name)
        corrected.write_parquet(staging / "actions.parquet")
        updated = manifest.model_copy(
            update={
                "snapshot_id": target.name,
                "action_rows": corrected.height,
                "partition_sha256": {
                    **manifest.partition_sha256,
                    "actions.parquet": digest_file(staging / "actions.parquet"),
                },
            }
        )
        (staging / "manifest.json").write_text(updated.to_json())
        write_json(
            staging / "extension.json",
            {
                "base": str(base),
                "base_manifest_sha256": digest_file(base / "manifest.json"),
                "raw_snapshot": str(raw),
                "raw_manifest_sha256": digest_file(raw / "manifest.json"),
                "policy": "Full daily/action refresh; complete historical windows reused.",
                "added_sessions": updated.session_count - original.session_count,
                "added_daily_rows": updated.daily_rows - original.daily_rows,
                "correction": {
                    "symbol": "SHY",
                    "ex_date": "2021-12-01",
                    "observed": observed,
                    "total": 0.049913,
                    "source": "https://www.ishares.com/us/literature/tax-information/"
                    "2021-ishares-distribution-summary.pdf",
                    "page": 3,
                    "reason": "Issuer reports total 0.049913 and 2021 total 0.223854; "
                    "the two vendor rows are components, not alternatives.",
                },
            },
        )
        panel, _ = load_panel(staging, manifest.symbols)
        load_corporate_actions(staging, manifest.symbols, sessions=panel.sessions)
        staging.rename(target)
    verified = load_manifest(target)
    panel, _ = load_panel(target, manifest.symbols)
    load_corporate_actions(target, manifest.symbols, sessions=panel.sessions)
    return target, verified.model_dump(mode="json")


def extend_minute(base: Path, daily: Path, end: dt.date) -> tuple[Path, dict]:
    previous = json.loads((base / "manifest.json").read_text())
    start = dt.date.fromisoformat(previous["end"]) + dt.timedelta(days=1)
    if start.month != 1 or start.day != 1:
        raise ValueError("Minute extension requires a base ending at a year boundary")
    target = ROOT / "data/minute" / f"alpaca-sip-2022-{end}"
    if target.exists():
        saved = json.loads((target / "manifest.json").read_text())
        for name, expected in saved["partitions"].items():
            if digest_file(target / name) != expected:
                raise ValueError("Existing minute dataset hash mismatch")
        return target, json.loads((target / "extension.json").read_text())
    daily_manifest = load_manifest(daily)
    if daily_manifest.last_session < end - dt.timedelta(days=4):
        raise ValueError("Update daily data first to obtain a current session calendar")
    staging = target.with_name(target.name + ".partial")
    staging.mkdir(parents=True, exist_ok=True)
    for name, expected in previous["partitions"].items():
        path = base / name
        if not path.resolve().is_relative_to(base.resolve()) or digest_file(path) != expected:
            raise ValueError("Base minute partition hash mismatch")
        shutil.copy2(path, staging / name)
        if path.with_suffix(".json").exists():
            shutil.copy2(path.with_suffix(".json"), (staging / name).with_suffix(".json"))
    print(f"Minute bars: {start} through {end - dt.timedelta(days=1)}", flush=True)
    added = fetch(staging, start, end - dt.timedelta(days=1))
    updated = dict(
        added, start=previous["start"], partitions={**previous["partitions"], **added["partitions"]}
    )
    write_json(staging / "manifest.json", updated)
    dates = pl.read_parquet(daily / "daily.parquet").filter(pl.col("symbol") == "SPY")
    write_json(
        staging / "calendar.json",
        {
            "dates": sorted(dates["session_date"].cast(pl.String).unique().to_list()),
            "source": str(daily),
            "source_sha256": digest_file(daily / "daily.parquet"),
        },
    )
    audit = Panel(staging, start=str(start), end=str(end - dt.timedelta(days=1))).audit
    counts = {
        name: pl.scan_parquet(staging / name).select(pl.len()).collect().item()
        for name in updated["partitions"]
    }
    detail = {
        "base": str(base),
        "base_manifest_sha256": digest_file(base / "manifest.json"),
        "rows": sum(counts.values()),
        "added_rows": sum(counts[name] for name in added["partitions"]),
        "extension_quality": audit,
        "research_usage": "2026 data available for explicit future validation; current intraday "
        "research still freezes development 2022-2024 and validation 2025.",
    }
    write_json(staging / "extension.json", detail)
    staging.rename(target)
    return target, detail


def inventory() -> None:
    for path in sorted((ROOT / "data/snapshots").glob("*/manifest.json")):
        data = json.loads(path.read_text())
        print(
            f"Daily  {len(data['symbols']):3} symbols  {data['first_session']}.."
            f"{data['last_session']}  {data['daily_rows']:,} rows  {path.parent.name}"
        )
    paths = [ORIGINAL_MINUTE / "manifest.json", *(ROOT / "data/minute").glob("*/manifest.json")]
    for path in paths:
        if path.is_file() and not path.parent.name.endswith(".partial"):
            data = json.loads(path.read_text())
            print(
                f"Minute {len(data['symbols']):3} symbols  {data['start']}..{data['end']}  "
                f"{len(data['partitions'])} partitions  {path.parent}"
            )
    if REGISTRY.exists():
        print(f"Dataset registry: {REGISTRY}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "update"], nargs="?", default="status")
    parser.add_argument("--only", choices=["daily", "minute"])
    parser.add_argument(
        "--end",
        type=dt.date.fromisoformat,
        default=dt.datetime.now(ZoneInfo("America/New_York")).date(),
        help="Exclusive date; defaults to today in New York",
    )
    args = parser.parse_args()
    if args.action == "status":
        inventory()
        return 0
    if args.end > dt.datetime.now(ZoneInfo("America/New_York")).date():
        parser.error("--end must exclude today's incomplete session")
    selected = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    errors = {}
    for kind in ("daily", "minute"):
        if args.only and args.only != kind:
            continue
        try:
            if kind == "daily":
                path, result = extend_daily(ORIGINAL_DAILY, args.end)
            else:
                path, result = extend_minute(
                    ORIGINAL_MINUTE, Path(selected.get("daily", ORIGINAL_DAILY)), args.end
                )
            selected[kind] = str(path)
            selected[kind + "_summary"] = result
            write_json(REGISTRY, selected)
            print(f"Ready: {path}", flush=True)
        except Exception as exc:
            errors[kind] = f"{type(exc).__name__}: {exc}"
            print(f"{kind} extension failed: {errors[kind]}", flush=True)
    write_json(
        ROOT / "data/last-update.json",
        {"end_exclusive": str(args.end), "errors": errors, "datasets": selected},
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
