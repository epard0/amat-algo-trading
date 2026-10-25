"""Freeze and execute the cost-aware switching and rank-stability autoresearch protocol."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Choose a new study directory")
    frozen = output / "frozen"
    frozen.mkdir(parents=True)
    for folder in ["src", "configs"]:
        shutil.copytree(
            source / folder, frozen / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    code = (
        "from pathlib import Path; import sys; "
        "from autoresearch.improvements.switching_study import register,run; "
        "register(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3])); "
        "run(Path(sys.argv[3]))"
    )
    subprocess.run(  # noqa: S603 - fixed interpreter and frozen research code
        [
            sys.executable,
            "-c",
            code,
            str(frozen),
            str(source / "data/snapshots/alpaca-sip-core-2026-10-13-verified"),
            str(output / "study"),
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(frozen / "src"),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
    )


if __name__ == "__main__":
    main()
