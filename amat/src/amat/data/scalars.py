"""Per-session weight-scalar files: the v3 conditioning handoff.

Strategy v3 conditions the v1 core on point-in-time news, but ``amat`` and
``newsdesk`` deliberately never import each other: the strategy reaches news
only through a *file* produced ahead of the run (``newsdesk export-scalars``).
That file format lives here.

Format
------
CSV with an exact ``session,scalar`` header. ``#`` lines are comments (the
exporter records its parameters there, which makes the file self-describing).
One row per session date, ISO format, scalar in ``[0, 1]``.

Alignment is fail-closed: every panel session must be present exactly once. A
missing session silently defaulting to 1.0 would make "the store had no
coverage" indistinguishable from "the signal said full size", which is the
exact confusion point-in-time discipline exists to prevent. Rows for sessions
outside the panel are ignored (a file may legitimately span more history than
one windowed run).
"""

from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from amat.errors import DataIntegrityError

__all__ = ["load_weight_scalars"]

_HEADER = ("session", "scalar")
#: Refuse to report more than this many missing sessions individually.
_MAX_LISTED = 5


def load_weight_scalars(path: Path, sessions: Sequence[dt.date]) -> np.ndarray:
    """Read a scalar file and align it to ``sessions``.

    Returns:
        A ``(len(sessions),)`` float array in ``[0, 1]``.

    Raises:
        DataIntegrityError: on an unreadable file, a wrong header, a duplicate
            or unparseable row, a value outside ``[0, 1]``, or any panel
            session the file does not cover.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"could not read weight scalars from {path}: {exc}"
        raise DataIntegrityError(message) from exc

    lines = [line for line in raw.splitlines() if line.strip() and not line.startswith("#")]
    reader = csv.reader(lines)
    try:
        header = next(reader)
    except StopIteration as exc:
        message = f"{path}: no rows; expected a 'session,scalar' header and one row per session"
        raise DataIntegrityError(message) from exc
    if tuple(part.strip() for part in header) != _HEADER:
        message = f"{path}: header must be exactly 'session,scalar', found {header!r}"
        raise DataIntegrityError(message)

    by_session: dict[dt.date, float] = {}
    for number, row in enumerate(reader, start=2):
        if len(row) != len(_HEADER):
            message = f"{path} row {number}: expected 2 fields, found {len(row)}"
            raise DataIntegrityError(message)
        try:
            session = dt.date.fromisoformat(row[0].strip())
            scalar = float(row[1])
        except ValueError as exc:
            message = f"{path} row {number}: {exc}"
            raise DataIntegrityError(message) from exc
        if not np.isfinite(scalar) or not 0.0 <= scalar <= 1.0:
            message = (
                f"{path} row {number}: scalar {row[1]!r} outside [0, 1]; "
                "conditioning may reduce risk, never add"
            )
            raise DataIntegrityError(message)
        if session in by_session:
            message = f"{path} row {number}: duplicate session {session.isoformat()}"
            raise DataIntegrityError(message)
        by_session[session] = scalar

    missing = [session for session in sessions if session not in by_session]
    if missing:
        shown = ", ".join(session.isoformat() for session in missing[:_MAX_LISTED])
        suffix = "" if len(missing) <= _MAX_LISTED else f" (+{len(missing) - _MAX_LISTED} more)"
        message = (
            f"{path} lacks {len(missing)} of the run's {len(sessions)} sessions: "
            f"{shown}{suffix}. A missing session must not silently default to full "
            "size; re-export the file over the run's window"
        )
        raise DataIntegrityError(message)

    return np.array([by_session[session] for session in sessions], dtype=float)
