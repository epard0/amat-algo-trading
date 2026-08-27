"""The runtime paper/live switch shared by the bot and the dashboard.

The dashboard cannot flip a trading mode by itself, and deliberately so. It
writes a *request* into a small JSON file; the bot reads that file between
cycles and decides whether to honour it. Three properties fall out of that
split, and all three are the reason it is a file rather than a method call:

* **The dashboard never reaches into a running trader.** It has no handle on
  the client, the ledger, or an open position. The worst a compromised or
  buggy dashboard can do is ask.
* **A switch lands only when the bot is flat.** ``bot.py`` consults this
  between settlement and the next scan, so a request made mid-position is
  queued rather than applied under a position opened on the other book.
* **The bot has the last word on live.** A request for live is refused unless
  the process was itself started with every live prerequisite satisfied. The
  file cannot grant a permission ``.env`` withheld.

The bot also writes back what it is *actually* running, so the UI can tell
"requested and applied" from "requested, and nothing is listening" — the
second being what a stopped bot looks like, which would otherwise read as a
successful switch.

Nothing here talks to a network, a clock the caller cannot supply, or the
ledger; it is a file format and its invariants.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX only, which is every platform the installer supports.
    import fcntl
except ImportError:  # pragma: no cover - Windows is not a supported target
    fcntl = None  # type: ignore[assignment]

SCHEMA_VERSION = 1
MODES = ("paper", "live")
DEFAULT_FILENAME = "runtime_mode.json"

# A report older than this means nobody is applying requests: either the bot is
# stopped, or it is wedged. Comfortably longer than a 15-minute market plus
# settlement, so simply holding a position never looks like a dead process.
STALE_AFTER_SECONDS = 1_200


class ModeControlError(ValueError):
    """A request that cannot be represented, as opposed to one that is refused."""


def normalise(mode: str) -> str:
    candidate = (mode or "").strip().lower()
    if candidate not in MODES:
        raise ModeControlError(f"mode must be one of {', '.join(MODES)}")
    return candidate


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ModeState:
    """What has been asked for, and what is actually running."""

    requested: str = "paper"
    requested_at: datetime | None = None
    requested_by: str = ""
    # What the bot last reported. None means no bot has ever written here.
    active: str | None = None
    active_at: datetime | None = None
    active_pid: int | None = None
    # Whether the *running* bot could switch to live if asked. It reads .env
    # once at startup, so editing .env under a running bot does not change
    # this — and the UI has to be able to say so rather than fail obscurely.
    live_available: bool = False
    note: str = ""

    @property
    def pending(self) -> bool:
        """A request the bot has not yet reported acting on."""
        return self.active is not None and self.active != self.requested

    def is_stale(self, *, now: datetime | None = None) -> bool:
        """True when no bot has reported recently enough to be trusted."""
        if self.active_at is None:
            return True
        moment = now or datetime.now(timezone.utc)
        return (moment - self.active_at).total_seconds() > STALE_AFTER_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "requested": self.requested,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "requested_by": self.requested_by,
            "active": self.active,
            "active_at": self.active_at.isoformat() if self.active_at else None,
            "active_pid": self.active_pid,
            "live_available": self.live_available,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ModeState":
        """Tolerant by design: a damaged field falls back to paper, never live.

        This file is read on the path to placing real orders. Every ambiguity
        it can contain has to resolve to the safe side.
        """
        requested = str(payload.get("requested") or "paper").strip().lower()
        if requested not in MODES:
            requested = "paper"
        active = payload.get("active")
        if isinstance(active, str):
            active = active.strip().lower()
            if active not in MODES:
                active = None
        else:
            active = None
        pid = payload.get("active_pid")
        return cls(
            requested=requested,
            requested_at=_parse_time(payload.get("requested_at")),
            requested_by=str(payload.get("requested_by") or ""),
            active=active,
            active_at=_parse_time(payload.get("active_at")),
            active_pid=pid if isinstance(pid, int) else None,
            live_available=bool(payload.get("live_available")),
            note=str(payload.get("note") or ""),
        )


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Serialise read-modify-write across the bot and the dashboard.

    Both processes update disjoint fields of the same file, so a plain
    overwrite would let the bot's status report erase a request that arrived
    a millisecond earlier — the one failure that must not happen quietly, since
    the operator would see the switch acknowledged and then ignored.
    """
    if fcntl is None:  # pragma: no cover
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    handle = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


class ModeControl:
    """Read/modify/write access to one control file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    # -- reading ------------------------------------------------------------
    def read(self) -> ModeState:
        """Never raises. An unreadable file reads as a default paper state.

        A control file that cannot be parsed must not stop a bot that is
        already trading, and must not be interpreted as a request for live.
        """
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ModeState()
        except (OSError, json.JSONDecodeError):
            return ModeState(note="control file unreadable; defaulting to paper")
        if not isinstance(payload, dict):
            return ModeState(note="control file malformed; defaulting to paper")
        return ModeState.from_dict(payload)

    # -- writing ------------------------------------------------------------
    def _write(self, state: ModeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".runtime_mode.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(state.to_dict(), fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def request(
        self,
        mode: str,
        *,
        by: str = "",
        now: datetime | None = None,
    ) -> ModeState:
        """Ask the bot to run in ``mode``. Does not itself change any trading."""
        wanted = normalise(mode)
        with _locked(self.path):
            current = self.read()
            state = replace(
                current,
                requested=wanted,
                requested_at=now or datetime.now(timezone.utc),
                requested_by=by,
            )
            self._write(state)
        return state

    def report_active(
        self,
        mode: str,
        *,
        live_available: bool,
        pid: int | None = None,
        note: str = "",
        adopt_request: bool = False,
        now: datetime | None = None,
    ) -> ModeState:
        """The bot's heartbeat: what it is running, and what it could run.

        ``adopt_request`` is for the first report of a process launched with an
        explicit ``--live``/paper choice on the command line. The CLI is the
        operator speaking now; a stale request in the file is the operator
        speaking last week, and the command line wins.
        """
        running = normalise(mode)
        with _locked(self.path):
            current = self.read()
            moment = now or datetime.now(timezone.utc)
            state = replace(
                current,
                active=running,
                active_at=moment,
                active_pid=pid if pid is not None else os.getpid(),
                live_available=live_available,
                note=note,
            )
            if adopt_request:
                state = replace(
                    state,
                    requested=running,
                    requested_at=moment,
                    requested_by="command line",
                )
            self._write(state)
        return state


def default_path(ledger_path: Path | str) -> Path:
    """Sit beside the ledger: same directory, same lifetime, same backups."""
    return Path(ledger_path).parent / DEFAULT_FILENAME
