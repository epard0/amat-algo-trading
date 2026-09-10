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
import json
import logging
import signal
import threading
from dataclasses import dataclass
from types import FrameType
from typing import Any
from zoneinfo import ZoneInfo

from executor.broker import AlpacaPaperClient, CalendarSession
from executor.config import ExecutorSettings
from executor.errors import ExecutorError
from executor.journal import Journal
from executor.plan_source import build_plan
from executor.report import (
    DailyReport,
    build_daily_report,
    prune_old_reports,
    send_report,
    write_report,
)
from executor.submit import submit_plan

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
        self._client = AlpacaPaperClient(settings.trading_key_id, settings.trading_secret_key)
        settings.ensure_directories()
        self._journal = Journal(settings.journal_path, settings.equity_curve_path)
        self._state_path = settings.state_root / "daemon_state.json"
        self._stop = threading.Event()
        self._calendar_cache: tuple[dt.date, tuple[CalendarSession, ...]] | None = None
        #: Read from disk once, then written through. Re-reading it per phase
        #: per tick was pure syscall overhead.
        self._ledger: set[str] | None = None

    # -- lifecycle ----------------------------------------------------------

    def request_stop(self, signum: int, _frame: FrameType | None) -> None:
        """Signal handler: finish the current step, then exit cleanly."""
        LOGGER.info("received signal %s; shutting down after the current step", signum)
        self._stop.set()

    def install_signal_handlers(self) -> None:
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, self.request_stop)

    # -- persisted state ----------------------------------------------------

    def _load_ledger(self) -> set[str]:
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
            except (OSError, json.JSONDecodeError):
                # A corrupt state file must not wedge the daemon. Losing it
                # means at worst re-examining a phase, which the idempotency
                # key and the duplicate-equity guard both absorb.
                LOGGER.warning("daemon state file unreadable; treating as empty")
                payload = {}
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
        payload = {"version": 2, "completed": retained}
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self._state_path)
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
        """Build today's plan from live account state and submit it.

        ``minutes_late`` is how far past the scheduled instant this run began.
        It is journalled and surfaced in the report rather than used to decide
        anything: the plan is built from live state at this moment and carries
        its own expiry, so lateness is an execution-quality caveat, not a
        correctness one.
        """
        settings = self._settings
        LOGGER.info("submit phase for %s", session)

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
        positions = self._client.position_quantities()
        self._journal.record(
            "account_observed",
            session=session,
            phase="submit",
            equity=account.equity,
            cash=account.cash,
            positions=positions,
        )

        plan = build_plan(
            amat_root=settings.amat_root,
            strategy_config=settings.strategy_config,
            snapshot=settings.snapshot,
            equity=account.equity,
            positions=positions,
            offline=False,
            news_assets=settings.news_assets,
            plan_command=settings.plan_command,
            out_path=settings.plans_dir / f"{session.isoformat()}.json",
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

        result = submit_plan(
            plan,
            self._client,
            account=account,
            session=session,
            now=dt.datetime.now(dt.UTC),
            journal=self._journal,
            dry_run=settings.dry_run,
        )
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

        # Orders submitted today, so the report can show what actually filled
        # rather than only what was requested.
        zone = ZoneInfo(plan.exchange_timezone if plan else "America/New_York")
        since = dt.datetime.combine(session, dt.time(0, 0), tzinfo=zone)
        try:
            orders = tuple(
                order
                for order in self._client.list_orders(after=since)
                if order.client_order_id.startswith("amat-")
            )
        except ExecutorError as exc:
            warnings.append(f"Could not list today's orders: {exc}")
            orders = ()

        report = dataclasses.replace(
            build_daily_report(
                session=session,
                now=dt.datetime.now(dt.UTC),
                account=account,
                positions=positions,
                equity_curve=self._journal.read_equity_curve(),
                submission=None,
                plan=plan,
                orders=orders,
                extra_warnings=tuple(warnings),
            ),
            submission=submission_payload,
        )

        markdown_path, json_path = write_report(report, settings.reports_dir)
        LOGGER.info("wrote %s", markdown_path)

        # Reports and plans are retention-window artifacts; the journal is the
        # permanent operational record. Persist the compact reconciliation so
        # fill quality remains analyzable after the rendered report is pruned.
        self._record_execution_reconciliation(session, report)

        delivered, detail = (False, "SMTP is not configured")
        if settings.smtp is not None:
            delivered, detail = send_report(report, settings.smtp)
        self._journal.record(
            "report_written",
            session=session,
            markdown=str(markdown_path),
            json=str(json_path),
            mail_delivered=delivered,
            mail_detail=detail,
        )
        if not delivered and settings.smtp is not None:
            LOGGER.warning("report mail not delivered: %s", detail)

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
            elif event in {"intent_withheld", "order_rejected", "order_duplicate"}:
                executed = True
                status = {
                    "intent_withheld": "withheld",
                    "order_rejected": "rejected",
                    "order_duplicate": "already_submitted",
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

    def tick(self) -> float:
        """Run whatever is due, and return how long to sleep next.

        A failure inside one phase is journalled and swallowed: the daemon must
        survive a bad morning and still send the evening's report, and a report
        that says "the plan failed, here is the traceback" is worth far more
        than a dead process that says nothing.
        """
        clock = self._client.get_clock()
        zone = self._zone(clock)
        now = clock.timestamp.astimezone(zone)

        phases = self._phases(now, zone)
        for phase in phases:
            if self._already_done(phase.name, phase.session):
                continue
            if phase.is_missed(now):
                # Mark it done so the loop does not reconsider it every tick,
                # and record that it was skipped rather than performed.
                self._mark_done(phase.name, phase.session)
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
                continue
            if phase.is_due(now):
                try:
                    if phase.name == "submit":
                        self.run_submit(phase.session, minutes_late=phase.minutes_late(now))
                    else:
                        self.run_report(phase.session)
                except Exception as exc:
                    LOGGER.exception("%s phase failed for %s", phase.name, phase.session)
                    self._journal.record(
                        "phase_failed",
                        session=phase.session,
                        phase=phase.name,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                self._mark_done(phase.name, phase.session)
                return 1.0  # re-evaluate immediately; another phase may also be due

        upcoming = [phase.at for phase in phases if phase.at > now]
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

        while not self._stop.is_set():
            try:
                sleep_for = self.tick()
            except Exception as exc:
                LOGGER.exception("tick failed; retrying")
                self._journal.record("tick_failed", error=f"{type(exc).__name__}: {exc}")
                sleep_for = 60.0
            self._stop.wait(sleep_for)

        self._journal.record("daemon_stopped")
        LOGGER.info("stopped")
        return 0


def run_daemon(settings: ExecutorSettings) -> int:
    """Construct and run the daemon with signal handling installed."""
    daemon = Daemon(settings)
    daemon.install_signal_handlers()
    return daemon.run_forever()
