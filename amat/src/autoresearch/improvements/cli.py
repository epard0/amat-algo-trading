"""Explicit commands for immutable strategy-improvement evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from amat.config.loading import load_strategy_config
from autoresearch.improvements.minute_data import audit_archive, extend_archive
from autoresearch.improvements.minute_study import minute_report, register_minute, run_minute_arm
from autoresearch.improvements.study import register, report, run_arm


def main() -> None:  # noqa: PLR0912 - explicit CLI dispatch
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "register",
            "daily",
            "report",
            "audit-minutes",
            "extend-minutes",
            "register-minute",
            "minute",
            "minute-report",
        ],
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm")
    parser.add_argument("--cost", type=int, choices=[2, 6, 10], default=2)
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2022, 1, 1))
    parser.add_argument("--end", type=dt.date.fromisoformat)
    args = parser.parse_args()
    symbols = tuple(
        map(
            str,
            load_strategy_config(
                args.root / "configs/strategy/amat_v13_return_target.toml"
            ).strategy.universe.symbols,
        )
    )
    value: Any
    if args.action == "register":
        if args.snapshot is None:
            parser.error("--snapshot required")
        value = register(args.root, args.snapshot, args.output)
    elif args.action == "daily":
        if args.arm is None:
            parser.error("--arm required")
        value = run_arm(args.output, args.arm, args.cost)["metrics"]
    elif args.action == "report":
        value = str(report(args.output))
    elif args.action == "audit-minutes":
        if args.archive is None:
            parser.error("--archive required")
        value = audit_archive(args.archive, symbols)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2, default=str))
    elif args.action == "extend-minutes":
        if args.archive is None or args.end is None:
            parser.error("--archive and --end required")
        value = extend_archive(args.archive, args.output, symbols, args.start, args.end)
    elif args.action == "register-minute":
        if args.archive is None or args.snapshot is None or args.end is None:
            parser.error("--archive, --snapshot and --end required")
        value = register_minute(
            args.root, args.archive, args.snapshot, args.output, args.start, args.end
        )
    elif args.action == "minute":
        if args.arm is None:
            parser.error("--arm required")
        value = run_minute_arm(args.output, args.arm, args.cost)
    else:
        value = str(minute_report(args.output))
    print(json.dumps(value, indent=2, default=str))  # noqa: T201 - explicit CLI output


if __name__ == "__main__":
    main()
