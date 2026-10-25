from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.engine_actions import capabilities, command


def service(tmp_path):
    for relative in [
        "amat/.venv/bin/python",
        "amat/data/snapshots/registered/daily.parquet",
        "amat/configs/strategy/amat_v6.toml",
    ]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return SimpleNamespace(root=tmp_path, storage=tmp_path / "runtime")


def test_historical_builder_is_inert_and_preserves_guards(tmp_path):
    svc = service(tmp_path)
    action = SimpleNamespace(
        capability="amat.backtest",
        inputs={
            "snapshot": "registered",
            "version": "v6",
            "start": "2020-01-01",
            "end": "2024-12-31",
        },
    )
    args, cwd, cap = command(svc, action)
    assert "--no-sleeves" in args and "--snapshot" in args and "--run-root" in args
    assert (
        "--allow-holdout" not in args and "--no-run-record" not in args and "--advisory" not in args
    )
    assert Path(args[args.index("--out") + 1]).is_relative_to(svc.storage)
    assert not svc.storage.exists()
    assert command(svc, action)[0] == args
    assert cwd == svc.root / "amat"
    assert cap["supported"]


def test_no_manual_plan_action_exists(tmp_path):
    # The executor daemon plans automatically; the terminal must not offer a second path.
    svc = service(tmp_path)
    assert "amat.plan.preview" not in {c["id"] for c in capabilities(svc.root, svc.storage)}
    assert command(svc, SimpleNamespace(capability="amat.plan.preview", inputs={})) is None


def test_historical_bounds_and_versions(tmp_path):
    svc = service(tmp_path)
    for inputs in [
        {"snapshot": "registered", "version": "v2"},
        {"snapshot": "registered", "start": "2025-01-01", "end": "2024-01-01"},
        {"snapshot": "registered", "initial_cash": "Infinity"},
        {"snapshot": "registered", "out": "/tmp/untrusted"},
    ]:
        with pytest.raises(HTTPException):
            command(svc, SimpleNamespace(capability="amat.backtest", inputs=inputs))
    assert command(svc, SimpleNamespace(capability="unknown", inputs={})) is None
