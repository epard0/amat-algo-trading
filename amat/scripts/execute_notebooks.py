"""Execute the research notebooks headlessly and save their outputs in place.

    uv run --group notebooks python scripts/execute_notebooks.py

Execution is the honesty check for a generated notebook: committed outputs
must be outputs a kernel actually produced, never hand-written cell results.
Each notebook runs with the repository root as its working directory, exactly
as the setup cell expects.
"""

from __future__ import annotations

from pathlib import Path

import nbformat
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]

#: A Monte Carlo cell runs a pool of engine paths; give it real headroom.
_CELL_TIMEOUT_SECONDS = 3600


def main() -> None:
    notebooks = sorted((ROOT / "notebooks").glob("*.ipynb"))
    if not notebooks:
        print("no notebooks found; run scripts/build_notebooks.py first")
        return
    for path in notebooks:
        print(f"executing {path.name} ...", flush=True)
        notebook = nbformat.read(path, as_version=4)
        client = NotebookClient(
            notebook,
            timeout=_CELL_TIMEOUT_SECONDS,
            kernel_name="python3",
            resources={"metadata": {"path": str(ROOT)}},
        )
        client.execute()
        nbformat.write(notebook, path)
        print(f"  saved with outputs: {path}")


if __name__ == "__main__":
    main()
