from __future__ import annotations

import datetime as dt

import pytest

from amat.data import ingest as subject
from amat.data.ingest import IngestionPlan


class EmptyWindowClient:
    def execution_window_bars(self, *_args, **_kwargs):
        return []


def test_wide_checkpoint_is_written_in_batches_and_finally_flushed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = [dt.date(2020, 1, 1) + dt.timedelta(days=index) for index in range(52)]
    plan = IngestionPlan(["SPY"], sessions[0], sessions[-1] + dt.timedelta(days=1))
    writes = 0
    original = subject._write_checkpoint

    def counted(*args, **kwargs):
        nonlocal writes
        writes += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(subject, "_write_checkpoint", counted)
    checkpoint = tmp_path / "windows.json"
    result = subject._collect_window_bars(
        EmptyWindowClient(),
        plan,
        sessions,
        checkpoint_path=checkpoint,
        progress=lambda _message: None,
    )
    assert result == []
    assert writes == 3
    assert len(subject._read_checkpoint(checkpoint, plan)) == 52


def test_exception_flushes_every_completed_session(tmp_path) -> None:
    class FailingClient:
        calls = 0

        def execution_window_bars(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 8:
                raise RuntimeError("vendor failed")
            return []

    sessions = [dt.date(2020, 1, 1) + dt.timedelta(days=index) for index in range(20)]
    plan = IngestionPlan(["SPY"], sessions[0], sessions[-1] + dt.timedelta(days=1))
    checkpoint = tmp_path / "windows.json"
    with pytest.raises(RuntimeError, match="vendor failed"):
        subject._collect_window_bars(
            FailingClient(),
            plan,
            sessions,
            checkpoint_path=checkpoint,
            progress=lambda _message: None,
        )
    assert len(subject._read_checkpoint(checkpoint, plan)) == 7
