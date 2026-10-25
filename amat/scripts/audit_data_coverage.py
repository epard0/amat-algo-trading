"""Read-only, hash-verified field coverage for a declared strategy universe."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import tomllib
from pathlib import Path

import polars as pl


def audit(snapshot: Path, config: Path, target_start: dt.date) -> dict:
    manifest = json.loads((snapshot / "manifest.json").read_text())
    strategy = tomllib.loads(config.read_text())
    symbols = sorted(
        {s for cluster in strategy["universe"]["clusters"] for s in cluster["symbols"]}
    )
    rows = []
    for partition, digest in manifest["partition_sha256"].items():
        path = snapshot / partition
        if not path.resolve().is_relative_to(snapshot.resolve()):
            raise ValueError("partition outside snapshot")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"partition digest mismatch: {partition}")
        data = pl.read_parquet(path)
        date = "ex_date" if partition == "actions.parquet" else "session_date"
        for symbol in symbols:
            sample = data.filter(pl.col("symbol") == symbol)
            first, last = sample[date].min(), sample[date].max()
            for field, dtype in sample.schema.items():
                if field in {date, "symbol"}:
                    continue
                series = sample[field]
                rows.append(
                    {
                        "symbol": symbol,
                        "partition": partition,
                        "field": field,
                        "provider": manifest["provider"],
                        "feed": manifest["feed"],
                        "rows": sample.height,
                        "nulls": series.null_count(),
                        "nonfinite": int((~series.is_finite()).sum())
                        if dtype.is_numeric()
                        else None,
                        "first": str(first) if first else None,
                        "last": str(last) if last else None,
                        "missing_requested_start": first is None or first > target_start,
                        "duplicate_symbol_dates": (
                            sample.height - sample.unique(subset=["symbol", date]).height
                            if partition != "actions.parquet"
                            else None
                        ),
                        "partition_sha256": digest,
                    }
                )
    return {
        "schema": "data-coverage/1",
        "snapshot_id": manifest["snapshot_id"],
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "strategy_version": strategy["version"],
        "target_start": str(target_start),
        "snapshot_end": manifest["last_session"],
        "symbols": symbols,
        "coverage": rows,
        "limits": [
            "Coverage describes stored data; it does not certify corporate-action completeness.",
            "No provider history before the first stored date is inferred.",
            "Publication timestamps and historical universe membership require separate sources.",
            "Calendar completeness is not established by duplicate and null checks.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--target-start", type=dt.date.fromisoformat, default=dt.date(2012, 1, 1))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.snapshot, args.config, args.target_start)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
