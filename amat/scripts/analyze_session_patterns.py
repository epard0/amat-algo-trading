"""Measure repeatable overnight/intraday effects in an immutable snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amat.config import load_strategy_config  # noqa: E402
from amat.data.snapshot import load_manifest, load_panel  # noqa: E402
from amat.patterns import scan_session_patterns  # noqa: E402


def _json_safe(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=None, help="Optional strategy universe subset."
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--train-end", type=dt.date.fromisoformat, default=dt.date(2022, 12, 30))
    parser.add_argument("--round-trip-cost-bps", type=float, default=4.0)
    parser.add_argument("--false-discovery-rate", type=float, default=0.10)
    parser.add_argument("--minimum-observations", type=int, default=60)
    args = parser.parse_args()

    manifest = load_manifest(args.snapshot)
    symbols = (
        load_strategy_config(args.config).strategy.universe.symbols
        if args.config is not None
        else manifest.symbols
    )
    panel, _ = load_panel(args.snapshot, symbols)
    patterns = scan_session_patterns(
        panel,
        train_end=args.train_end,
        round_trip_cost_bps=args.round_trip_cost_bps,
        false_discovery_rate=args.false_discovery_rate,
        minimum_observations=args.minimum_observations,
    )
    payload = {
        "schema_version": 1,
        "snapshot_id": manifest.snapshot_id,
        "snapshot_digest": manifest.partition_sha256,
        "symbols_tested": len(symbols),
        "hypotheses_tested": len(patterns),
        "train_end": args.train_end.isoformat(),
        "round_trip_cost_bps": args.round_trip_cost_bps,
        "false_discovery_rate": args.false_discovery_rate,
        "opening_price": "raw 09:30-09:35 VWAP rescaled to adjusted basis",
        "overnight_definition": "prior official close to next 09:30-09:35 VWAP",
        "warning": (
            "This snapshot has no 15:55-16:00 observations; results do not test a 4:55 PM fill."
        ),
        "robust_candidates": sum(pattern.robust_candidate for pattern in patterns),
        "patterns": [pattern.to_dict() for pattern in patterns],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"tested {len(patterns)} hypotheses over {len(symbols)} symbols; "
        f"{payload['robust_candidates']} survived train/validation and BH correction"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
