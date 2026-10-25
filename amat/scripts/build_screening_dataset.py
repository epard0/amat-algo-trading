"""Resumable read-only Alpaca daily universe and observed execution-window archive.

No imputation, synthetic fills, automatic promotion or trading access. The curated
universe is selected before measurements; quality failures stay in the audit.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import hashlib
import json
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from amat.data.alpaca import AlpacaMarketData
from amat.data.snapshot import (
    SnapshotManifest,
    _build_actions_frame,
    _build_daily_frame,
    _build_window_frame,
    _check_daily_quality,
    load_manifest,
    partition_digest,
)
from amat.errors import DataIntegrityError
from amat.paths import reserve_immutable_directory


class ConcurrentMarketData(AlpacaMarketData):
    """Share one rate allowance across bounded concurrent GET requests."""

    def __init__(self) -> None:
        super().__init__(timeout_seconds=30)
        self._rate_lock = threading.RLock()

    def _throttle(self) -> None:
        with self._rate_lock:
            super()._throttle()


def save(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def _fetch_daily_batches(
    client: AlpacaMarketData,
    symbols: list[str],
    start: dt.date,
    end: dt.date,
    stage: Path,
) -> None:
    batch_size = 25
    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset : offset + batch_size]
        directory = stage / f"batch-{offset // batch_size:03}"
        directory.mkdir(exist_ok=True)
        if (directory / "complete.json").exists():
            continue
        print(f"Daily batch {offset // batch_size + 1}: {len(batch)} symbols", flush=True)
        raw = client.daily_bars(batch, start, end - dt.timedelta(days=1), adjustment="raw")
        adjusted = client.daily_bars(batch, start, end - dt.timedelta(days=1), adjustment="all")
        # Retain vendor rows before quality decisions, including rejected observations.
        for name, records in [("raw", raw), ("adjusted", adjusted)]:
            if records:
                pl.DataFrame([dataclasses.asdict(r) for r in records]).write_parquet(
                    directory / (name + ".parquet")
                )
        dividends, splits = client.corporate_actions(batch, start, end - dt.timedelta(days=1))
        accepted, actions, audit = [], [], []
        for symbol in batch:
            r = [b for b in raw if b.symbol == symbol]
            a = [b for b in adjusted if b.symbol == symbol]
            try:
                frame = _build_daily_frame(r, a)
                if frame.height != len(r) or frame.height != len(a):
                    raise ValueError("raw/adjusted key coverage differs")
                _check_daily_quality(frame, [s for s in splits if s.symbol == symbol])
                action = _build_actions_frame(
                    [d for d in dividends if d.symbol == symbol],
                    [s for s in splits if s.symbol == symbol],
                )
                accepted.append(frame)
                actions.append(action)
                audit.append(
                    {
                        "symbol": symbol,
                        "status": "accepted",
                        "rows": frame.height,
                        "first": str(frame["session_date"].min()),
                        "last": str(frame["session_date"].max()),
                    }
                )
            except (ValueError, ArithmeticError, DataIntegrityError) as exc:
                audit.append({"symbol": symbol, "status": "quarantined", "reason": str(exc)})
        if accepted:
            pl.concat(accepted).write_parquet(directory / "daily.parquet")
            pl.concat(actions).write_parquet(directory / "actions.parquet")
        save(directory / "complete.json", audit)
        print(f"Accepted {len(accepted)}/{len(batch)}; all failures retained", flush=True)


def _collect_windows(
    client: AlpacaMarketData,
    daily: pl.DataFrame,
    admitted: list[str],
    stage: Path,
    execution_start: dt.date,
) -> pl.DataFrame:
    windows_dir = stage / "windows"
    windows_dir.mkdir(exist_ok=True)
    sessions = (
        daily.filter((pl.col("symbol") == "SPY") & (pl.col("session_date") >= execution_start))[
            "session_date"
        ]
        .unique()
        .sort()
        .to_list()
    )

    def fetch_window(session: dt.date) -> dt.date:
        path = windows_dir / f"{session}.parquet"
        if path.exists():
            return session
        bars = client.execution_window_bars(
            admitted, session, window_start=dt.time(9, 30), window_end=dt.time(9, 35)
        )
        frame = _build_window_frame(bars)
        temporary = path.with_suffix(".tmp")
        frame.write_parquet(temporary)
        temporary.replace(path)
        return session

    remaining = [
        session for session in sessions if not (windows_dir / f"{session}.parquet").exists()
    ]
    completed = len(sessions) - len(remaining)
    print(
        f"Execution windows: {completed} cached, {len(remaining)} remaining; "
        "four bounded readers share one rate limit",
        flush=True,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_window, session) for session in remaining]
        for future in concurrent.futures.as_completed(futures):
            session = future.result()
            completed += 1
            if completed % 25 == 0 or completed == len(sessions):
                print(
                    f"Execution windows {completed}/{len(sessions)}; "
                    f"latest completed request {session}",
                    flush=True,
                )
    return pl.concat([pl.read_parquet(p) for p in sorted(windows_dir.glob("*.parquet"))]).sort(
        "session_date", "symbol"
    )


def _validated_batches(stage: Path) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, object]]]:
    daily = pl.concat(
        [pl.read_parquet(p) for p in sorted(stage.glob("batch-*/daily.parquet"))]
    ).sort("session_date", "symbol")
    actions = pl.concat(
        [pl.read_parquet(p) for p in sorted(stage.glob("batch-*/actions.parquet"))]
    ).sort("ex_date", "symbol")
    audit = [
        row
        for p in sorted(stage.glob("batch-*/complete.json"))
        for row in json.loads(p.read_text())
    ]
    # Match the native event loader: identical repeats are one observation;
    # only distinct rates/ratios on the same event key require reconciliation.
    actions = actions.unique(subset=["symbol", "ex_date", "kind", "value"])
    duplicate_actions = actions.filter(pl.struct("symbol", "ex_date", "kind").is_duplicated())
    ambiguous = set(duplicate_actions["symbol"].to_list())
    if ambiguous:
        for row in audit:
            if row["symbol"] in ambiguous:
                row.update(
                    status="quarantined",
                    reason="ambiguous duplicate corporate actions; requires issuer reconciliation",
                )
        daily = daily.filter(~pl.col("symbol").is_in(sorted(ambiguous)))
        actions = actions.filter(~pl.col("symbol").is_in(sorted(ambiguous)))
    return daily, actions, audit


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--universe", type=Path)
    parser.add_argument(
        "--resume-dir",
        type=Path,
        help="Reuse a matching local checkpoint without downloading daily bars again",
    )
    parser.add_argument("--snapshot-id", default="alpaca-sip-diverse561-2026-10-19-verified")
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    root = args.root.resolve()
    definition = args.universe or root / "configs/data/screening-universe.json"
    spec = json.loads(definition.read_text())
    start = dt.date.fromisoformat(spec["start"])
    end = dt.date.fromisoformat(spec["end_exclusive"])
    if end > dt.datetime.now(ZoneInfo("America/New_York")).date():
        raise ValueError("end must exclude the current incomplete session")
    symbols = [e["symbol"] for e in spec["entries"]]
    if len(symbols) != len(set(symbols)):
        raise ValueError("duplicate requested symbols")
    identity = hashlib.sha256(definition.read_bytes()).hexdigest()
    target = root / "data/snapshots" / args.snapshot_id
    if target.exists():
        print(load_manifest(target).to_json(), flush=True)
        return
    stage = (
        args.resume_dir.resolve()
        if args.resume_dir
        else root / "data/interim" / (args.snapshot_id + ".partial")
    )
    if not stage.resolve().is_relative_to((root / "data/interim").resolve()):
        raise ValueError("checkpoint must remain under data/interim")
    stage.mkdir(parents=True, exist_ok=True)
    marker = stage / "protocol.json"
    protocol = {
        "schema": "diverse-ingestion/1",
        "universe_sha256": identity,
        "spec": spec,
        "feed": "sip",
        "execution_scope": "Observed 09:30–09:35 America/New_York windows from "  # noqa: RUF001
        + spec["execution_start"],
        "quality_policy": (
            "Reject impossible OHLC and unexplained adjusted jumps per symbol; "
            "never drop failures from the audit. No backfill or manufactured prices."
        ),
    }
    if marker.exists() and json.loads(marker.read_text()) != protocol:
        raise ValueError("partial ingestion protocol changed")
    save(marker, protocol)
    client = ConcurrentMarketData()
    _fetch_daily_batches(client, symbols, start, end, stage)
    daily, actions, audit = _validated_batches(stage)
    admitted = sorted(daily["symbol"].unique().to_list())
    execution_start = dt.date.fromisoformat(spec["execution_start"])
    window = _collect_windows(client, daily, admitted, stage, execution_start)
    additional = sorted(set(admitted) - set(window["symbol"].unique().to_list()))
    if additional:
        extra_stage = stage / "additional-windows"
        extra_stage.mkdir(exist_ok=True)
        added = _collect_windows(client, daily, additional, extra_stage, execution_start)
        window = pl.concat([window, added]).unique(subset=["session_date", "symbol"])
    window = window.filter(pl.col("symbol").is_in(admitted)).sort("session_date", "symbol")
    all_sessions = daily["session_date"].unique().sort()
    directory = reserve_immutable_directory(root, Path("data/snapshots") / args.snapshot_id)
    for name, frame in [("daily", daily), ("window", window), ("actions", actions)]:
        frame.write_parquet(directory / f"{name}.parquet")
    manifest = SnapshotManifest(
        snapshot_id=args.snapshot_id,
        provider="alpaca",
        feed="sip",
        as_of=dt.datetime.now(dt.UTC),
        symbols=tuple(admitted),
        first_session=all_sessions[0],
        last_session=all_sessions[-1],
        session_count=len(all_sessions),
        daily_rows=daily.height,
        window_rows=window.height,
        action_rows=actions.height,
        execution_window_start=dt.time(9, 30),
        execution_window_end=dt.time(9, 35),
        exchange_timezone="America/New_York",
        partition_sha256={p.name: partition_digest(p) for p in directory.glob("*.parquet")},
        window_coverage=window.height / (len(all_sessions) * len(admitted)),
    )
    (directory / "manifest.json").write_text(manifest.to_json())
    eligible_cells = daily.filter(pl.col("session_date") >= execution_start).height
    save(
        directory / "coverage.json",
        {
            **protocol,
            "symbol_audit": audit,
            "requested_symbols": len(symbols),
            "accepted_symbols": len(admitted),
            "daily_rows": daily.height,
            "window_rows": window.height,
            "execution_window_coverage_on_observed_daily_cells": window.height / eligible_cells
            if eligible_cells
            else 0,
            "earlier_period_policy": (
                "Daily screening only before execution_start. Missing windows never fill."
            ),
            "survivorship": (
                "Curated current symbols; not delisting-complete "
                "or an unbiased historical stock-universe test."
            ),
        },
    )
    load_manifest(directory)
    print(
        f"Complete: {directory}, {len(admitted)} assets, {daily.height} daily rows, "
        f"{window.height} observed windows",
        flush=True,
    )


if __name__ == "__main__":
    main()
