"""Append-only record of everything the executor did and refused to do.

The journal is the executor's counterpart to the strategy repository's
immutable snapshots. It is never rewritten in place: a fill that cannot be
traced to an intent, or an intent that cannot be traced to a plan, is exactly
the failure this file exists to make impossible.

Two files, with different lifetimes and different reasons:

``journal.ndjson``
    Every event, verbose. Grows with activity.
``equity_curve.ndjson``
    One line per trading day: equity, cash, exposure, realised movement. This
    is what the performance report is computed from, so it outlives the daily
    report files that the retention policy prunes.

Rotation, and why only the journal has it
-----------------------------------------
A daemon bug once wrote 41,000 duplicate events and took ``journal.ndjson`` to
17 MB in five days. The bug is fixed, but "one defect can fill the VPS disk"
is a property worth removing rather than a lesson worth relying on. The journal
therefore rolls over to ``journal.1.ndjson`` at a size cap, keeping exactly one
previous generation; readers span both. The equity curve is *never* rotated --
at roughly 200 bytes a day, a decade of it costs 500 KB, and losing its head
would silently truncate every return series computed downstream.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

__all__ = ["MAX_JOURNAL_BYTES", "Journal"]

#: Size at which ``journal.ndjson`` rolls over to ``journal.1.ndjson``. With
#: the scheduler behaving, a trading day writes on the order of a kilobyte, so
#: this cap is reached only by a defect -- which is the case it exists for.
#: Two generations bound the journal's disk footprint at twice this.
MAX_JOURNAL_BYTES = 32 * 1024 * 1024


def _jsonable(value: Any) -> Any:
    """Render Decimals as strings and dates as ISO text, recursively."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class Journal:
    """Append-only NDJSON writer.

    Every write is a single ``write()`` of one newline-terminated line followed
    by an ``fsync``. One line is far below the pipe/file atomicity threshold, so
    a process killed mid-run truncates at a record boundary rather than leaving
    a half-written event that no reader can classify.
    """

    def __init__(
        self,
        journal_path: Path,
        equity_curve_path: Path,
        *,
        max_journal_bytes: int = MAX_JOURNAL_BYTES,
    ) -> None:
        self._journal_path = journal_path
        self._equity_curve_path = equity_curve_path
        self._max_journal_bytes = max_journal_bytes
        for path in (journal_path, equity_curve_path):
            path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def previous_journal_path(self) -> Path:
        """Where the journal rolls over to. ``journal.ndjson`` -> ``journal.1.ndjson``."""
        path = self._journal_path
        return path.with_name(f"{path.stem}.1{path.suffix}")

    def _rotate_if_oversized(self) -> None:
        """Roll the journal over once it passes the cap, keeping one generation.

        A rename, not a truncation: the previous generation stays readable
        until the *next* rollover overwrites it, so nothing is destroyed by the
        write that happens to cross the threshold.
        """
        try:
            size = self._journal_path.stat().st_size
        except OSError:
            return
        if size < self._max_journal_bytes:
            return
        try:
            self._journal_path.replace(self.previous_journal_path)
        except OSError:
            # A journal that cannot be rotated must not stop the daemon from
            # recording what it is doing; it keeps appending to the large file.
            return

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        line = json.dumps(_jsonable(record), sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        """Append one event and return it, so callers can also report it."""
        entry = {
            "at": dt.datetime.now(dt.UTC).isoformat(),
            "event": event,
            **fields,
        }
        self._rotate_if_oversized()
        self._append(self._journal_path, entry)
        return entry

    def read_journal_entries(self) -> list[dict[str, Any]]:
        """Every readable journal event across both generations, oldest first.

        Unparseable lines are skipped rather than fatal, for the same reason
        :meth:`read_equity_curve` skips them: a record truncated by a hard kill
        must not make the surrounding history unreadable.
        """
        entries: list[dict[str, Any]] = []
        for path in (self.previous_journal_path, self._journal_path):
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    entries.append(parsed)
        return entries

    def record_equity(
        self,
        *,
        session: dt.date,
        equity: Decimal,
        cash: Decimal,
        gross_exposure: float,
        positions: dict[str, Decimal],
        **fields: Any,
    ) -> dict[str, Any]:
        """Append one end-of-day equity observation."""
        entry = {
            "at": dt.datetime.now(dt.UTC).isoformat(),
            "session": session.isoformat(),
            "equity": equity,
            "cash": cash,
            "gross_exposure": gross_exposure,
            "positions": positions,
            **fields,
        }
        self._append(self._equity_curve_path, entry)
        return entry

    def read_equity_curve(self) -> list[dict[str, Any]]:
        """Every equity observation, oldest first.

        A line that will not parse is skipped rather than fatal: a truncated
        final record from a hard kill must not make the whole history
        unreadable, and the report says how many lines were unreadable.
        """
        if not self._equity_curve_path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._equity_curve_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "session" in parsed:
                entries.append(parsed)
        entries.sort(key=lambda item: str(item.get("session", "")))
        return entries

    def has_equity_for(self, session: dt.date) -> bool:
        """Whether this session already has an end-of-day observation.

        Guards against a restart writing a second observation for one day,
        which would put a duplicate point in every return series computed
        downstream.
        """
        target = session.isoformat()
        return any(entry.get("session") == target for entry in self.read_equity_curve())
