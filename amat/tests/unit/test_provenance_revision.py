from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from amat.provenance import detect_code_revision


def test_detect_code_revision_records_status_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    calls = 0

    def fake_run(*args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(args[0], 0, stdout="abc123\n", stderr="")
        raise subprocess.TimeoutExpired(args[0], timeout=10)

    monkeypatch.setattr(subprocess, "run", fake_run)

    revision = detect_code_revision(tmp_path)

    assert revision.detected is False
    assert revision.commit is None
    assert revision.dirty is None
    assert "timed out" in (revision.reason or "")
