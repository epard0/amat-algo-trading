"""Set or clear the executor's STOP_SUBMISSIONS runtime interlock.

The executor only tests that the file exists (executor.safety.submission_interlock),
so the content is a human-readable audit note. Stopping is fail-safe and idempotent;
resuming removes only that one regular file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    stop = sub.add_parser("stop")
    stop.add_argument("--note", default="")
    sub.add_parser("resume")
    args = parser.parse_args(argv)
    root = args.state_root
    if not root.is_dir():
        print(f"state root {root} is not a directory", file=sys.stderr)
        return 2
    path = root / "STOP_SUBMISSIONS"
    if args.action == "stop":
        stamp = dt.datetime.now(dt.UTC).isoformat()
        text = f"stopped by AMAT Terminal at {stamp}\n" + (
            f"note: {args.note}\n" if args.note else ""
        )
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            print("STOP_SUBMISSIONS already present; submissions remain stopped")
            return 0
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        print("STOP_SUBMISSIONS created; the daemon halts submission at its next check")
        return 0
    if path.is_symlink() or (path.exists() and not path.is_file()):
        print("STOP_SUBMISSIONS is not a regular file; refusing to touch it", file=sys.stderr)
        return 2
    if not path.exists():
        print("STOP_SUBMISSIONS not present; nothing to resume")
        return 0
    path.unlink()
    print("STOP_SUBMISSIONS removed; other executor interlocks still apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
