"""Typed adapters for the submission interlock, newsdesk and further AMAT analyses.

Same contract as engine_actions: ``capabilities`` advertises schemas and effects,
``command`` validates inputs and returns an argv that is never built from free text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

from fastapi import HTTPException

from server.strategy_defaults import default_snapshot, default_strategy

NEWS_READ = {"news.coverage", "news.knowable", "news.discover"}
NEWS_INGEST = {"news.ingest_alpaca"}
INTERLOCK = {"executor.stop_submissions", "executor.resume_submissions"}
ANALYSIS = {"amat.scan", "amat.walkforward", "amat.montecarlo"}
HEAVY = {"amat.walkforward", "amat.montecarlo"}
OWNED = NEWS_READ | NEWS_INGEST | INTERLOCK | ANALYSIS
# Credentials the vendor intake needs; nothing else leaves the process environment.
NEWS_KEYS = ("ALPACA_MARKET_API_KEY", "ALPACA_MARKET_API_SECRET", "ALPACA_MARKET_API_ENDPOINT")
SYMBOLS = re.compile(r"[A-Z]{1,5}(?:[.-][A-Z])?(?:,[A-Z]{1,5}(?:[.-][A-Z])?){0,24}")


def _configs(root: Path) -> list[str]:
    return [
        p.name
        for p in sorted((root / "amat/configs/strategy").glob("*.toml"))
        if not p.is_symlink()
    ]


def _snapshots(root: Path) -> list[str]:
    base = root / "amat/data/snapshots"
    return [
        p.parent.name
        for p in sorted(base.glob("*/daily.parquet"))
        if not p.is_symlink() and p.resolve().is_relative_to(base.resolve())
    ]


def _themes(root: Path) -> list[str]:
    return [
        p.name for p in sorted((root / "amat/configs/news").glob("*.toml")) if not p.is_symlink()
    ]


def news_db(root: Path) -> Path:
    return Path(root) / "amat/data/news/news.sqlite3"


def capabilities(root: Path, state: Path) -> list[dict]:
    root = Path(root)
    amat_ready = (root / "amat/.venv/bin/python").exists()
    snapshots = _snapshots(root)
    configs = _configs(root)
    themes = _themes(root)
    state_ok = Path(state).is_dir()
    controls = _flag("TERMINAL_ENABLE_EXECUTOR_ACTIONS")
    ingest = _flag("TERMINAL_ENABLE_NEWS_INGEST")
    snapshot = {
        "type": "select",
        "options": snapshots,
        "default": default_snapshot(snapshots),
    }
    config = {
        "type": "select",
        "options": configs,
        "default": default_strategy(root),
    }
    news_ready = amat_ready and news_db(root).exists()
    why_news = None if news_ready else "AMAT runtime and an initialized news store are required"
    analysis_ready = amat_ready and bool(snapshots and configs)
    why_analysis = (
        None
        if analysis_ready
        else "AMAT runtime, a strategy configuration and a frozen snapshot are required"
    )
    window = {
        "start": {"type": "string", "default": "2019-01-01"},
        "end": {"type": "string", "default": "2024-12-31"},
    }

    def cap(id_, supported, reason, effects, inputs):
        return {
            "id": id_,
            "capability": id_,
            "supported": supported,
            "reason": None if supported else reason,
            "effects": effects,
            "inputs": inputs,
        }

    return [
        cap(
            "executor.stop_submissions",
            state_ok,
            "Executor state root is not available",
            [
                "Creates STOP_SUBMISSIONS in the selected executor state root",
                "The daemon checks it before the submission phase and between order POSTs; no restart needed",
                "Does not cancel orders already accepted by the broker",
            ],
            {
                "note": {
                    "type": "string",
                    "default": "",
                    "description": "Optional operator reason recorded in the file",
                }
            },
        ),
        cap(
            "executor.resume_submissions",
            state_ok and controls,
            "Requires TERMINAL_ENABLE_EXECUTOR_ACTIONS=1 and an available executor state root"
            if state_ok
            else "Executor state root is not available",
            [
                "Removes STOP_SUBMISSIONS so the daemon may submit orders again at its next phase",
                "Unresolved submission receipts and every other executor safeguard still apply",
                "Reconcile uncertain orders before resuming",
            ],
            {"confirm": {"type": "string", "default": "", "description": "Type RESUME to confirm"}},
        ),
        cap(
            "news.coverage",
            news_ready,
            why_news,
            ["Reads every ingestion run and its outcome from the news store"],
            {},
        ),
        cap(
            "news.knowable",
            news_ready,
            why_news,
            [
                "Reads articles knowable now from the news store; vendor entity tags are a lower bound"
            ],
            {
                "symbol": {
                    "type": "string",
                    "default": "",
                    "description": "Optional vendor-tagged symbol",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
        ),
        cap(
            "news.discover",
            news_ready and bool(themes),
            why_news or "No reviewed theme taxonomy found",
            [
                "Matches knowable headlines to the reviewed theme taxonomy",
                "Writes amat/outputs/news/assets.json; news controls eligibility only, never weights or orders",
            ],
            {
                "themes": {
                    "type": "select",
                    "options": themes,
                    "default": themes[0] if themes else "",
                },
                "lookback_hours": {"type": "integer", "minimum": 1, "maximum": 720, "default": 72},
                "min_sentiment": {"type": "string", "default": "0.15"},
                "min_articles": {"type": "integer", "minimum": 1, "maximum": 50, "default": 1},
                "max_assets": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                "ttl_hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 30},
            },
        ),
        cap(
            "news.ingest_alpaca",
            news_ready and ingest,
            why_news
            or "Requires TERMINAL_ENABLE_NEWS_INGEST=1 and Alpaca market-data credentials in the gateway environment",
            [
                "Contacts the Alpaca news archive (read-only vendor access)",
                "Appends articles to the news store and records an immutable coverage run",
                "Archive rows are stamped with the actual ingestion time, not backdated",
            ],
            {
                "start": {
                    "type": "string",
                    "default": (dt.date.today() - dt.timedelta(days=3)).isoformat(),
                },
                "end": {"type": "string", "default": dt.date.today().isoformat()},
                "symbols": {
                    "type": "string",
                    "default": "",
                    "description": "Optional comma-separated tickers",
                },
            },
        ),
        cap(
            "amat.scan",
            analysis_ready,
            why_analysis,
            ["Ranks symbols by the strategy's own trend features as of one session; read-only"],
            {
                "snapshot": snapshot,
                "config": config,
                "session": {
                    "type": "string",
                    "default": "",
                    "description": "Optional decision session YYYY-MM-DD",
                },
                "top": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "condition_sessions": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 252,
                    "default": 0,
                    "description": "Require this many past completed trading days; 0 keeps the original configured-universe scan.",
                },
            },
        ),
        cap(
            "amat.walkforward",
            analysis_ready,
            why_analysis,
            [
                "Expanding walk-forward on a frozen snapshot; writes native run evidence under terminal storage",
                "Holdout guard stays on; a dataset crossing its boundary is refused",
            ],
            {
                "snapshot": snapshot,
                "config": config,
                **window,
                "initial_cash": {"type": "string", "default": "100000"},
            },
        ),
        cap(
            "amat.montecarlo",
            analysis_ready,
            why_analysis,
            [
                "Block-bootstrap Monte Carlo on a frozen snapshot; writes native run evidence under terminal storage",
                "Holdout guard stays on; a dataset crossing its boundary is refused",
            ],
            {
                "snapshot": snapshot,
                "config": config,
                **window,
                "initial_cash": {"type": "string", "default": "100000"},
                "paths": {"type": "integer", "minimum": 100, "maximum": 2000, "default": 500},
                "seed": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 2147483647,
                    "default": 20260808,
                },
            },
        ),
    ]


def _flag(name: str) -> bool:
    import os

    return os.getenv(name) == "1"


def _money(value, field):
    from server.engine_actions import _money as money

    return money(value, field)


def _date(value, field, optional=False):
    if optional and value in ("", None):
        return None
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = dt.date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise HTTPException(422, f"{field} must be YYYY-MM-DD")


def _int(value, field, low, high):
    if type(value) is not int or not low <= value <= high:
        raise HTTPException(422, f"{field} must be an integer from {low} to {high}")
    return value


def _choice(value, field, options):
    if value not in options:
        raise HTTPException(422, f"Unknown {field}")
    return value


def _history_window(inputs):
    start = _date(inputs.get("start", "2019-01-01"), "start")
    end = _date(inputs.get("end", "2024-12-31"), "end")
    if (
        start > end
        or start < dt.date(1990, 1, 1)
        or end > dt.date.today()
        or (end - start).days > 30 * 366
    ):
        raise HTTPException(422, "Invalid historical range; use YYYY-MM-DD and at most 30 years")
    return start, end


def command(service, action):
    cap_id = action.capability
    if cap_id not in OWNED:
        return None
    root = Path(service.root).resolve()
    state = Path(service.state).resolve()
    storage = Path(service.storage).resolve()
    cap = next(c for c in capabilities(root, state) if c["id"] == cap_id)
    if not cap["supported"]:
        raise HTTPException(422, cap["reason"])
    inputs = action.inputs
    allowed = set(cap["inputs"])
    if set(inputs) - allowed:
        raise HTTPException(422, "Unknown input fields")
    amat_python = str(root / "amat/.venv/bin/python")

    if cap_id in INTERLOCK:
        base = [sys.executable, "-m", "server.interlock", "--state-root", str(state)]
        if cap_id == "executor.stop_submissions":
            note = inputs.get("note", "")
            if not isinstance(note, str) or len(note) > 200 or "\n" in note:
                raise HTTPException(422, "note must be one line of at most 200 characters")
            return base + ["stop", "--note", note], root / "frontend", cap
        if inputs.get("confirm") != "RESUME":
            raise HTTPException(422, "Type RESUME in the confirm field to remove the interlock")
        return base + ["resume"], root / "frontend", cap

    news = [amat_python, "-m", "newsdesk.cli", "--db", str(news_db(root))]
    cwd = root / "amat"
    if cap_id == "news.coverage":
        return news + ["coverage"], cwd, cap
    if cap_id == "news.knowable":
        args = ["knowable", "--limit", str(_int(inputs.get("limit", 25), "limit", 1, 100))]
        symbol = inputs.get("symbol", "")
        if symbol:
            if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z]{1,5}(?:[.-][A-Z])?", symbol):
                raise HTTPException(422, "symbol must be an upper-case ticker")
            args += ["--symbol", symbol]
        return news + args, cwd, cap
    if cap_id == "news.discover":
        themes = _choice(
            inputs.get("themes", cap["inputs"]["themes"]["default"]),
            "themes",
            cap["inputs"]["themes"]["options"],
        )
        try:
            sentiment = float(inputs.get("min_sentiment", "0.15"))
            if not -1 <= sentiment <= 1:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(422, "min_sentiment must be a number from -1 to 1")
        out = root / "amat/outputs/news/assets.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        return (
            news
            + [
                "discover-assets",
                "--themes",
                str(root / "amat/configs/news" / themes),
                "--out",
                str(out),
                "--lookback-hours",
                str(_int(inputs.get("lookback_hours", 72), "lookback_hours", 1, 720)),
                "--min-sentiment",
                repr(sentiment),
                "--min-articles",
                str(_int(inputs.get("min_articles", 1), "min_articles", 1, 50)),
                "--max-assets",
                str(_int(inputs.get("max_assets", 8), "max_assets", 1, 50)),
                "--ttl-hours",
                str(_int(inputs.get("ttl_hours", 30), "ttl_hours", 1, 168)),
            ],
            cwd,
            cap,
        )
    if cap_id == "news.ingest_alpaca":
        start = _date(inputs.get("start", cap["inputs"]["start"]["default"]), "start")
        end = _date(inputs.get("end", cap["inputs"]["end"]["default"]), "end")
        if start > end or end > dt.date.today() or (end - start).days > 14:
            raise HTTPException(
                422, "Use a window of at most 14 days that does not end in the future"
            )
        args = ["ingest-alpaca", "--start", start.isoformat(), "--end", end.isoformat()]
        symbols = inputs.get("symbols", "")
        if symbols:
            if not isinstance(symbols, str) or not SYMBOLS.fullmatch(symbols):
                raise HTTPException(
                    422, "symbols must be up to 25 comma-separated upper-case tickers"
                )
            args += ["--symbols", symbols]
        return news + args, cwd, cap

    # AMAT analyses
    options = cap["inputs"]
    snapshot = _choice(
        inputs.get("snapshot", options["snapshot"]["default"]),
        "registered snapshot",
        options["snapshot"]["options"],
    )
    config = _choice(
        inputs.get("config", options["config"]["default"]),
        "registered configuration",
        options["config"]["options"],
    )
    dataset = root / "amat/data/snapshots" / snapshot
    config_path = str(root / "amat/configs/strategy" / config)
    base = [amat_python, "-m", "amat.cli.main"]
    if cap_id == "amat.scan":
        args = [
            "scan",
            config_path,
            "--snapshot",
            str(dataset),
            "--top",
            str(_int(inputs.get("top", 20), "top", 1, 100)),
            "--json",
        ]
        duration = _int(inputs.get("condition_sessions", 0), "condition_sessions", 0, 252)
        if duration:
            args += ["--condition-sessions", str(duration)]
        session = _date(inputs.get("session", ""), "session", optional=True)
        if session:
            args += ["--session", session.isoformat()]
        return base + args, cwd, cap
    start, end = _history_window(inputs)
    cash = _money(inputs.get("initial_cash", "100000"), "initial_cash")
    fingerprint = hashlib.sha256(
        json.dumps({"c": cap_id, "i": inputs}, sort_keys=True).encode()
    ).hexdigest()[:24]
    runs = storage / "engine-artifacts" / fingerprint / "runs"
    common = [
        "--snapshot",
        str(dataset),
        "--start",
        start.isoformat(),
        "--end",
        end.isoformat(),
        "--initial-cash",
        cash,
        "--run-root",
        str(runs),
        "--json",
    ]
    if cap_id == "amat.walkforward":
        return base + ["walkforward", config_path] + common, cwd, cap
    paths = _int(inputs.get("paths", 500), "paths", 100, 2000)
    seed = _int(inputs.get("seed", 20260808), "seed", 0, 2147483647)
    return (
        base
        + ["montecarlo", config_path]
        + common
        + ["--paths", str(paths), "--seed", str(seed), "--workers", "2"],
        cwd,
        cap,
    )


def child_env(capability: str, env: dict) -> dict | None:
    """Environment override for owned capabilities; None leaves the caller's policy in force."""
    if capability in NEWS_INGEST:
        scrubbed = {
            k: v
            for k, v in env.items()
            if not re.search(
                r"secret|token|api.?key|password|smtp|mailgun|telegram|alpaca|adanos|marketaux",
                k,
                re.I,
            )
        }
        scrubbed.update({k: env[k] for k in NEWS_KEYS if k in env})
        return scrubbed
    if capability in NEWS_READ | ANALYSIS:
        return {
            k: v
            for k, v in env.items()
            if not re.search(
                r"secret|token|api.?key|password|smtp|mailgun|telegram|alpaca|adanos|marketaux",
                k,
                re.I,
            )
        }
    return None


def timeout_seconds(capability: str) -> int | None:
    if capability in HEAVY:
        return 3600
    if capability in NEWS_INGEST:
        return 900
    return None


def news_overview(root: Path) -> dict:
    """Read-only view of the news store's coverage record and the last discovery artifact."""
    root = Path(root)
    db = news_db(root)
    result: dict = {
        "store": str(db.relative_to(root)) if db.exists() else None,
        "runs": [],
        "counts": {},
        "assets": None,
    }
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            result["counts"] = {
                t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("article", "article_version", "ingestion_run")
            }
            result["runs"] = [
                dict(r)
                for r in con.execute(
                    "SELECT r.run_id, p.name AS provider, r.status, r.started_at, r.finished_at, r.query_window_start, "
                    "r.query_window_end, r.query_symbols, r.pages_fetched, r.rows_stored, r.failure_reason "
                    "FROM ingestion_run r JOIN provider p USING(provider_id) ORDER BY r.run_id DESC LIMIT 50"
                )
            ]
            newest = con.execute("SELECT MAX(published_at) FROM article_version").fetchone()
            result["newest_article_at"] = newest[0] if newest else None
            con.close()
        except sqlite3.Error as exc:
            result["error"] = f"News store unreadable: {exc}"
    assets = root / "amat/outputs/news/assets.json"
    if assets.is_file() and not assets.is_symlink() and assets.stat().st_size < 4_000_000:
        try:
            result["assets"] = json.loads(assets.read_text())
            result["assets_modified_at"] = dt.datetime.fromtimestamp(
                assets.stat().st_mtime, dt.UTC
            ).isoformat()
        except ValueError:
            result["assets"] = None
    return result
