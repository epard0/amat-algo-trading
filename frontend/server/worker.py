"""Run one persisted typed capability independently of the HTTP process."""

from __future__ import annotations

import argparse
import fcntl
import json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("root", "storage", "state", "job"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--lease-fd", type=int, required=True)
    args = parser.parse_args()
    # The kernel lease is held from before API dispatch until this process exits.
    fcntl.flock(args.lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    from server.app import Action, Service, now

    service = Service(args.root, args.state, args.storage, recover=False)
    row = service.db.execute("SELECT data FROM jobs WHERE id=?", (args.job,)).fetchone()
    if row is None:
        return 2
    job = json.loads(row["data"])
    if job["status"] != "queued":
        return 3
    try:
        command, cwd, _ = service.command(
            Action(capability=job["capability"], inputs=job["inputs"])
        )
        service.run(job, command, cwd)
    except Exception as exc:
        job.update(status="refused", finished_at=now(), reason=str(getattr(exc, "detail", exc)))
        service.save_job_event(job, {"reason": job["reason"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
