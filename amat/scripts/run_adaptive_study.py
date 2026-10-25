"""Freeze source, register fixed adaptive-rotation arms, and execute with autoresearch."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from autoresearch.improvements.adaptive_study import LABELS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 5))
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Choose a new immutable study directory")
    runtime = output / "frozen"
    runtime.mkdir(parents=True)
    for directory in ["src", "configs"]:
        shutil.copytree(
            source / directory,
            runtime / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    env = {
        **os.environ,
        "PYTHONPATH": str(runtime / "src"),
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    snapshot = source / "data/snapshots/alpaca-sip-core-2026-10-13-verified"
    study = output / "study"
    register = (
        "from pathlib import Path; "
        "from autoresearch.improvements.adaptive_study import register; import sys; "
        "register(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]))"
    )
    subprocess.run(  # noqa: S603 - fixed interpreter code and registered arguments
        [sys.executable, "-c", register, str(runtime), str(snapshot), str(study)],
        env=env,
        check=True,
    )

    def run(arm: str, cost: int) -> None:
        code = (
            "from pathlib import Path; "
            "from autoresearch.improvements.study import run_arm; import sys; "
            "run_arm(Path(sys.argv[1]),sys.argv[2],int(sys.argv[3]))"
        )
        subprocess.run(  # noqa: S603 - fixed interpreter code and registered arguments
            [sys.executable, "-c", code, str(study), arm, str(cost)], env=env, check=True
        )
        print(f"Completed {arm} at {cost} bps", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, arm, cost) for arm in LABELS for cost in [2, 6, 10]]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    code = (
        "from pathlib import Path; "
        "from autoresearch.improvements.adaptive_study import report; import sys; "
        "report(Path(sys.argv[1]))"
    )
    subprocess.run([sys.executable, "-c", code, str(study)], env=env, check=True)  # noqa: S603
    protocol = json.loads((study / "protocol.json").read_text())
    print(
        f"Finished {len(LABELS) * 3} fixed runs; protocol source root {protocol['root']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
