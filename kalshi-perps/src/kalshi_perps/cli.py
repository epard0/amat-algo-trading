"""Three commands: run, status, stop. Mode belongs in the configuration."""

import argparse
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx

from .config import Config
from .domain import canonical
from .runtime import Feed, Runner, build, lease, run


def status(config):
    if not config.database.exists():
        from .api import describe_strategy

        profile, _, _ = describe_strategy(config)
        if profile and profile.evidence.get("execution_status") == "blocked":
            return {
                "mode": config.mode,
                "state": "research_gated",
                "leverage_mode": config.raw.get("paper", {}).get("leverage_mode", "fixed"),
                "strategy": profile.name,
                "reason": "No robust walk-forward winner; no strategy paper trading",
                "withdrawals_enabled": False,
            }
        return {"mode": config.mode, "state": "not_started"}
    db = sqlite3.connect(f"file:{config.database}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT identity,body FROM state WHERE id=1").fetchone()
        identity, state = json.loads(row[0]), json.loads(row[1])
        if identity["mode"] != config.mode or identity["account"] != config.raw["account"]:
            raise ValueError("database account/mode mismatch")
        if config.mode == "paper":
            from .paper import equity, notional

            report = {
                "mode": "paper",
                "equity": str(equity(state)),
                "cash": state["cash"],
                "fees": state["fees"],
                "funding": state["funding"],
                "gross_notional": str(notional(state)),
                "positions": state["positions"],
                "halted": state["halted"],
            }
        else:
            report = {
                "mode": config.mode,
                "halted": state["halted"],
                "account": state["venue"],
                "orders": [
                    {"client_order_id": r[0], "status": r[1]}
                    for r in db.execute("SELECT id,status FROM orders")
                ],
            }
        if config.mode == "paper" and state.get("strategy_profile") == "doge-sol-adx14":
            from types import SimpleNamespace

            from .adx_forward import account_metrics

            view = SimpleNamespace(db=db, state=lambda: state)
            report.update(
                account_metrics(
                    view,
                    state.get("forward_start_ms", state["timestamp_ms"]),
                    state["timestamp_ms"],
                )
            )
            report["leverage_mode"] = "venue_max"
            report["tracking_report"] = str(config.database.parent / "adx-forward/status.md")
        if config.mode == "paper" and state.get("strategy_profile") == "doge-sol-trend-shadow":
            from types import SimpleNamespace

            from .shadow_forward import account_metrics

            report.update(
                account_metrics(
                    SimpleNamespace(db=db, state=lambda: state),
                    state["forward_start_ms"],
                    state["timestamp_ms"],
                )
            )
            report["leverage_mode"] = "fixed_1x"
            report["tracking_report"] = str(config.database.parent / "adx-forward/status.md")
        report["strategy"] = state.get(
            "strategy_profile", config.raw["strategy"].get("profile", "btc-eth-reference")
        )
        report["signal_history"] = state.get("signal_history")
        report["stop_requested"] = Path(str(config.database) + ".stop").exists()
        try:
            with lease(config.database):
                report["runner_active"] = False
        except ValueError:
            report["runner_active"] = True
        return report
    finally:
        db.close()


def stop(config, flatten):
    path = Path(str(config.database) + ".stop")
    path.parent.mkdir(parents=True, exist_ok=True)
    instruction = {
        "flatten": flatten,
        "id": str(uuid.uuid4()),
        "timestamp_ms": int(time.time() * 1000),
    }
    temporary = path.with_suffix(".stop.tmp")
    temporary.write_text(canonical(instruction))
    temporary.replace(path)
    try:
        with lease(config.database):
            if not config.database.exists():
                return {"state": "stopped", "positions": "account not started"}
            strategy, broker = build(config, with_strategy=False)
            try:
                if flatten:
                    quotes, now = None, None
                    if config.mode != "paper":
                        event = Feed(broker.transport, broker.instruments).snapshot(
                            int(config.raw["risk"]["quote_max_age_ms"])
                        )
                        quotes, now = event["quotes"], event["timestamp_ms"]
                    return Runner(config, strategy, broker).flatten(instruction["id"], quotes, now)
                if config.mode != "paper":
                    broker.cancel_all()
                return {"state": "stopped", "report": broker.report()}
            finally:
                broker.store.close()
    except ValueError as exc:
        if "runner already owns" not in str(exc):
            raise
        return {
            "state": "stop_requested",
            "flatten": flatten,
            "note": "Runner will process this request after its current bounded operation.",
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kalshi perps: run, inspect, or stop an account.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "status", "stop"):
        p = sub.add_parser(command)
        p.add_argument("--config", default="configs/paper.toml")
        p.add_argument(
            "--all",
            action="store_true",
            help="All leading strategies in a shared-data paper comparison",
        )
        p.add_argument(
            "--strategy", help="Named strategy preset; isolates its account and database"
        )
        if command == "run":
            p.add_argument(
                "--recording", type=Path, help="Paper replay of a recorded market stream"
            )
            p.add_argument(
                "--once",
                action="store_true",
                help="One forward observation instead of continuous polling",
            )
        if command == "stop":
            p.add_argument(
                "--flatten", action="store_true", help="Also attempt bounded reduce-only closes"
            )
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        if args.strategy:
            config = config.select_strategy(args.strategy)
        if args.all:
            if args.strategy:
                raise ValueError("--all and --strategy are mutually exclusive")
            if config.mode != "paper":
                raise ValueError("--all is paper-only")
            from .comparison import run_all, status_all, stop_all

            if args.command == "run":
                if args.recording:
                    raise ValueError("--all uses forward public data, not recordings")
                result = run_all(config, args.once)
            elif args.command == "status":
                result = status_all(config)
            else:
                result = stop_all(config, args.flatten)
        elif args.command == "run":
            result = run(config, args.recording, args.once)
        elif args.command == "status":
            result = status(config)
        else:
            result = stop(config, args.flatten)
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print(
            "Local runner stopped. Positions remain; use stop --flatten to close them.",
            file=sys.stderr,
        )
        return 130
    except (ValueError, RuntimeError, OSError, KeyError, httpx.HTTPError) as exc:
        print(f"kalshi-perps: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
