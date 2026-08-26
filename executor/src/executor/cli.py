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
import sys
from collections.abc import Sequence
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
            "Execute the AMAT strategy against an Alpaca PAPER account. "
            "Reads settings from the environment; never from flags, so that "
            "credentials stay out of shell history and process listings."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    subparsers.add_parser("account", help="Print the paper account and its positions as JSON.")
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
    return parser


def _today() -> dt.date:
    return dt.datetime.now(_EXCHANGE_ZONE).date()


def _command_check(settings: ExecutorSettings) -> int:
    """Prove the deployment works without placing an order."""
    from executor.broker import AlpacaPaperClient  # noqa: PLC0415

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

    client = AlpacaPaperClient(settings.trading_key_id, settings.trading_secret_key)

    account = client.get_account()
    print("\nbroker (paper)")
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
    from executor.broker import AlpacaPaperClient  # noqa: PLC0415

    client = AlpacaPaperClient(settings.trading_key_id, settings.trading_secret_key)
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
    from executor.broker import AlpacaPaperClient  # noqa: PLC0415

    client = AlpacaPaperClient(settings.trading_key_id, settings.trading_secret_key)
    today = _today()
    sessions = client.get_calendar(today, today + dt.timedelta(days=14))
    print(f"exchange sessions {today} .. {today + dt.timedelta(days=14)}")
    for session in sessions:
        marker = "  <- today" if session.date == today else ""
        early = "  (early close)" if session.close_local < dt.time(16, 0) else ""
        print(f"  {session.date}  {session.open_local}-{session.close_local}{early}{marker}")
    return _EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    try:
        settings = ExecutorSettings.from_environment()
        settings.ensure_directories()

        if args.command == "daemon":
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
