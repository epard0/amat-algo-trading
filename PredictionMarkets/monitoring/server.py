"""Read-only monitoring server: Prometheus metrics, JSON API, and the WebUI.

Run as its own process, never inside ``bot.py``:

    python -m monitoring.server --port 8787

It opens the ledger read-only and never writes to any bot state, so it can be
restarted or crash without touching an open position. It binds 127.0.0.1 by
default — the dashboard exposes full position and P&L history, and the VPS has
no host firewall (ingress is the Linode Cloud Firewall's job), so a default of
0.0.0.0 would publish the account's trading record to the internet. Reach it
over an SSH tunnel:

    ssh -p 2222 -N -L 8787:127.0.0.1:8787 <user>@<vps>

Routes:
    /                     the dashboard
    /metrics              Prometheus exposition for Grafana
    /api/overview         headline stats, calibration, funnel, health
    /api/trades           every trade, newest first
    /api/equity           realised P&L curve with drawdown
    /api/chart?asset=BTC  candles + indicators + trade markers
    /api/mode             GET the paper/live switch, POST to change it
    /healthz              liveness probe

The one thing this server writes is the mode control file, and it writes only
a *request* there: the bot reads it, re-checks every live prerequisite against
its own settings, and applies it between trades. Nothing here can place an
order, move funds, or touch the ledger. Pass --no-control to nail even that
shut. See execution/mode_control.py for why the switch is a file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import SERIES_BY_ASSET  # noqa: E402
from monitoring.chart import build_chart_payload  # noqa: E402
from monitoring.grafana_exporter import render_metrics  # noqa: E402
from monitoring.performance_tracker import (  # noqa: E402
    brier_score,
    calibration,
    edge_realisation,
    equity_curve,
    gate_funnel,
    group_summaries,
    health,
    longest_losing_streak,
    max_drawdown,
    current_streak,
    regime_distribution,
    summarize,
)
from monitoring.sources import DecisionTail, read_trades  # noqa: E402
from execution.mode_control import (  # noqa: E402
    ModeControl,
    ModeControlError,
    normalise,
)

WEBUI_DIR = Path(__file__).resolve().parent / "webui"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _json_default(value: Any) -> Any:
    """Decimals become strings, not floats.

    Money is Decimal throughout this codebase precisely so it does not acquire
    binary-float error; serialising it as a JSON number would undo that at the
    last step. The UI formats the strings.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: getattr(value, name) for name in value.__dataclass_fields__
        }
    raise TypeError(f"cannot serialise {type(value).__name__}")


def dumps(payload: Any) -> bytes:
    return json.dumps(payload, default=_json_default).encode("utf-8")


def env_live_problems(env_path: Path) -> list[str] | None:
    """Advisory read of .env: why live trading would be refused, if it would.

    Advisory, not authoritative. The bot reads .env once at startup and is the
    only process that can actually refuse a switch, so its own answer travels
    through the control file and wins. This exists for the case the bot is not
    running yet, where "start the bot" alone is a worse answer than "start the
    bot, and by the way ENABLE_LIVE_TRADING is still false".

    The checks themselves are not reimplemented here. A stand-in object carries
    the four attributes KalshiSettings.validate_live reads, and the real method
    is called against it, so the rule has exactly one definition. Returns None
    when the file cannot be assessed at all.
    """
    from types import SimpleNamespace

    try:
        from dotenv import dotenv_values

        from config import KalshiSettings
    except ImportError:
        return None
    if not env_path.is_file():
        return [f"no .env at {env_path}"]

    values = dotenv_values(env_path)
    raw_key = str(values.get("KALSHI_PRIVATE_KEY_PATH") or "private_key.pem")
    key_path = Path(raw_key).expanduser()
    if not key_path.is_absolute():
        key_path = env_path.parent / key_path
    try:
        subaccount = int(str(values.get("KALSHI_SUBACCOUNT") or "0"))
    except ValueError:
        return ["KALSHI_SUBACCOUNT is not a number"]

    stand_in = SimpleNamespace(
        enable_live_trading=str(values.get("ENABLE_LIVE_TRADING") or "").strip().lower()
        in {"1", "true", "yes", "on"},
        api_key_id=values.get("KALSHI_API_KEY") or None,
        private_key_path=key_path,
        subaccount=subaccount,
    )
    try:
        KalshiSettings.validate_live(stand_in, True)  # type: ignore[arg-type]
    except RuntimeError as exc:
        return [str(exc)]
    except AttributeError:
        # validate_live grew a check this stand-in does not model. Say nothing
        # rather than something wrong; the bot still enforces the real rule.
        return None
    return []


def build_mode_payload(
    control: ModeControl | None,
    env_path: Path,
) -> dict[str, Any]:
    if control is None:
        return {
            "control_enabled": False,
            "detail": "started with --no-control; the switch is read-only here",
        }
    state = control.read()
    stale = state.is_stale()
    problems = env_live_problems(env_path)
    return {
        "control_enabled": True,
        "path": str(control.path),
        "requested": state.requested,
        "requested_at": state.requested_at,
        "requested_by": state.requested_by,
        "active": state.active,
        "active_at": state.active_at,
        "active_pid": state.active_pid,
        "pending": state.pending,
        # No recent report means no bot is applying requests. Displayed as its
        # own state, because "requested live" against a stopped bot otherwise
        # looks exactly like a successful switch.
        "bot_seen": state.active is not None,
        "stale": stale,
        "live_available": state.live_available and not stale,
        "reason": state.note,
        "env": {
            "path": str(env_path),
            "checked": problems is not None,
            "problems": problems or [],
        },
    }


class MonitoringState:
    """Loads and caches bot state, so a browser refresh cannot hammer the disk."""

    def __init__(
        self,
        ledger_path: Path,
        decisions_path: Path,
        *,
        cache_seconds: float = 3.0,
    ) -> None:
        self.ledger_path = ledger_path
        self.decisions_path = decisions_path
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._loaded_at: float = 0.0
        self._trades: list[Any] = []
        self._decisions: list[dict[str, Any]] = []
        self._tail = DecisionTail(decisions_path, limit=50_000)

    def load(self, *, force: bool = False) -> tuple[list[Any], list[dict[str, Any]]]:
        import time

        with self._lock:
            if force or (time.monotonic() - self._loaded_at) > self.cache_seconds:
                self._trades = read_trades(self.ledger_path)
                # Incremental: only the bytes appended since the last refresh
                # are parsed. A full re-parse every cache_seconds is the single
                # most expensive thing this server did.
                self._decisions = self._tail.load()
                self._loaded_at = time.monotonic()
            return self._trades, self._decisions


def build_overview(trades: list[Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    modes: dict[str, Any] = {}
    for mode in ("live", "paper"):
        subset = [t for t in trades if t.mode == mode]
        summary = summarize(subset)
        curve = equity_curve(subset)
        streak_kind, streak_len = current_streak(subset)
        modes[mode] = {
            "trades": summary.trades,
            "open": summary.open_trades,
            "settled": summary.settled,
            "wins": summary.wins,
            "losses": summary.losses,
            "voids": summary.voids,
            "net_pnl": summary.net_pnl,
            "staked": summary.total_staked,
            "fees_paid": summary.fees_paid,
            "win_rate": summary.win_rate,
            "roi": summary.roi,
            "fee_drag": summary.fee_drag,
            "average_pnl": summary.average_pnl,
            "max_drawdown": max_drawdown(curve),
            "longest_losing_streak": longest_losing_streak(subset),
            "streak": {"kind": streak_kind, "length": streak_len},
        }

    status = health(trades, decisions, now=now)
    funnel = gate_funnel(decisions)
    return {
        "generated_at": now,
        "modes": modes,
        "model": {
            "brier_score": brier_score(trades),
            **edge_realisation(trades),
            "calibration": [
                {
                    "label": b.label,
                    "count": b.count,
                    "predicted": b.predicted_mean,
                    "actual": b.actual_rate,
                    "gap": b.gap,
                    "net_pnl": b.net_pnl,
                }
                for b in calibration(trades)
            ],
        },
        "funnel": {
            "evaluations": funnel.evaluations,
            "traded": funnel.traded,
            "errors": funnel.errors,
            "trade_rate": funnel.trade_rate,
            "blocked": [{"gate": g, "count": c} for g, c in funnel.blocked],
        },
        "regimes": regime_distribution(decisions),
        "breakdowns": {
            dimension: {
                name: {
                    "settled": s.settled,
                    "wins": s.wins,
                    "losses": s.losses,
                    "net_pnl": s.net_pnl,
                    "win_rate": s.win_rate,
                    "roi": s.roi,
                    "staked": s.total_staked,
                }
                for name, s in group_summaries(trades, dimension).items()
            }
            for dimension in ("asset", "regime", "outcome", "mode")
        },
        "health": {
            "ok": status.ok,
            "issues": status.issues,
            "last_decision_at": status.last_decision_at,
            "seconds_since_decision": status.seconds_since_decision,
            "last_trade_at": status.last_trade_at,
            "open_trades": status.open_trades,
            "consecutive_empty_discovery": status.consecutive_empty_discovery,
            "recent_errors": status.recent_errors,
        },
    }


def serialise_trades(trades: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "trade_id": t.trade_id,
            "mode": t.mode,
            "asset": t.asset,
            "ticker": t.ticker,
            "outcome": t.outcome,
            "status": t.status,
            "market_result": t.market_result,
            "placed_at": t.placed_at,
            "market_close_time": t.market_close_time,
            "settled_at": t.settled_at,
            "contracts": t.contracts,
            "entry_price": t.entry_price,
            "total_debit": t.total_debit,
            "fees_paid": t.fees_paid,
            "gross_payout": t.gross_payout,
            "pnl": t.pnl,
            "target_price": t.target_price,
            "model_probability": t.model_probability,
            "model_edge": t.model_edge,
            "regime": t.regime,
        }
        for t in sorted(
            trades,
            key=lambda t: t.placed_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    ]


class Handler(BaseHTTPRequestHandler):
    state: MonitoringState  # injected by serve()
    default_mode: str = "paper"  # which book the dashboard opens on
    control: ModeControl | None = None  # None when --no-control was passed
    env_path: Path = PROJECT_ROOT / ".env"
    server_version = "KalshiMonitoring/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Default logging writes every request to stderr, which under systemd
        # would bury the bot's own journal. Silence by default.
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    # -- helpers ------------------------------------------------------------
    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(dumps(payload), "application/json; charset=utf-8", status)

    def _static(self, name: str) -> None:
        # Resolve and confirm containment: never let a crafted path escape the
        # webui directory and serve .env or a private key.
        target = (WEBUI_DIR / name).resolve()
        if not target.is_file() or WEBUI_DIR.resolve() not in target.parents:
            self._json({"error": "not found"}, 404)
            return
        self._send(
            target.read_bytes(),
            CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
        )

    # -- routing ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        try:
            if route == "/":
                self._static("index.html")
            elif route in {"/app.js", "/styles.css", "/favicon.svg"}:
                self._static(route.lstrip("/"))
            elif route == "/healthz":
                self._json({"status": "ok"})
            elif route == "/api/config":
                # Which book the UI opens on. Both are always readable; this
                # only chooses the default view.
                self._json(
                    {
                        "default_mode": self.default_mode,
                        "ledger": str(self.state.ledger_path),
                        "decisions": str(self.state.decisions_path),
                        "control_enabled": self.control is not None,
                        # Served rather than hardcoded in the page. The chart
                        # selector listed five assets in JavaScript, so adding a
                        # series to config.py left the dashboard unable to chart
                        # it - and nothing failed, which is why it went unnoticed.
                        "assets": list(SERIES_BY_ASSET),
                    }
                )
            elif route == "/metrics":
                trades, decisions = self.state.load()
                self._send(
                    render_metrics(trades, decisions).encode("utf-8"),
                    "text/plain; version=0.0.4; charset=utf-8",
                )
            elif route == "/api/overview":
                trades, decisions = self.state.load()
                self._json(build_overview(trades, decisions))
            elif route == "/api/trades":
                trades, _ = self.state.load()
                self._json({"trades": serialise_trades(trades)})
            elif route == "/api/equity":
                trades, _ = self.state.load()
                mode = (query.get("mode") or [None])[0]
                subset = [t for t in trades if not mode or t.mode == mode]
                self._json(
                    {
                        "points": [
                            {
                                "timestamp": p.timestamp,
                                "pnl": p.pnl,
                                "cumulative": p.cumulative,
                                "peak": p.peak,
                                "drawdown": p.drawdown,
                                "asset": p.asset,
                                "status": p.status,
                                "trade_id": p.trade_id,
                            }
                            for p in equity_curve(subset)
                        ]
                    }
                )
            elif route == "/api/mode":
                self._json(build_mode_payload(self.control, self.env_path))
            elif route == "/api/chart":
                trades, decisions = self.state.load()
                asset = (query.get("asset") or ["BTC"])[0].upper()
                hours = int((query.get("hours") or ["48"])[0])
                self._json(
                    asyncio.run(build_chart_payload(asset, trades, decisions, hours=hours))
                )
            else:
                self._json({"error": "not found", "path": route}, 404)
        except BrokenPipeError:
            pass  # browser navigated away mid-response
        except Exception as exc:  # noqa: BLE001
            # A dashboard bug must surface as a 500, not kill the server thread.
            self._json({"error": type(exc).__name__, "detail": str(exc)}, 500)

    # -- the one mutating route --------------------------------------------
    def _same_origin(self) -> bool:
        """Reject a request a foreign page could have caused.

        This server binds loopback, which stops the network but not the
        browser: any page the operator visits can issue requests to
        127.0.0.1. Two things make that fail here — an Origin that is not
        ours is refused outright, and the X-KX15-Control header cannot be set
        by a form or an image, so a cross-origin fetch has to survive a
        preflight this server never answers.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # curl, or a same-origin GET-shaped POST
        host = self.headers.get("Host", "")
        return origin.split("://", 1)[-1] == host

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if route != "/api/mode":
                self._json({"error": "not found", "path": route}, 404)
                return
            if self.control is None:
                self._json(
                    {
                        "error": "control_disabled",
                        "detail": "This dashboard was started with --no-control.",
                    },
                    403,
                )
                return
            if self.headers.get("X-KX15-Control") != "1":
                self._json({"error": "missing_control_header"}, 403)
                return
            if not self._same_origin():
                self._json({"error": "cross_origin"}, 403)
                return

            length = int(self.headers.get("Content-Length") or 0)
            if length > 4096:
                self._json({"error": "body_too_large"}, 413)
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json({"error": "invalid_json"}, 400)
                return
            if not isinstance(body, dict):
                self._json({"error": "invalid_json"}, 400)
                return

            try:
                wanted = normalise(str(body.get("mode", "")))
            except ModeControlError as exc:
                self._json({"error": "invalid_mode", "detail": str(exc)}, 400)
                return

            payload = build_mode_payload(self.control, self.env_path)
            if wanted == "live":
                # Typing the word is not security - it is the difference
                # between meaning it and mis-clicking a toggle next to a
                # chart of your own money.
                if str(body.get("confirm", "")).strip().upper() != "LIVE":
                    self._json(
                        {
                            "error": "confirmation_required",
                            "detail": 'Send {"confirm": "LIVE"} to switch to live trading.',
                        },
                        400,
                    )
                    return
                refusal = self._live_refusal(payload)
                if refusal is not None:
                    self._json(refusal, 409)
                    return

            state = self.control.request(
                wanted, by=f"dashboard@{self.client_address[0]}"
            )
            result = build_mode_payload(self.control, self.env_path)
            result["accepted"] = True
            # A switch lands between trades, so what the operator needs to know
            # is whether one is in flight right now - not merely that the bot
            # has yet to acknowledge, which is true for a moment every time.
            trades, _ = self.state.load()
            in_flight = any(
                t.status == "OPEN" and t.mode == (state.active or "paper")
                for t in trades
            )
            result["applies_when"] = (
                "after the open position settles"
                if in_flight
                else "at the next scan"
            )
            self._json(result)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._json({"error": type(exc).__name__, "detail": str(exc)}, 500)

    @staticmethod
    def _live_refusal(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Why a live request cannot be honoured, phrased for a human.

        Ordered by what the operator has to do about it: fix the file, then
        start the process, then restart it. The bot re-checks all of this
        before it acts, so this is a better error message rather than the
        actual guard.
        """
        env = payload.get("env") or {}
        problems: list[str] = list(env.get("problems") or [])
        if problems:
            return {
                "error": "live_not_configured",
                "detail": problems[0],
                "problems": problems,
                "hint": (
                    f"Fix {env.get('path')}, then restart the bot so it re-reads it."
                ),
            }
        if not payload.get("bot_seen"):
            return {
                "error": "no_bot_running",
                "detail": "No bot has reported to this dashboard.",
                "hint": "Start the bot, then switch modes here.",
            }
        if payload.get("stale"):
            return {
                "error": "bot_not_responding",
                "detail": "The bot has not reported recently; it may have stopped.",
                "hint": "Check the bot process, then try again.",
            }
        if not payload.get("live_available"):
            return {
                "error": "live_not_available",
                "detail": payload.get("reason")
                or "The running bot cannot trade live.",
                "hint": (
                    "The bot reads .env once at startup. If you have just "
                    "enabled live trading there, restart it."
                ),
            }
        return None


def serve(
    ledger_path: Path,
    decisions_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    verbose: bool = False,
    default_mode: str = "paper",
    control_path: Path | None = None,
    env_path: Path | None = None,
) -> None:
    Handler.state = MonitoringState(ledger_path, decisions_path)
    Handler.default_mode = default_mode
    Handler.control = ModeControl(control_path) if control_path else None
    Handler.env_path = env_path or (PROJECT_ROOT / ".env")
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.verbose = verbose  # type: ignore[attr-defined]
    print(f"monitoring dashboard  http://{host}:{port}/")
    print(f"prometheus metrics    http://{host}:{port}/metrics")
    print(f"ledger                {ledger_path}")
    print(f"decisions             {decisions_path}")
    print(f"default view          {default_mode.upper()} book (read-only)")
    print(
        "mode switch           "
        + (f"{control_path}" if control_path else "disabled (--no-control)")
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()


@lru_cache(maxsize=1)
def _default_paths() -> tuple[Path, Path, Path]:
    """Resolve state paths from .env when available, else repo defaults."""
    ledger = PROJECT_ROOT / "trade_ledger.db"
    decisions = PROJECT_ROOT / "decisions.jsonl"
    control: Path | None = None
    try:
        from dotenv import dotenv_values

        values = dotenv_values(PROJECT_ROOT / ".env")
        if values.get("TRADE_LEDGER_PATH"):
            ledger = Path(str(values["TRADE_LEDGER_PATH"]))
        if values.get("DECISION_LOG_PATH"):
            decisions = Path(str(values["DECISION_LOG_PATH"]))
        if values.get("MODE_CONTROL_PATH"):
            control = Path(str(values["MODE_CONTROL_PATH"]))
    except Exception:  # noqa: BLE001
        pass
    if not ledger.is_absolute():
        ledger = PROJECT_ROOT / ledger
    if not decisions.is_absolute():
        decisions = PROJECT_ROOT / decisions
    # Defaults beside the ledger, matching config.KalshiSettings, so pointing
    # the dashboard at a ledger points it at that bot's switch too.
    if control is None:
        control = ledger.parent / "runtime_mode.json"
    elif not control.is_absolute():
        control = PROJECT_ROOT / control
    return ledger, decisions, control


def main(argv: list[str] | None = None) -> int:
    default_ledger, default_decisions, default_control = _default_paths()
    parser = argparse.ArgumentParser(
        description="Kalshi bot monitoring dashboard. Never places an order; the\n"
                    "only state it writes is a paper/live request the bot chooses\n"
                    "whether to honour."
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default 127.0.0.1; use an SSH tunnel for remote access)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--ledger", type=Path, default=default_ledger)
    parser.add_argument("--decisions", type=Path, default=default_decisions)
    parser.add_argument("--verbose", action="store_true", help="log every request")
    parser.add_argument(
        "--mode-file", type=Path, default=default_control,
        help="paper/live control file shared with the bot (default: beside the ledger)",
    )
    parser.add_argument(
        "--env", type=Path, default=PROJECT_ROOT / ".env",
        help="the bot's .env, read only to explain why live trading is unavailable",
    )
    parser.add_argument(
        "--no-control", action="store_true",
        help="serve the dashboard strictly read-only: the paper/live switch is "
             "shown but cannot be changed",
    )

    # Which book the dashboard opens on. This selects a *view*: the server is
    # read-only and cannot place an order, so unlike bot.py's --live it carries
    # no financial risk. Both books stay switchable in the UI regardless.
    # Default is paper, matching bot.py, so the riskier view is always explicit.
    book = parser.add_mutually_exclusive_group()
    book.add_argument(
        "--live", action="store_true",
        help="open the dashboard on the LIVE book (a view; to change what the "
             "bot trades, use the switch in the UI or --mode-file)",
    )
    book.add_argument(
        "--paper", action="store_true",
        help="open the dashboard on the paper book (default)",
    )
    args = parser.parse_args(argv)

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"WARNING: binding {args.host} exposes full P&L and position history "
            "to the network. Prefer an SSH tunnel.",
            file=sys.stderr,
        )
    serve(
        args.ledger,
        args.decisions,
        host=args.host,
        port=args.port,
        verbose=args.verbose,
        default_mode="live" if args.live else "paper",
        control_path=None if args.no_control else args.mode_file,
        env_path=args.env,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
