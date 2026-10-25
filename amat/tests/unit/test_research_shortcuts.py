"""The short interface must preserve evidence and select datasets explicitly."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "args,expected",
    [
        (["run", "3"], ["control", "3"]),
        (["3", "--no-open"], ["control", "3", "--no-open"]),
        (["intraday", "4"], ["control", "--intraday", "4"]),
        (["advanced", "loop", "/some/session"], ["loop", "/some/session"]),
    ],
)
def test_shortcuts_preserve_arguments(args, expected):
    cmd, _ = script("research_cli").command(args)
    assert cmd[3:] == expected


def test_latest_results_use_frozen_runtime_without_bytecode(tmp_path):
    root = tmp_path / "workspace/amat"
    root.mkdir(parents=True)
    session = tmp_path / "amat-research/session"
    session.mkdir(parents=True)
    (session / "program.json").write_text("{}")
    (session.parent / "latest-session.json").write_text(json.dumps({"workspace": str(session)}))
    cmd, env = script("research_cli").command(["status"], root)
    assert cmd[3:] == ["status", str(session), "--human"]
    assert env["PYTHONPATH"] == str(session / "runtime")
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_daily_inspection_cannot_accidentally_open_intraday(tmp_path):
    (tmp_path / "program.json").write_text('{"schema":"intraday-research/1"}')
    with pytest.raises(ValueError, match="daily sessions"):
        script("research_cli").command(["charts", "--session", str(tmp_path)])


def test_latest_data_uses_actual_last_session_and_keeps_default_unchanged(tmp_path):
    snapshot = tmp_path / "data/snapshots/new"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.json").write_text('{"last_session":"2026-09-11"}')
    (tmp_path / "data/research-datasets.json").write_text(json.dumps({"daily": str(snapshot)}))
    cli = script("research_cli")
    cmd, _ = cli.command(["run", "2", "--latest-data"], tmp_path)
    assert cmd[3:] == [
        "control",
        "2",
        "--snapshot",
        str(snapshot),
        "--validation-end",
        "2026-09-11",
    ]
    ordinary, _ = cli.command(["run", "2"], tmp_path)
    assert ordinary[3:] == ["control", "2"]
    with pytest.raises(ValueError, match="explicit snapshot"):
        cli.command(["run", "--latest-data", "--snapshot=other"], tmp_path)


def test_tampered_minute_base_is_rejected_before_download(tmp_path, monkeypatch):
    import datetime as dt
    from types import SimpleNamespace

    update = script("update_research_data")
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(
        update, "load_manifest", lambda _: SimpleNamespace(last_session=dt.date(2026, 9, 11))
    )
    base = tmp_path / "original"
    base.mkdir()
    (base / "SPY-2025.parquet").write_bytes(b"modified")
    (base / "manifest.json").write_text(
        json.dumps(
            {
                "end": "2025-12-31",
                "partitions": {"SPY-2025.parquet": "0" * 64},
            }
        )
    )
    monkeypatch.setattr(update, "fetch", lambda *_: pytest.fail("Must not download on bad base"))
    with pytest.raises(ValueError, match="Base minute partition hash"):
        update.extend_minute(base, tmp_path, dt.date(2026, 9, 14))
    assert not (tmp_path / "data/minute/alpaca-sip-2022-2026-09-14").exists()


@pytest.mark.parametrize(
    "amounts,valid", [([0.018007, 0.031906], True), ([0.018007, 0.040000], False)]
)
def test_issuer_correction_preserves_raw_and_rejects_changed_values(tmp_path, amounts, valid):
    import datetime as dt

    from amat.data.alpaca import DailyBar, DividendEvent
    from amat.data.snapshot import load_corporate_actions, load_manifest, write_snapshot
    from autoresearch.workspace import digest_file

    update = script("update_research_data")
    date = dt.date(2021, 12, 1)
    bars = [DailyBar("SHY", date, 86, 87, 85, 86, 1000, 86, 100)]
    raw, _ = write_snapshot(
        tmp_path,
        "raw",
        raw_bars=bars,
        adjusted_bars=bars,
        window_bars=[],
        dividends=[DividendEvent("SHY", date, value) for value in amounts],
        splits=[],
        provider="alpaca",
        feed="sip",
        as_of=dt.datetime(2026, 9, 14, tzinfo=dt.UTC),
        execution_window_start=dt.time(9, 30),
        execution_window_end=dt.time(9, 35),
        exchange_timezone="America/New_York",
    )
    before = {p.name: digest_file(p) for p in raw.iterdir()}
    if not valid:
        with pytest.raises(ValueError, match="no longer matches"):
            update.normalize_daily(raw, raw)
        assert not raw.with_name("raw-verified").exists()
    else:
        target, _ = update.normalize_daily(raw, raw)
        assert load_manifest(target).action_rows == 1
        actions = load_corporate_actions(target, ["SHY"], sessions=[date])
        assert len(actions[date]) == 1
        import polars as pl

        assert pl.read_parquet(target / "actions.parquet")["value"].item() == pytest.approx(
            0.049913
        )
        assert update.normalize_daily(raw, raw)[0] == target  # Retry verifies without rewriting.
    assert {p.name: digest_file(p) for p in raw.iterdir()} == before
