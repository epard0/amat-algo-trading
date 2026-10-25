"""The long-running, market-aware loop.

What this process does, once per trading day
--------------------------------------------
1. Shortly before the exchange opens, fetch the account and its positions,
   hand them to ``amat plan``, and submit the resulting intents as market DAY
   orders inside the strategy's configured opening window.
2. Shortly after the close, fetch the account again, record one equity
   observation, render the daily report, mail it, and prune old report files.

How it knows when
-----------------
From the broker's own calendar, never from a weekday check or a hardcoded
09:30. Holidays are absent from that calendar and half-days carry an early
close, so both are handled by asking rather than by deriving. The daemon holds
no timezone assumption of its own beyond the exchange zone the calendar is
expressed in, which means the VPS's own clock zone is irrelevant.

Why it sleeps in bounded slices
-------------------------------
A single ``sleep`` until the next event would be simpler and worse: it delays
SIGTERM handling by up to a day, and it trusts a duration computed before a
possible clock adjustment. The loop wakes at least every few minutes, re-reads
the calendar cheaply, and re-decides.

How late a phase may still run
------------------------------
Each phase carries an explicit last instant, not a fixed grace period. For the
submit phase that instant is derived from the session itself -- a fixed cutoff
before the *close* -- because the risk being managed is "do not put on the
day's rebalance minutes before the bell", and only the calendar knows when the
bell is. A half day therefore narrows the window automatically.

Within that window a late submit is allowed and recorded as late. The plan is
rebuilt from live account state at submission time and carries its own expiry,
which :mod:`executor.submit` refuses to trade past; lateness costs execution
quality against the backtest's assumed open fill, which is a caveat for the
report, not grounds for skipping the day's rebalance entirely.

Startup authorization
---------------------
Before the loop runs at all, the pinned strategy file's digest must be in
``AUTHORIZED_CONFIG_SHA256``. The same check happens again on every plan at
submit time (:mod:`executor.submit`), but checking it here too turns a
misconfigured deployment into a startup failure a human sees immediately,
rather than a quiet day of refused submissions discovered only in the report.

Restart safety
--------------
Which phases have run for which session is persisted, keyed by *both* phase and
session. Keying by phase alone cannot represent "yesterday's submit is settled
and today's is not", and a daemon that cannot represent that will reconsider
both on every tick forever. A crash-and-restart at 09:33 does not re-submit the
morning's orders -- and even if the state file were lost, the deterministic
client order IDs in :mod:`executor.submit` make a duplicate submission a
broker-side rejection rather than a doubled position.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass
from types import FrameType
from typing import Any
from zoneinfo import ZoneInfo

from executor.alerts import notify
from executor.batch import BatchPolicy, advance_batch
from executor.broker import CalendarSession, Clock, create_client
from executor.config import ExecutorSettings
from executor.errors import BatchPendingError, ConfigurationError, ExecutorError
from executor.journal import Journal
from executor.mailgun import send_mailgun
from executor.operations import observe_operations
from executor.order_registry import refresh_orders
from executor.plan_source import build_plan, parse_plan
from executor.portfolio_monitor import refresh_portfolio
from executor.report import (
    DailyReport,
    build_daily_report,
    prune_old_reports,
    send_report,
    write_report,
)
from executor.report.trade_monitor import capture_report
from executor.safety import (
    atomic_json,
    bind_account,
    require_reconciled_portfolio,
    submission_interlock,
)
from executor.submit import client_order_id_for, submit_plan
from executor.telegram import send_telegram

__all__ = ["Daemon", "run_daemon"]

LOGGER = logging.getLogger("executor.daemon")

#: Longest the loop will sleep in one slice. Bounds shutdown latency and the
#: damage a mid-sleep clock adjustment can do.
_MAX_SLEEP_SECONDS = 240.0

#: Past calendar horizon requested from the broker. A one-day lookback made a
#: daemon that restarted after a long weekend or host outage permanently blind
#: to the intervening sessions: it neither ran them (correct) nor recorded them
#: as missed (incorrect). Fourteen calendar days covers the seven-trading-day
#: artifact/audit horizon with room for weekends and exchange holidays.
_CALENDAR_LOOKBACK_DAYS = 14

#: How long after its scheduled instant the report phase may still run. The
#: report only observes; running it late costs nothing but timeliness.
_REPORT_GRACE = dt.timedelta(hours=6)

#: How many completed (phase, session) pairs the state file keeps. The calendar
#: window the loop can see is twenty-four days wide, so anything older can never be
#: reconsidered; this is generous enough that it never truncates something
#: still in view, and bounded so the file cannot grow without limit.
_LEDGER_LIMIT = 64

#: How late a submit has to be before it is worth saying so. A few minutes of
#: drift is the loop's own sleep granularity; a quarter hour means the process
#: was down, throttled, or the host was suspended, and the report should say so.
_LATE_SUBMIT_MINUTES = 15

#: A phase that raised is retried after this many seconds, while its window is
#: still open. Long enough for a broker blip or a stuck subprocess to clear,
#: short enough that a 09:25 failure still trades well before the cutoff.
#: Submit retries are safe because ``client_order_id_for`` is deterministic:
#: an order that already reached the broker is rejected as a duplicate rather
#: than doubled, so retrying a submit that actually succeeded is harmless.
_PHASE_RETRY_SECONDS = 300.0

#: Attempts per phase per session, including the first. After this the phase
#: is settled as failed so the loop does not hammer a broken dependency.
_MAX_PHASE_ATTEMPTS = 3


def _watch_progress(progress: list[float], stop: threading.Event) -> None:
    """Exit a hung process so Docker can restart it; receipts survive the exit."""
    while not stop.wait(30):
        if time.monotonic() - progress[0] > 1800:  # noqa: PLR2004
            LOGGER.critical("daemon made no successful progress for 30 minutes; exiting")
            os._exit(1)


@dataclass(frozen=True, slots=True)
class _Phase:
    """One scheduled action for one session, with the window it may run in.

    ``until`` is an instant rather than a duration because the submit window's
    end is a property of the session (a fixed cutoff before its close), not a
    fixed offset from its start.
    """

    name: str
    session: dt.date
    at: dt.datetime
    until: dt.datetime

    @property
    def key(self) -> str:
        """Ledger key. Both halves, because either alone is ambiguous."""
        return f"{self.name}:{self.session.isoformat()}"

    def is_due(self, now: dt.datetime) -> bool:
        return self.at <= now <= self.until

    def is_missed(self, now: dt.datetime) -> bool:
        return now > self.until

    def minutes_late(self, now: dt.datetime) -> int:
        return max(0, int((now - self.at).total_seconds() // 60))


class Daemon:
    """The trading loop. One instance per process."""

    def __init__(self, settings: ExecutorSettings) -> None:
        self._settings = settings
        self._client = create_client(settings)
        settings.ensure_directories()
        self._journal = Journal(settings.journal_path, settings.equity_curve_path)
        self._verify_authorized_config()
        self._state_path = settings.state_root / "daemon_state.json"
        self._stop = threading.Event()
        self._calendar_cache: tuple[dt.date, tuple[CalendarSession, ...]] | None = None
        #: Read from disk once, then written through. Re-reading it per phase
        #: per tick was pure syscall overhead.
        self._ledger: set[str] | None = None
        #: Attempts made so far for each not-yet-settled phase, keyed by
        #: ``_Phase.key``. Persisted before dispatch so restarting cannot reset
        #: the cap. Existing version-2 ledgers migrate on their next write.
        self._attempts: dict[str, int] = {}
        #: Earliest instant a failed, not-yet-settled phase may be attempted
        #: again, keyed by ``_Phase.key``. A phase with an entry here that is
        #: still in the future is skipped for the tick -- not run, not
        #: settled -- so a slow retry cannot block other due phases from
        #: running in the same tick, and it feeds the sleep calculation so
        #: the loop wakes up in time for it. Persisted alongside ``_attempts``.
        self._retry_not_before: dict[str, dt.datetime] = {}

    # -- lifecycle ----------------------------------------------------------

    def request_stop(self, signum: int, _frame: FrameType | None) -> None:
        """Signal handler: finish the current step, then exit cleanly."""
        LOGGER.info("received signal %s; shutting down after the current step", signum)
        self._stop.set()

    def install_signal_handlers(self) -> None:
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, self.request_stop)

    def _verify_authorized_config(self) -> None:
        """Refuse to start if the pinned strategy file is not on the allowlist.

        The plan will carry the same digest and be refused again at submit time;
        checking here turns a misconfiguration into a loud startup failure
        instead of a quiet day of refused submissions.
        """
        digest = hashlib.sha256(self._settings.strategy_config.read_bytes()).hexdigest()
        authorized = self._settings.limits.authorized_config_sha256
        self._journal.record(
            "authorization_check",
            strategy_config=str(self._settings.strategy_config),
            config_sha256=digest,
            authorized=digest in authorized,
            authorized_count=len(authorized),
            disabled=self._settings.limits.disabled,
        )
        if digest not in authorized:
            message = (
                f"STRATEGY_CONFIG {self._settings.strategy_config} has sha256 {digest}, "
                f"which is not in AUTHORIZED_CONFIG_SHA256"
            )
            raise ConfigurationError(message)

    # -- persisted state ----------------------------------------------------

    def _load_ledger(self) -> set[str]:  # noqa: PLR0912 - validated legacy migration
        """The set of ``phase:session`` pairs already settled.

        Reads the version-1 layout -- a single session per phase name -- as
        well, so an upgrade in place does not look like a blank slate. That
        layout could hold only one session per phase, so migrating it recovers
        the most recent of each and leaves anything older to be re-examined
        once and marked missed; a past session can never be *due*, so no order
        can result from it.
        """
        if self._ledger is not None:
            return self._ledger

        ledger: set[str] = set()
        if self._state_path.is_file():
            try:
                payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigurationError(
                    "daemon state unreadable; restore or reconcile it"
                ) from exc
            if not isinstance(payload, dict):
                raise ConfigurationError("invalid daemon state object")
            if payload.get("version") not in (None, 2, 3):
                raise ConfigurationError("unsupported daemon state version")
            if "version" in payload and (
                not isinstance(payload.get("completed"), list)
                or any(not isinstance(item, str) for item in payload["completed"])
            ):
                raise ConfigurationError("invalid completed-phase ledger")
            if payload.get("version") == 3:  # noqa: PLR2004
                try:
                    attempts = payload["attempts"]
                    if not isinstance(attempts, dict) or any(
                        type(value) is not int or value < 0 for value in attempts.values()
                    ):
                        raise ValueError("invalid retry counts")
                    self._attempts = attempts
                    self._retry_not_before = {
                        key: dt.datetime.fromisoformat(value)
                        for key, value in payload["retry_not_before"].items()
                    }
                    if any(value.tzinfo is None for value in self._retry_not_before.values()):
                        raise ValueError("naive retry timestamp")
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    raise ConfigurationError("invalid persisted retry state") from exc
            if isinstance(payload, dict):
                completed = payload.get("completed")
                if isinstance(completed, list):
                    ledger = {str(item) for item in completed}
                else:  # version 1: {"submit": "2026-08-20", "report": "2026-08-19"}
                    ledger = {
                        f"{name}:{value}"
                        for name, value in payload.items()
                        if isinstance(value, str) and name in {"submit", "report"}
                    }
                    if ledger:
                        LOGGER.info("migrated %d entries from the v1 state file", len(ledger))

        self._ledger = ledger
        return ledger

    def _save_ledger(self, ledger: set[str]) -> None:
        # Newest last, then truncated from the front: the entries that matter
        # are the ones inside the visible calendar window.
        retained = sorted(ledger, key=lambda item: item.split(":", 1)[::-1])[-_LEDGER_LIMIT:]
        payload = {
            "version": 3,
            "completed": retained,
            "attempts": {
                key: value for key, value in self._attempts.items() if key not in retained
            },
            "retry_not_before": {
                key: value.isoformat()
                for key, value in self._retry_not_before.items()
                if key not in retained
            },
        }
        atomic_json(self._state_path, payload)
        self._ledger = set(retained)

    def _mark_done(self, phase: str, session: dt.date) -> None:
        ledger = set(self._load_ledger())
        ledger.add(f"{phase}:{session.isoformat()}")
        self._save_ledger(ledger)

    def _already_done(self, phase: str, session: dt.date) -> bool:
        return f"{phase}:{session.isoformat()}" in self._load_ledger()

    # -- calendar -----------------------------------------------------------

    def _sessions(self, today: dt.date) -> tuple[CalendarSession, ...]:
        """Trading sessions around ``today``, cached for the day.

        The window reaches back fourteen calendar days so a restart after a
        host outage records every skipped phase inside the seven-trading-day
        audit horizon. It reaches forward ten so a Friday evening knows about
        Monday and a long holiday weekend still has a next event to sleep toward.
        """
        if self._calendar_cache is not None and self._calendar_cache[0] == today:
            return self._calendar_cache[1]
        sessions = self._client.get_calendar(
            today - dt.timedelta(days=_CALENDAR_LOOKBACK_DAYS),
            today + dt.timedelta(days=10),
        )
        self._calendar_cache = (today, sessions)
        return sessions

    def _phases(self, now_exchange: dt.datetime, zone: ZoneInfo) -> list[_Phase]:
        """Every phase in the visible calendar window, in chronological order."""
        settings = self._settings
        phases: list[_Phase] = []
        for session in self._sessions(now_exchange.date()):
            open_at = dt.datetime.combine(session.date, session.open_local, tzinfo=zone)
            close_at = dt.datetime.combine(session.date, session.close_local, tzinfo=zone)
            submit_at = open_at - dt.timedelta(minutes=settings.plan_lead_minutes)
            # Derived from the close, so a half day narrows the window without
            # anything here knowing that today is a half day. `max` keeps the
            # window from inverting on a pathologically short session; it can
            # only ever shrink to a point, never extend past the cutoff.
            submit_until = max(
                submit_at,
                close_at - dt.timedelta(minutes=settings.submit_cutoff_minutes),
            )
            report_at = close_at + dt.timedelta(minutes=settings.report_delay_minutes)
            phases.append(
                _Phase(
                    name="submit",
                    session=session.date,
                    at=submit_at,
                    until=submit_until,
                )
            )
            phases.append(
                _Phase(
                    name="report",
                    session=session.date,
                    at=report_at,
                    until=report_at + _REPORT_GRACE,
                )
            )
        phases.sort(key=lambda phase: phase.at)
        return phases

    def minutes_past_schedule(self, session: dt.date) -> int:
        """How late a submit for ``session`` would be if it started now.

        Answered from the calendar, so it is the real distance from that
        session's scheduled instant rather than a guess. A session the calendar
        does not contain returns zero: there is no schedule to be late against.
        """
        zone = self._zone(None)
        now = self._client.get_clock().timestamp.astimezone(zone)
        for phase in self._phases(now, zone):
            if phase.name == "submit" and phase.session == session:
                return phase.minutes_late(now)
        return 0

    # -- the two phases -----------------------------------------------------

    def run_submit(self, session: dt.date, *, minutes_late: int = 0) -> None:
        """Serialize submissions and reuse the original session plan on retries."""
        with (self._settings.state_root / "submission.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigurationError("another submission process holds the state lock") from exc
            refusal = submission_interlock(self._settings.state_root)
            if refusal and not (
                self._settings.execution_mode == "staged-v1"
                and (self._settings.state_root / "batches" / f"{session}.json").is_file()
                and refusal.startswith(("unresolved submission ", "STOP_SUBMISSIONS"))
            ):
                self._journal.record("submission_refused", session=session, reason=refusal)
                return
            self._run_submit(session, minutes_late=minutes_late)

    def _submission_now(self, session: dt.date) -> dt.datetime:
        """Enforce the exchange window even for one-shot CLI calls and slow planning."""
        if self._stop.is_set():
            raise ConfigurationError("shutdown requested; no further orders permitted")
        clock = self._client.get_clock()
        wall_now = dt.datetime.now(dt.UTC)
        if abs((wall_now - clock.timestamp).total_seconds()) > 120:  # noqa: PLR2004
            raise ConfigurationError("broker/host clock skew exceeds 120 seconds; check NTP")
        zone = self._zone(clock)
        now = clock.timestamp.astimezone(zone)
        phases = self._phases(now, zone)
        if not any(p.name == "submit" and p.session == session and p.is_due(now) for p in phases):
            raise ConfigurationError("submission is outside the exchange session/cutoff window")
        require_reconciled_portfolio(self._settings.state_root, wall_now)
        return wall_now

    def _run_submit(self, session: dt.date, *, minutes_late: int = 0) -> None:
        """Build today's plan from live account state and submit it.

        ``minutes_late`` is how far past the scheduled instant this run began.
        It is journalled and surfaced in the report rather than used to decide
        anything: the plan is built from live state at this moment and carries
        its own expiry, so lateness is an execution-quality caveat, not a
        correctness one.
        """
        settings = self._settings
        LOGGER.info("submit phase for %s", session)
        self._submission_now(session)

        if minutes_late >= _LATE_SUBMIT_MINUTES:
            LOGGER.warning(
                "submit for %s is starting %d minutes after its scheduled time; "
                "fills will differ from the backtest's assumed open",
                session,
                minutes_late,
            )
            self._journal.record(
                "submit_delayed",
                session=session,
                phase="submit",
                minutes_late=minutes_late,
            )

        account = self._client.get_account()
        bind_account(settings.state_root, account.account_number)
        positions = self._client.position_quantities()
        self._journal.record(
            "account_observed",
            session=session,
            phase="submit",
            equity=account.equity,
            cash=account.cash,
            positions=positions,
        )

        plan_path = settings.plans_dir / f"{session.isoformat()}.json"
        if plan_path.exists():
            # A retry may observe partial fills. Replanning then would create
            # different quantities and idempotency keys for the same session.
            # Corrupt artifacts fail closed instead of triggering a new plan.
            plan = parse_plan(json.loads(plan_path.read_text(encoding="utf-8")))
            self._journal.record("plan_reused", session=session)
        else:
            plan = build_plan(
                amat_root=settings.amat_root,
                strategy_config=settings.strategy_config,
                snapshot=settings.snapshot,
                equity=account.equity,
                positions=positions,
                offline=False,
                news_assets=settings.news_assets,
                plan_command=settings.plan_command,
                out_path=plan_path,
            )
        self._journal.record(
            "plan_built",
            session=session,
            as_of_session=plan.as_of_session,
            strategy=f"{plan.strategy_name} v{plan.strategy_version}",
            config_sha256=plan.config_sha256,
            snapshot_id=plan.snapshot_id,
            appended_sessions=plan.appended_sessions,
            intent_count=len(plan.intents),
            gross_target_exposure=plan.gross_target_exposure,
        )

        calendar = self._client.get_calendar(
            session - dt.timedelta(days=_CALENDAR_LOOKBACK_DAYS), session
        )
        previous = [item.date for item in calendar if item.date < session]
        if (
            not any(item.date == session for item in calendar)
            or not previous
            or plan.as_of_session != max(previous)
        ):
            self._journal.record(
                "submission_refused",
                session=session,
                reason=(
                    "plan does not use the immediately preceding exchange session, "
                    "or session is closed"
                ),
            )
            return

        expected_ids = {client_order_id_for(plan, intent) for intent in plan.intents}
        unrelated = [
            order
            for order in self._client.list_orders(status="open")
            if order.client_order_id not in expected_ids
        ]
        if unrelated and settings.execution_mode == "legacy":
            self._journal.record(
                "submission_refused",
                session=session,
                reason=f"{len(unrelated)} open broker order(s) outside this session plan",
            )
            return

        if settings.execution_mode == "staged-v1":
            batch = advance_batch(
                plan,
                self._client,
                session=session,
                now=self._submission_now(session),
                journal=self._journal,
                limits=settings.limits,
                policy=BatchPolicy(whole_share_buys=settings.whole_share_buys),
                dry_run=settings.dry_run,
                submission_time=lambda: self._submission_now(session),
            )
            result = batch.submission
            if batch.pending:
                raise BatchPendingError(f"batch {batch.state['batch_id']}: {batch.status}")
        else:
            result = submit_plan(
                plan,
                self._client,
                account=self._client.get_account(),
                session=session,
                now=self._submission_now(session),
                journal=self._journal,
                dry_run=settings.dry_run,
                limits=settings.limits,
                current_positions=self._client.position_quantities(),
                submission_time=lambda: self._submission_now(session),
            )
        if (
            result.refusal_reason
            and not result.refusal_reason.startswith("no trades:")
            and not settings.limits.disabled
        ):
            notify(settings, "submission-refused", result.refusal_reason)
        # Counted by actual status rather than by `not submitted`, so a dry run
        # does not report ten withheld intents when nothing was withheld.
        tally: dict[str, int] = {}
        for outcome in result.outcomes:
            tally[outcome.status] = tally.get(outcome.status, 0) + 1
        LOGGER.info(
            "submit phase complete for %s: executed=%s %s",
            session,
            result.executed,
            ", ".join(f"{status}={count}" for status, count in sorted(tally.items()))
            or "no intents",
        )

    def run_report(self, session: dt.date) -> None:
        """Serialize automatic and manual report/equity writers."""
        with (self._settings.state_root / "report.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigurationError("another report process holds the state lock") from exc
            self._run_report(session)

    def _run_report(self, session: dt.date) -> None:  # noqa: PLR0912, PLR0915 - ordered report transaction
        """Record the day's equity, write the report, mail it, prune old files."""
        settings = self._settings
        LOGGER.info("report phase for %s", session)

        account = self._client.get_account()
        positions = self._client.list_positions()
        gross_exposure = (
            float(sum(position.market_value for position in positions) / account.equity)
            if account.equity > 0
            else 0.0
        )

        # One observation per session. A restart after the report already ran
        # must not append a second point and put a spurious 0% return in every
        # downstream series.
        if not self._journal.has_equity_for(session):
            self._journal.record_equity(
                session=session,
                equity=account.equity,
                cash=account.cash,
                gross_exposure=gross_exposure,
                positions={position.symbol: position.quantity for position in positions},
            )

        warnings: list[str] = []

        plan = None
        plan_path = settings.plans_dir / f"{session.isoformat()}.json"
        if plan_path.is_file():
            from executor.plan_source import parse_plan  # noqa: PLC0415

            try:
                plan = parse_plan(json.loads(plan_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, ExecutorError) as exc:
                warnings.append(f"Today's archived plan could not be re-read: {exc}")

        # Sourced from the journal rather than re-derived, so the report
        # describes what the morning actually did even if the daemon was
        # restarted between the two phases.
        submission_payload = self._last_submission_for(session)
        late = int(submission_payload.get("minutes_late", 0) or 0)
        if late >= _LATE_SUBMIT_MINUTES:
            warnings.append(
                f"The rebalance was submitted {late} minutes after its scheduled time, "
                f"so fills reflect intraday prices rather than the opening prices the "
                f"backtest assumes. Treat today's tracking difference accordingly."
            )
        refusal = str(submission_payload.get("refusal_reason", ""))
        if refusal:
            warnings.append(f"No orders placed: {refusal}")
        elif not submission_payload:
            warnings.append(
                "No submission was recorded for this session. Either the daemon was not "
                "running at the open, or the submit phase failed; check the journal."
            )
        rejected = [
            item
            for item in submission_payload.get("outcomes", [])
            if item.get("status") == "rejected"
        ]
        if rejected:
            detail = "; ".join(
                f"{item['symbol']} ({str(item.get('detail', ''))[:120]})" for item in rejected
            )
            warnings.append(f"{len(rejected)} order(s) rejected by the broker: {detail}")
        if any(
            item.get("status") == "outcome_unknown"
            for item in submission_payload.get("outcomes", [])
        ):
            warnings.append(
                "Order outcome unknown: submissions halted until broker reconciliation."
            )

        # Orders submitted today, so the report can show what actually filled
        # rather than only what was requested.
        zone = ZoneInfo(plan.exchange_timezone if plan else "America/New_York")
        since = dt.datetime.combine(session, dt.time(0, 0), tzinfo=zone)
        orders_available = True
        try:
            orders = self._client.list_orders(after=since)
        except ExecutorError as exc:
            warnings.append(f"Could not list today's orders: {exc}")
            orders = ()
            orders_available = False

        equity_curve = self._journal.read_equity_curve()
        if self._journal.unreadable_journal_lines or self._journal.unreadable_equity_lines:
            warnings.append(
                f"History contains {self._journal.unreadable_journal_lines} unreadable journal "
                f"line(s) and {self._journal.unreadable_equity_lines} unreadable equity line(s). "
                "Original files are preserved; reconstructed history is incomplete."
            )
        report = dataclasses.replace(
            build_daily_report(
                session=session,
                now=dt.datetime.now(dt.UTC),
                account=account,
                positions=positions,
                equity_curve=equity_curve,
                submission=None,
                plan=plan,
                orders=orders,
                orders_available=orders_available,
                market_is_open=self._client.get_clock().is_open,
                extra_warnings=tuple(warnings),
            ),
            submission=submission_payload,
            trading_mode=self._settings.trading_mode,
        )

        try:
            monitor = capture_report(
                settings.state_root, report, plan, orders_available=orders_available
            )
            report = dataclasses.replace(report, trade_monitor=monitor["summary"])
        except (OSError, ValueError, TypeError, KeyError, ExecutorError) as exc:
            LOGGER.exception("trade monitoring could not be updated")
            report = dataclasses.replace(
                report, warnings=(*report.warnings, f"Trade monitor update failed: {exc}")
            )
        markdown_path, json_path = write_report(report, settings.reports_dir)
        LOGGER.info("wrote %s", markdown_path)

        # Reports and plans are retention-window artifacts; the journal is the
        # permanent operational record. Persist the compact reconciliation so
        # fill quality remains analyzable after the rendered report is pruned.
        self._record_execution_reconciliation(session, report)

        delivered, detail = (False, "SMTP is not configured")
        if settings.mailgun is not None:
            delivered, detail = send_mailgun(settings.mailgun, report.subject, report.to_markdown())
        elif settings.smtp is not None:
            delivered, detail = send_report(report, settings.smtp)
        self._journal.record(
            "report_written",
            session=session,
            markdown=str(markdown_path),
            json=str(json_path),
            mail_delivered=delivered,
            mail_detail=detail,
        )
        if not delivered and (settings.smtp is not None or settings.mailgun is not None):
            LOGGER.warning("report mail not delivered: %s", detail)

        if settings.telegram is not None:
            accepted, telegram_detail = send_telegram(
                settings.telegram, report.subject, report.to_markdown()
            )
            self._journal.record(
                "report_telegram", session=session, accepted=accepted, detail=telegram_detail
            )

        self._prune_artifacts(session)

    def _record_execution_reconciliation(self, session: dt.date, report: DailyReport) -> None:
        """Keep fill evidence after retention prunes the rendered report."""
        self._journal.record(
            "execution_reconciled",
            session=session,
            **report.execution.as_dict(),
        )

    def _prune_artifacts(self, session: dt.date) -> None:
        settings = self._settings
        removed = prune_old_reports(
            (settings.reports_dir, settings.plans_dir),
            retain_days=settings.report_retention_days,
        )
        if not removed:
            return
        self._journal.record(
            "artifacts_pruned",
            session=session,
            retained_days=settings.report_retention_days,
            removed=[str(path) for path in removed],
        )
        LOGGER.info(
            "pruned %d artifact(s) beyond the %d-day window",
            len(removed),
            settings.report_retention_days,
        )

    def _last_submission_for(self, session: dt.date) -> dict[str, Any]:
        """Reconstruct the day's submission summary from the journal."""
        target = session.isoformat()
        outcomes: list[dict[str, Any]] = []
        refusal = ""
        executed = False
        minutes_late = 0

        for entry in self._journal.read_journal_entries():
            if entry.get("session") != target:
                continue
            event = entry.get("event")
            if event == "submit_delayed":
                try:
                    minutes_late = int(entry.get("minutes_late", 0))
                except (TypeError, ValueError):
                    minutes_late = 0
            elif event == "submission_refused":
                refusal = str(entry.get("reason", ""))
            elif event in {"order_submitted", "intent_dry_run"}:
                executed = True
                outcomes.append(
                    {
                        "symbol": entry.get("symbol", ""),
                        "side": entry.get("side", ""),
                        "intended_quantity": str(entry.get("quantity", "0")),
                        "submitted_quantity": str(entry.get("quantity", "0")),
                        "status": ("submitted" if event == "order_submitted" else "dry_run"),
                        "client_order_id": entry.get("client_order_id", ""),
                        "detail": str(entry.get("note", "") or ""),
                    }
                )
            elif event in {
                "intent_withheld",
                "order_rejected",
                "order_duplicate",
                "order_outcome_unknown",
            }:
                executed = True
                status = {
                    "intent_withheld": "withheld",
                    "order_rejected": "rejected",
                    "order_duplicate": "already_submitted",
                    "order_outcome_unknown": "outcome_unknown",
                }[str(event)]
                outcomes.append(
                    {
                        "symbol": entry.get("symbol", ""),
                        "side": entry.get("side", ""),
                        "intended_quantity": str(
                            entry.get("intended_quantity", entry.get("quantity", "0"))
                        ),
                        "submitted_quantity": "0",
                        "status": status,
                        "client_order_id": entry.get("client_order_id", ""),
                        "detail": str(entry.get("reason", entry.get("detail", "")) or ""),
                    }
                )

        if not outcomes and not refusal:
            return {}
        return {
            "session": target,
            "executed": executed and not refusal,
            "refusal_reason": refusal,
            "minutes_late": minutes_late,
            "outcomes": outcomes,
        }

    # -- the loop -----------------------------------------------------------

    def _observe_account(self, clock: Clock, now: dt.datetime) -> None:
        try:
            refresh_orders(self._settings.state_root, self._client, clock, self._journal)
        except (ExecutorError, OSError, ValueError) as exc:
            LOGGER.warning("order registry refresh failed: %s", type(exc).__name__)
            self._journal.record("order_registry_refresh_failed", error_type=type(exc).__name__)

        try:
            refresh_portfolio(self._settings.state_root, self._client, clock, self._journal)
        except OSError as exc:
            LOGGER.warning("portfolio monitor persistence failed: %s", type(exc).__name__)

        try:
            observe_operations(self._settings, now)
        except OSError as exc:
            LOGGER.warning("operations projection failed: %s", type(exc).__name__)

        try:
            require_reconciled_portfolio(self._settings.state_root, dt.datetime.now(dt.UTC))
            issue = submission_interlock(self._settings.state_root)
            if issue:
                notify(self._settings, "submission-interlock", issue)
        except (ConfigurationError, OSError):
            notify(
                self._settings,
                "portfolio-monitor",
                "Portfolio observation is stale or unreconciled.",
            )

    def tick(self) -> float:  # noqa: PLR0912, PLR0915 - ordered nonblocking phase scheduling
        """Run whatever is due, and return how long to sleep next.

        A failure inside one phase is journalled and swallowed: the daemon must
        survive a bad morning and still send the evening's report, and a report
        that says "the plan failed, here is the traceback" is worth far more
        than a dead process that says nothing. While the phase's window is
        still open and its attempt count is below the cap, the failure is
        retried after a short delay rather than settled immediately -- a
        transient broker or subprocess error should not permanently skip the
        day's rebalance. The retry delay only defers that one phase: it is
        recorded as a "not before" instant rather than a blocking sleep, so
        every other phase due in the same tick still runs.
        """
        clock = self._client.get_clock()
        zone = self._zone(clock)
        now = clock.timestamp.astimezone(zone)

        self._observe_account(clock, now)

        phases = self._phases(now, zone)
        for phase in phases:
            if self._already_done(phase.name, phase.session):
                continue
            if phase.is_missed(now):
                # Mark it done so the loop does not reconsider it every tick,
                # and record that it was skipped rather than performed.
                self._mark_done(phase.name, phase.session)
                self._attempts.pop(phase.key, None)
                self._retry_not_before.pop(phase.key, None)
                self._journal.record(
                    "phase_missed",
                    session=phase.session,
                    phase=phase.name,
                    scheduled_at=phase.at.isoformat(),
                    window_closed_at=phase.until.isoformat(),
                    observed_at=now.isoformat(),
                    reason=(
                        "the daemon was not running, or was busy, for the whole "
                        "window in which this phase could run"
                    ),
                )
                LOGGER.warning(
                    "missed %s phase for %s (window %s to %s)",
                    phase.name,
                    phase.session,
                    phase.at,
                    phase.until,
                )
                notify(self._settings, "phase-missed", f"Missed {phase.name} for {phase.session}.")
                continue
            if phase.is_due(now):
                not_before = self._retry_not_before.get(phase.key)
                if not_before is not None and now < not_before:
                    # A retry is pending but not due yet; leave it alone this
                    # tick so it does not starve other phases that are due,
                    # and let the sleep calculation below wake up for it.
                    continue
                attempt = self._attempts.get(phase.key, 0) + 1
                if attempt > _MAX_PHASE_ATTEMPTS:
                    self._journal.record("phase_exhausted", session=phase.session, phase=phase.name)
                    self._mark_done(phase.name, phase.session)
                    continue
                self._attempts[phase.key] = attempt
                self._save_ledger(self._load_ledger())
                try:
                    if phase.name == "submit":
                        self.run_submit(phase.session, minutes_late=phase.minutes_late(now))
                    else:
                        self.run_report(phase.session)
                except BatchPendingError as exc:
                    # Fills may take many ticks. Waiting consumes no failure budget.
                    self._attempts[phase.key] = max(0, attempt - 1)
                    retry_at = now + dt.timedelta(seconds=5)
                    if retry_at <= phase.until:
                        self._retry_not_before[phase.key] = retry_at
                        self._save_ledger(self._load_ledger())
                        self._journal.record(
                            "batch_waiting", session=phase.session, detail=str(exc)
                        )
                        continue
                    self._journal.record("batch_window_closed", session=phase.session)
                except Exception as exc:
                    LOGGER.exception("%s phase failed for %s", phase.name, phase.session)
                    notify(
                        self._settings,
                        "phase-failed",
                        f"{phase.name} phase failed for {phase.session}: {type(exc).__name__}",
                    )
                    self._journal.record(
                        "phase_failed",
                        session=phase.session,
                        phase=phase.name,
                        attempt=attempt,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    retry_at = now + dt.timedelta(seconds=_PHASE_RETRY_SECONDS)
                    if attempt < _MAX_PHASE_ATTEMPTS and retry_at <= phase.until:
                        self._retry_not_before[phase.key] = retry_at
                        self._save_ledger(self._load_ledger())
                        self._journal.record(
                            "phase_retry_scheduled",
                            session=phase.session,
                            phase=phase.name,
                            attempt=attempt,
                            retry_at=retry_at.isoformat(),
                            window_closes_at=phase.until.isoformat(),
                        )
                        continue  # other due phases still get a turn this tick
                self._mark_done(phase.name, phase.session)
                self._attempts.pop(phase.key, None)
                self._retry_not_before.pop(phase.key, None)
                continue  # this phase is settled; keep checking other due phases

        upcoming = [phase.at for phase in phases if phase.at > now]
        upcoming.extend(self._retry_not_before.values())
        if not upcoming:
            return _MAX_SLEEP_SECONDS
        seconds = (min(upcoming) - now).total_seconds()
        return max(1.0, min(seconds, _MAX_SLEEP_SECONDS))

    def _zone(self, clock: object) -> ZoneInfo:
        """The exchange zone. Constant for this venue, but named once."""
        del clock
        return ZoneInfo("America/New_York")

    def run_forever(self) -> int:
        """Loop until signalled. Returns a process exit code."""
        settings = self._settings
        LOGGER.info(
            "starting: strategy=%s snapshot=%s state=%s retention=%dd dry_run=%s",
            settings.strategy_config.name,
            settings.snapshot.name,
            settings.state_root,
            settings.report_retention_days,
            settings.dry_run,
        )
        self._journal.record(
            "daemon_started",
            strategy_config=str(settings.strategy_config),
            snapshot=str(settings.snapshot),
            dry_run=settings.dry_run,
            retention_days=settings.report_retention_days,
        )

        progress = [time.monotonic()]
        watchdog_stop = threading.Event()

        threading.Thread(
            target=_watch_progress,
            args=(progress, watchdog_stop),
            name="executor-watchdog",
            daemon=True,
        ).start()
        heartbeat = settings.state_root / "heartbeat.json"
        last_success: str | None = None
        try:
            while not self._stop.is_set():
                try:
                    sleep_for = self.tick()
                    last_success = dt.datetime.now(dt.UTC).isoformat()
                    atomic_json(heartbeat, {"status": "running", "last_success_at": last_success})
                    progress[0] = time.monotonic()
                except Exception as exc:
                    atomic_json(heartbeat, {"status": "degraded", "last_success_at": last_success})
                    LOGGER.exception("tick failed; retrying")
                    notify(settings, "daemon-degraded", f"Daemon tick failed: {type(exc).__name__}")
                    self._journal.record("tick_failed", error=f"{type(exc).__name__}: {exc}")
                    sleep_for = 60.0
                self._stop.wait(sleep_for)
        finally:
            watchdog_stop.set()
            atomic_json(heartbeat, {"status": "stopped", "last_success_at": last_success})

        self._journal.record("daemon_stopped")
        LOGGER.info("stopped")
        return 0


def run_daemon(settings: ExecutorSettings) -> int:
    """Construct and run the daemon with signal handling installed."""
    settings.ensure_directories()
    with (settings.state_root / "daemon.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConfigurationError("another daemon holds this state directory") from exc
        daemon = Daemon(settings)
        daemon.install_signal_handlers()
        return daemon.run_forever()
