"""Command line: run the daemon, or drive one phase by hand.

The one-shot commands exist so that every step the daemon takes can be
rehearsed and inspected by an operator without waiting for a market open, and
so that a missed phase can be run deliberately rather than by restarting the
daemon and hoping.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from executor.config import ExecutorSettings
from executor.daemon import Daemon, run_daemon
from executor.errors import ExecutorError
from executor.plan_source import build_plan
from executor.report import prune_old_reports

__all__ = ["main"]

_EXIT_OK = 0
_EXIT_ERROR = 1

_EXCHANGE_ZONE = ZoneInfo("America/New_York")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="executor",
        description=(
            "Execute the AMAT strategy against the configured Alpaca account. "
            "Reads settings from the environment; never from flags, so that "
            "credentials stay out of shell history and process listings."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Plan, monitor and execute automatically from configuration.")
    status = subparsers.add_parser("status", help="Read the latest automatic order registry.")
    status.add_argument("--state-root", type=Path, default=Path(os.getenv("STATE_ROOT", "state")))
    status.add_argument("--json", action="store_true", help="Print the recorded registry as JSON.")
    health = subparsers.add_parser(
        "health", help="Check durable daemon progress without API calls."
    )
    health.add_argument("--state-root", type=Path, default=Path(os.getenv("STATE_ROOT", "state")))
    health.add_argument("--ready", action="store_true", help="Also check local order readiness.")
    subparsers.add_parser(
        "reconcile-submissions",
        help="Resolve uncertain receipts only when broker lookup confirms the original order.",
    )
    subparsers.add_parser(
        "notification-test", help="Send a test to the primary notification channel."
    )
    subparsers.add_parser(
        "mail-test", help="Send a configuration test to the configured recipients."
    )

    subparsers.add_parser(
        "daemon",
        help="Run the market-aware loop until signalled. This is the VPS entry point.",
    )
    subparsers.add_parser(
        "check",
        help=(
            "Verify settings, credentials, broker reachability, and that the "
            "strategy can form a plan. Places no orders. Run this first."
        ),
    )
    subparsers.add_parser("account", help="Print the configured account and its positions as JSON.")
    subparsers.add_parser("calendar", help="Print the next two weeks of exchange sessions.")

    submit = subparsers.add_parser(
        "submit", help="Run the submit phase once, now, for a given session."
    )
    submit.add_argument(
        "--session",
        type=dt.date.fromisoformat,
        help="Exchange session date (default: today in America/New_York).",
    )

    report = subparsers.add_parser(
        "report", help="Run the report phase once: record equity, write, mail, prune."
    )
    report.add_argument(
        "--session",
        type=dt.date.fromisoformat,
        help="Exchange session date (default: today in America/New_York).",
    )

    prune = subparsers.add_parser("prune", help="Apply the retention window now.")
    prune.add_argument(
        "--retain-days",
        type=int,
        help="Override REPORT_RETENTION_DAYS for this invocation.",
    )
    trades = subparsers.add_parser(
        "trades", help="Rebuild local trade metrics without broker access"
    )
    trades.add_argument("--state-root", type=Path, default=Path("state"))
    return parser


def _today() -> dt.date:
    return dt.datetime.now(_EXCHANGE_ZONE).date()


def _command_check(settings: ExecutorSettings) -> int:
    """Prove the deployment works without placing an order."""
    from executor.broker import create_client  # noqa: PLC0415

    print("settings")
    print(f"  amat root        {settings.amat_root}")
    print(f"  strategy config  {settings.strategy_config}")
    print(f"  snapshot         {settings.snapshot}")
    print(f"  news assets      {settings.news_assets or 'disabled'}")
    print(f"  state root       {settings.state_root}")
    print(f"  retention        {settings.report_retention_days} trading days")
    print(f"  dry run          {settings.dry_run}")
    print(f"  smtp             {'configured' if settings.smtp else 'NOT configured'}")
    if settings.smtp is not None:
        print(f"    host           {settings.smtp.host}:{settings.smtp.port}")
        print(f"    recipients     {', '.join(settings.smtp.recipients)}")

    client = create_client(settings)

    account = client.get_account()
    print(f"\nbroker ({settings.trading_mode})")
    print(f"  account          {account.account_number}")
    print(f"  status           {account.status}")
    print(f"  equity           {account.equity:,.2f}")
    print(f"  cash             {account.cash:,.2f}")
    print(f"  tradable         {account.is_tradable}")

    clock = client.get_clock()
    print(f"  market open now  {clock.is_open}")
    print(f"  next open        {clock.next_open.astimezone(_EXCHANGE_ZONE)}")
    print(f"  next close       {clock.next_close.astimezone(_EXCHANGE_ZONE)}")

    positions = client.position_quantities()
    print(f"  positions        {len(positions)}")
    for symbol, quantity in sorted(positions.items()):
        print(f"    {symbol:<6}{quantity:>14}")

    print("\nstrategy — building a plan (this contacts the market-data API)")
    plan = build_plan(
        amat_root=settings.amat_root,
        strategy_config=settings.strategy_config,
        snapshot=settings.snapshot,
        equity=account.equity,
        positions=positions,
        offline=False,
        news_assets=settings.news_assets,
        plan_command=settings.plan_command,
    )
    print(f"  strategy         {plan.strategy_name} v{plan.strategy_version} [{plan.engine}]")
    print(f"  as of session    {plan.as_of_session}")
    print(f"  earliest exec    {plan.earliest_execution_session}")
    print(f"  expires          {plan.expires_at.isoformat()}")
    print(f"  snapshot         {plan.snapshot_id} (+{plan.appended_sessions} live)")
    print(f"  gross target     {plan.gross_target_exposure:.2%}")
    print(f"  intents          {len(plan.intents)}")
    for intent in plan.intents:
        print(
            f"    {intent.symbol:<6}{intent.side:<6}{intent.quantity:>12,.4f}"
            f"{intent.notional:>14,.2f}"
        )
    if plan.unrecognized_holdings:
        print(f"  UNRECOGNIZED     {', '.join(plan.unrecognized_holdings)}")
        print("    The submit phase will refuse to trade until this is resolved.")

    print("\nOK — nothing was submitted.")
    return _EXIT_OK


def _command_account(settings: ExecutorSettings) -> int:
    from executor.broker import create_client  # noqa: PLC0415

    client = create_client(settings)
    account = client.get_account()
    positions = client.list_positions()
    print(
        json.dumps(
            {
                "account": {
                    "account_number": account.account_number,
                    "status": account.status,
                    "equity": str(account.equity),
                    "cash": str(account.cash),
                    "buying_power": str(account.buying_power),
                    "tradable": account.is_tradable,
                },
                "positions": [
                    {
                        "symbol": position.symbol,
                        "quantity": str(position.quantity),
                        "market_value": str(position.market_value),
                        "unrealized_pl": str(position.unrealized_pl),
                    }
                    for position in positions
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return _EXIT_OK


def _command_calendar(settings: ExecutorSettings) -> int:
    from executor.broker import create_client  # noqa: PLC0415

    client = create_client(settings)
    today = _today()
    sessions = client.get_calendar(today, today + dt.timedelta(days=14))
    print(f"exchange sessions {today} .. {today + dt.timedelta(days=14)}")
    for session in sessions:
        marker = "  <- today" if session.date == today else ""
        early = "  (early close)" if session.close_local < dt.time(16, 0) else ""
        print(f"  {session.date}  {session.open_local}-{session.close_local}{early}{marker}")
    return _EXIT_OK


def _freshness(observed_at: str | None) -> str:
    if not observed_at:
        return "unavailable"
    stamp = dt.datetime.fromisoformat(observed_at)
    if stamp.tzinfo is None:
        return "unavailable"
    age = (dt.datetime.now(dt.UTC) - stamp).total_seconds()
    return "fresh" if 0 <= age <= 720 else "stale"  # noqa: PLR2004 - three polling intervals


def _command_status(state_root: Path, *, as_json: bool) -> int:
    path = state_root / "order-registry.json"
    if not path.exists():
        print("No order observation yet. Start executor run or check the service logs.")
        return _EXIT_ERROR
    try:
        registry = json.loads(path.read_text())
        portfolio_path = state_root / "portfolio-monitor.json"
        registry["portfolio"] = (
            json.loads(portfolio_path.read_text())
            if portfolio_path.exists()
            else {"status": "observation_unavailable"}
        )
        registry["freshness"] = {
            "orders": _freshness(registry.get("observed_at")),
            "portfolio": _freshness(registry["portfolio"].get("observed_at")),
        }
        operations_path = state_root / "operations.json"
        registry["operations"] = (
            json.loads(operations_path.read_text())
            if operations_path.exists()
            else {"status": "observation_unavailable"}
        )
        registry["freshness"]["operations"] = _freshness(registry["operations"].get("observed_at"))
        if as_json:
            print(json.dumps(registry, indent=2))
        else:
            print(f"Last observed: {registry['observed_at']}")
            print(f"Observation freshness: {registry['freshness']}")
            print(f"Market: {'open' if registry['market_is_open'] else 'closed'}")
            portfolio = registry["portfolio"]
            print(f"Portfolio: {portfolio['status']} (cash/share changes since baseline)")
            if portfolio.get("reason"):
                print(f"  reason: {portfolio['reason']}")
            if portfolio.get("status") == "discrepancy":
                print(f"  cash difference: {portfolio['cash_difference']}")
                print(f"  share differences: {portfolio['position_differences']}")
            _print_operations(registry["operations"])
            for row in registry["orders"].values():
                if not row["terminal"]:
                    print(
                        f"{row['symbol']} {row['side']}: {row['registry_status']} "
                        f"(broker: {row['broker_status']}, "
                        f"remaining: {row['remaining_quantity']})"
                    )
    except (OSError, ValueError, KeyError, TypeError):
        print("Order registry is unreadable; check service logs.", file=sys.stderr)
        return _EXIT_ERROR
    return _EXIT_OK


def _print_operations(operations: dict[str, object]) -> None:
    if operations.get("status") != "observed":
        print("Strategy monitoring: observation_unavailable")
        return
    deployment = operations["deployment"]
    if not isinstance(deployment, dict):
        raise ValueError("invalid deployment observation")
    print(
        f"Strategy: {deployment['state']} (shadow monitoring; "
        f"submission {deployment['submission_mode']})"
    )
    print(f"Operational incidents: {operations['active_incident_count']}")
    flags = operations["flags"]
    if isinstance(flags, list):
        for flag in flags:
            print(f"  {flag['kind']}: {flag['status']}")


def _command_reconcile(settings: ExecutorSettings) -> int:
    import fcntl  # noqa: PLC0415

    from executor.broker import create_client  # noqa: PLC0415
    from executor.safety import reconcile_submissions  # noqa: PLC0415

    with (settings.state_root / "submission.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExecutorError("another submission/reconciliation holds the lock") from exc
        client = create_client(settings)
        reconciled = reconcile_submissions(settings.state_root, client)
        print(json.dumps(reconciled, indent=2))
        return _EXIT_ERROR if reconciled["unresolved"] else _EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0912, PLR0915 - compatibility dispatch
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments in (["--help"], ["-h"]):
        print(
            "executor [run | status]\n\n"
            "  run       Automatically plan, submit when permitted, and monitor orders.\n"
            "            This is the default when no command is supplied.\n"
            "  status    Show recorded order states; --json for machine-readable output.\n\n"
            "Configuration: environment variables / Docker .env file.\n"
            "Advanced compatibility commands: executor --advanced-help"
        )
        return _EXIT_OK
    parser = _build_parser()
    if arguments == ["--advanced-help"]:
        parser.print_help()
        return _EXIT_OK
    args = parser.parse_args(arguments or ["run"])
    _configure_logging(args.verbose)

    if args.command == "status":
        return _command_status(args.state_root, as_json=args.json)

    if args.command == "health":
        from executor.health import health_status, readiness_status  # noqa: PLC0415

        try:
            result = (
                readiness_status(ExecutorSettings.from_environment())
                if args.ready
                else health_status(args.state_root)
            )
        except (ExecutorError, OSError) as exc:
            print(f"executor: readiness check failed: {type(exc).__name__}", file=sys.stderr)
            return _EXIT_ERROR
        print(json.dumps(result, indent=2))
        return _EXIT_OK if result.get("ready_for_orders", result["healthy"]) else _EXIT_ERROR

    if args.command == "trades":
        from executor.report.trade_monitor import rebuild_monitor  # noqa: PLC0415

        try:
            payload = rebuild_monitor(args.state_root)
        except (OSError, ValueError, TypeError, KeyError, ExecutorError) as exc:
            print(f"executor: trade monitor: {exc}", file=sys.stderr)
            return _EXIT_ERROR
        print(json.dumps(payload["summary"], indent=2))
        print(f"Trade history: {(args.state_root / 'trade-monitor.json').resolve()}")
        return _EXIT_OK

    try:
        settings = ExecutorSettings.from_environment()
        settings.ensure_directories()

        if args.command in {"mail-test", "notification-test"}:
            from executor.alerts import notify  # noqa: PLC0415

            accepted = notify(
                settings,
                "mail-test",
                "AMAT executor notification test. "
                "Monitoring is connected; submission mode is unchanged.",
            )
            print(
                "Notification accepted; check the destination."
                if accepted
                else "Notification not accepted; inspect alerts/mail-test.json and configuration."
            )
            return _EXIT_OK if accepted else _EXIT_ERROR

        if args.command == "reconcile-submissions":
            return _command_reconcile(settings)

        if args.command in {"run", "daemon"}:
            return run_daemon(settings)
        if args.command == "check":
            return _command_check(settings)
        if args.command == "account":
            return _command_account(settings)
        if args.command == "calendar":
            return _command_calendar(settings)
        if args.command == "submit":
            daemon = Daemon(settings)
            session = args.session or _today()
            # A hand-run submit is off-schedule by construction, so it derives
            # its own lateness rather than claiming to be on time. Without this
            # an operator catching up on a missed morning would produce a
            # report that quietly describes intraday fills as opening fills.
            daemon.run_submit(session, minutes_late=daemon.minutes_past_schedule(session))
            return _EXIT_OK
        if args.command == "report":
            Daemon(settings).run_report(args.session or _today())
            return _EXIT_OK
        if args.command == "prune":
            removed = prune_old_reports(
                (settings.reports_dir, settings.plans_dir),
                retain_days=args.retain_days or settings.report_retention_days,
            )
            for path in removed:
                print(f"removed {path}")
            print(f"{len(removed)} file(s) removed")
            return _EXIT_OK
    except ExecutorError as exc:
        print(f"executor: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_ERROR
    except KeyboardInterrupt:
        return _EXIT_OK

    # `required=True` on the subparsers means argparse rejects an unknown
    # command before this point; `error` raises SystemExit rather than
    # returning, so a fallthrough `return` here would be unreachable code.
    # mypy knows `error` is NoReturn and rejects the return; ruff's flow
    # analysis does not, hence the suppression rather than a dead statement.
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
