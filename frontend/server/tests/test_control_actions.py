import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.control_actions import capabilities, child_env, command, news_overview


def service(tmp_path, news=True):
    for relative in [
        "amat/.venv/bin/python",
        "amat/data/snapshots/registered/daily.parquet",
        "amat/configs/strategy/amat_v6.toml",
        "amat/configs/news/themes.toml",
    ]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    if news:
        db = tmp_path / "amat/data/news/news.sqlite3"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()
    state = tmp_path / "state"
    state.mkdir()
    return SimpleNamespace(root=tmp_path, storage=tmp_path / "runtime", state=state)


def act(capability, **inputs):
    return SimpleNamespace(capability=capability, inputs=inputs)


def by_id(svc):
    return {c["id"]: c for c in capabilities(svc.root, svc.state)}


def test_stop_is_always_available_and_resume_needs_explicit_enable(tmp_path, monkeypatch):
    svc = service(tmp_path)
    monkeypatch.delenv("TERMINAL_ENABLE_EXECUTOR_ACTIONS", raising=False)
    caps = by_id(svc)
    assert caps["executor.stop_submissions"]["supported"]
    assert not caps["executor.resume_submissions"]["supported"]
    monkeypatch.setenv("TERMINAL_ENABLE_EXECUTOR_ACTIONS", "1")
    assert by_id(svc)["executor.resume_submissions"]["supported"]


def test_resume_requires_typed_confirmation(tmp_path, monkeypatch):
    svc = service(tmp_path)
    monkeypatch.setenv("TERMINAL_ENABLE_EXECUTOR_ACTIONS", "1")
    with pytest.raises(HTTPException):
        command(svc, act("executor.resume_submissions", confirm="yes"))
    argv, _, _ = command(svc, act("executor.resume_submissions", confirm="RESUME"))
    assert argv[-1] == "resume" and str(svc.state) in argv


def test_interlock_round_trip_matches_executor_contract(tmp_path):
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "server.interlock", "--state-root", str(tmp_path), *a],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert run("stop", "--note", "drill").returncode == 0
    assert (tmp_path / "STOP_SUBMISSIONS").is_file()
    assert "already present" in run("stop").stdout  # idempotent, keeps first audit note
    assert "drill" in (tmp_path / "STOP_SUBMISSIONS").read_text()
    assert run("resume").returncode == 0
    assert not (tmp_path / "STOP_SUBMISSIONS").exists()
    assert "nothing to resume" in run("resume").stdout


def test_resume_refuses_symlink_and_missing_root(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    (tmp_path / "STOP_SUBMISSIONS").symlink_to(target)
    args = [sys.executable, "-m", "server.interlock", "--state-root"]
    cwd = Path(__file__).resolve().parents[2]
    assert (
        subprocess.run([*args, str(tmp_path), "resume"], cwd=cwd, capture_output=True).returncode
        == 2
    )
    assert target.read_text() == "keep"
    assert (
        subprocess.run(
            [*args, str(tmp_path / "nope"), "stop"], cwd=cwd, capture_output=True
        ).returncode
        == 2
    )


def test_news_discover_builder_is_bounded_and_registered(tmp_path):
    svc = service(tmp_path)
    argv, cwd, _ = command(svc, act("news.discover", themes="themes.toml", lookback_hours=24))
    assert "--db" in argv and "discover-assets" in argv and cwd == svc.root / "amat"
    assert Path(argv[argv.index("--out") + 1]) == svc.root / "amat/outputs/news/assets.json"
    for bad in (
        {"themes": "../x.toml"},
        {"lookback_hours": 0},
        {"min_sentiment": "nan"},
        {"extra": 1},
    ):
        with pytest.raises(HTTPException):
            command(svc, act("news.discover", **bad))


def test_news_ingest_is_gated_and_validates_inputs(tmp_path, monkeypatch):
    svc = service(tmp_path)
    monkeypatch.delenv("TERMINAL_ENABLE_NEWS_INGEST", raising=False)
    with pytest.raises(HTTPException):
        command(svc, act("news.ingest_alpaca"))
    monkeypatch.setenv("TERMINAL_ENABLE_NEWS_INGEST", "1")
    argv, _, _ = command(
        svc, act("news.ingest_alpaca", start="2020-01-01", end="2020-01-03", symbols="AAPL,MSFT")
    )
    assert argv[-2:] == ["--symbols", "AAPL,MSFT"]
    for bad in (
        {"start": "2020-01-01", "end": "2020-03-01"},  # > 14 days
        {"start": "2020-01-03", "end": "2020-01-01"},
        {"start": "2020-01-01", "end": "2020-01-02", "symbols": "aapl; rm"},
    ):
        with pytest.raises(HTTPException):
            command(svc, act("news.ingest_alpaca", **bad))


def test_ingest_gets_only_market_keys_and_read_actions_get_none(monkeypatch):
    env = {
        "PATH": "/bin",
        "ALPACA_MARKET_API_KEY": "k",
        "ALPACA_MARKET_API_SECRET": "s",
        "ALPACA_PAPER_API_KEY": "paper",
        "MARKETAUX_API_KEY": "m",
        "TELEGRAM_TOKEN": "t",
    }
    ingest = child_env("news.ingest_alpaca", env)
    assert set(ingest) == {"PATH", "ALPACA_MARKET_API_KEY", "ALPACA_MARKET_API_SECRET"}
    assert set(child_env("news.discover", env)) == {"PATH"}
    assert child_env("executor.status", env) is None


def test_analysis_builders_keep_holdout_guard_and_confine_output(tmp_path):
    svc = service(tmp_path)
    argv, _, _ = command(
        svc, act("amat.walkforward", snapshot="registered", start="2020-01-01", end="2023-12-31")
    )
    assert "--allow-holdout" not in argv and "--no-run-record" not in argv
    assert Path(argv[argv.index("--run-root") + 1]).is_relative_to(svc.storage)
    argv, _, _ = command(svc, act("amat.montecarlo", snapshot="registered", paths=200))
    assert argv[argv.index("--paths") + 1] == "200"
    with pytest.raises(HTTPException):
        command(svc, act("amat.montecarlo", snapshot="registered", paths=10**6))
    with pytest.raises(HTTPException):
        command(svc, act("amat.scan", snapshot="registered", session="2020-1-1"))
    assert command(svc, act("amat.status")) is None


def test_news_overview_reads_coverage_read_only(tmp_path):
    svc = service(tmp_path, news=False)
    assert news_overview(svc.root)["store"] is None
    db = svc.root / "amat/data/news/news.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE provider(provider_id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE article(article_id INTEGER PRIMARY KEY);"
        "CREATE TABLE article_version(version_id INTEGER PRIMARY KEY, published_at TEXT);"
        "CREATE TABLE ingestion_run(run_id INTEGER PRIMARY KEY, provider_id INT, status TEXT, started_at TEXT,"
        " finished_at TEXT, query_window_start TEXT, query_window_end TEXT, query_symbols TEXT,"
        " pages_fetched INT, rows_stored INT, failure_reason TEXT);"
        "INSERT INTO provider VALUES (1,'alpaca_news');"
        "INSERT INTO ingestion_run VALUES (1,1,'complete','t0','t1','a','b','',3,10,NULL);"
        "INSERT INTO article_version VALUES (1,'2026-10-12T00:00:00Z');"
    )
    con.commit()
    con.close()
    overview = news_overview(svc.root)
    assert (
        overview["runs"][0]["provider"] == "alpaca_news"
        and overview["counts"]["ingestion_run"] == 1
    )
    assert overview["newest_article_at"] == "2026-10-12T00:00:00Z" and overview["assets"] is None


def test_duration_screen_is_bounded_and_uses_the_native_read_only_command(tmp_path):
    svc = service(tmp_path)
    args, _, _ = command(svc, act('amat.scan', condition_sessions=12))
    assert args[args.index('--condition-sessions') + 1] == '12'
    assert 'scan' in args
    for duration in [-1, 253, 'not-a-number']:
        with pytest.raises(HTTPException):
            command(svc, act('amat.scan', condition_sessions=duration))
