"""Shared forward data, independent paper accounts and a durable comparison campaign."""

import csv
import hashlib
import json
import os
import time
import uuid
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .comparison_metrics import metrics
from .config import Config
from .domain import HOUR, canonical, dec, digest
from .history import SignalHistory
from .marketdata import Feed
from .runtime import Runner, build, lease
from .venue import Transport


def atomic_json(path, body):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(body, indent=2) + "\n")
    temporary.replace(path)


def pointer(config):
    return config.database.parent / "comparison-current.json"


def prepare(config):
    if config.mode != "paper":
        raise ValueError("comparison campaigns are paper-only; live remains disabled")
    current = pointer(config)
    if current.exists():
        return json.loads(current.read_text())
    stamp = datetime.now(ZoneInfo("America/Detroit")).strftime("%Y%m%dT%H%M%S")
    campaign_id = stamp + "-" + uuid.uuid4().hex[:6]
    directory = config.database.parent / "comparisons" / campaign_id
    directory.mkdir(parents=True)
    profiles = config.path.parent / "strategies"
    accounts = []
    for profile_path in sorted(profiles.glob("*.toml")):
        from .profiles import StrategyProfile

        if StrategyProfile.load(profile_path).evidence.get("execution_status") in (
            "blocked",
            "paper_authorized",
        ):
            continue
        raw = deepcopy(config.raw)
        raw["mode"] = "paper"
        raw["execution_enabled"] = False
        raw["account"] = f"{campaign_id}:{profile_path.stem}"
        raw["database"] = str(directory / (profile_path.stem + ".sqlite"))
        raw["instruments"] = str(config.resolve(raw["instruments"]))
        raw["strategy"]["profile"] = str(profile_path.resolve())
        for field in ("amat_root", "python"):
            raw["strategy"][field] = str(config.resolve(raw["strategy"][field]))
        raw["data"]["cache"] = str(config.resolve(raw["data"]["cache"]))
        raw["paper"]["collateral"] = "10000"
        raw["paper"]["leverage_mode"] = "venue_max"
        raw["risk"]["max_gross"] = "100"
        raw["risk"]["max_order_notional"] = "1000000"
        raw["risk"]["quote_max_age_ms"] = 15000
        accounts.append(dict(name=profile_path.stem, config=raw))
    if not accounts:
        raise ValueError("no strategy profiles configured")
    manifest = dict(
        id=campaign_id,
        directory=str(directory),
        mode="paper",
        live_enabled=False,
        created_at=datetime.now(ZoneInfo("America/Detroit")).isoformat(),
        start_ms=None,
        poll_seconds=30,
        leverage_policy="venue_max_estimated_initial_margin",
        capital_per_account="10000",
        accounts=accounts,
    )
    atomic_json(directory / "manifest.json", manifest)
    atomic_json(current, manifest)
    return manifest


class CachedStrategy:
    def __init__(self, engine):
        self.engine = engine
        self.identity, self.symbols, self.profile = engine.identity, engine.symbols, engine.profile
        self.last_key = None
        self.last_decision = None

    def decide(self, candles, as_of_ms):
        # Source pin checks also run on cache hits, so another research session
        # cannot silently change a running comparison's strategy.
        for filename, expected in self.profile.sources.items():
            actual = hashlib.sha256(
                (self.engine.root / "src/autoresearch" / filename).read_bytes()
            ).hexdigest()
            if actual != expected:
                raise ValueError("comparison engine source changed; new exposure blocked")
        key = digest(dict(candles=candles, as_of_ms=as_of_ms))
        if key != self.last_key:
            self.last_decision = self.engine.decide(candles, as_of_ms)
            self.last_key = key
        return deepcopy(self.last_decision)


def configs(config, manifest):
    return [(a["name"], Config(config.path, a["config"])) for a in manifest["accounts"]]


def write_report(manifest, report):
    directory = Path(manifest["directory"])
    atomic_json(directory / "comparison.json", report)
    rows = []
    lines = [
        "# Forward paper strategy comparison",
        "",
        f"Campaign: {manifest['id']}",
        f"Updated: {report['updated_at']}",
        "",
        "All accounts start with $10,000. Live trading is disabled. Leverage uses published side/size estimates and initial-margin multipliers, with a 2% execution-cost reserve.",
        "",
        "| Strategy | State | Net P&L | Max DD | Fees | Funding | TP closes | Profitable closes | Leverage |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, entry in report["accounts"].items():
        m = entry["metrics"]
        row = dict(
            strategy=name,
            state=entry["state"],
            net_pnl=m["net_pnl"],
            max_drawdown=m["max_drawdown"],
            fees=m["fees"],
            funding=m["funding"],
            take_profit_closes=m["take_profit_closes"],
            profitable_closes=m["profitable_closes"],
            effective_leverage=m["effective_leverage"],
            take_profit_closes_per_30d=m["take_profit_closes_per_30d"],
        )
        rows.append(row)
        lines.append(
            f"| {name} | {entry['state']} | ${dec(m['net_pnl']):.2f} | {dec(m['max_drawdown']) * 100:.2f}% | ${dec(m['fees']):.2f} | ${dec(m['funding']):.2f} | {m['take_profit_closes']} | {m['profitable_closes']} | {dec(m['effective_leverage'] or 0):.2f}x |"
        )
    lines += [
        "",
        "Take-profit frequency counts completed episodes whose engine exit was attributed to a take-profit trigger. Profitable closes include other natural exits after episode fees and funding. Unattributed exits are reported separately in JSON. Frequency is not extrapolated before one full observation day.",
        "",
        "Drawdown is measured at recorded marks and after account mutations; REST sampling does not establish intrabar drawdown. The maximum-leverage overlay overrides original volatility/exposure sizing while retaining entry and exit signals.",
    ]
    temp = directory / "comparison.md.tmp"
    temp.write_text("\n".join(lines) + "\n")
    temp.replace(directory / "comparison.md")
    with (directory / "comparison.csv.tmp").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    (directory / "comparison.csv.tmp").replace(directory / "comparison.csv")


def status_all(config):
    path = pointer(config)
    if not path.exists():
        return dict(mode="paper", state="not_started", live_enabled=False)
    manifest = json.loads(path.read_text())
    report_path = Path(manifest["directory"]) / "comparison.json"
    report = (
        json.loads(report_path.read_text())
        if report_path.exists()
        else dict(accounts={}, state="starting")
    )
    report["campaign"] = manifest["id"]
    report["report"] = str(report_path.with_suffix(".md"))
    report["live_enabled"] = False
    report["heartbeat_age_seconds"] = (
        int(time.time() * 1000) - report.get("timestamp_ms", 0)
    ) / 1000
    try:
        with lease(Path(manifest["directory"]) / "campaign.sqlite"):
            report["runner_active"] = False
    except ValueError:
        report["runner_active"] = True
    return report


def stop_all(config, flatten=False):
    path = pointer(config)
    if not path.exists():
        return dict(state="not_started", live_enabled=False)
    manifest = json.loads(path.read_text())
    target = Path(manifest["directory"]) / "stop.json"
    instruction = dict(flatten=flatten, id=uuid.uuid4().hex)
    atomic_json(target, instruction)
    if not status_all(config).get("runner_active"):
        return run_all(config, once=True, stop_instruction=instruction)
    return dict(
        state="stop_requested", flatten=flatten, campaign=manifest["id"], live_enabled=False
    )


def run_all(config, once=False, stop_instruction=None):
    with ExitStack() as stack:
        stack.enter_context(lease(config.database.parent / "comparison-control.sqlite"))
        manifest = prepare(config)
        directory = Path(manifest["directory"])
        stack.enter_context(lease(directory / "campaign.sqlite"))
        stop_path = directory / "stop.json"
        if stop_path.exists() and stop_instruction is None:
            stop_path.unlink()  # Explicit run resumes the campaign.
        accounts = {}
        instruments = {}
        for name, cfg in configs(config, manifest):
            cfg.validate()
            if cfg.mode != "paper" or cfg.raw.get("execution_enabled") is not False:
                raise ValueError("comparison must never enable venue writes")
            stack.enter_context(lease(cfg.database))
            engine, broker = build(cfg, with_strategy=stop_instruction is None)
            stack.callback(broker.store.close)
            accounts[name] = (
                cfg,
                broker,
                Runner(cfg, CachedStrategy(engine) if engine else None, broker),
            )
            instruments.update(broker.instruments)
        # Deliberately no key lookup: only unsigned, read-only public transport.
        transport = Transport("production", writable=False)
        feed = Feed(transport, instruments, include_leverage=True)
        data = config.raw["data"]
        history = SignalHistory(
            config.resolve(data["cache"]), instruments, days=data.get("history_days", 90)
        )
        stack.callback(history.close)
        boundary = None
        candles = history_report = None
        report = dict(mode="paper", live_enabled=False, campaign=manifest["id"], accounts={})
        while True:
            try:
                stopping = stop_instruction or (
                    json.loads(stop_path.read_text()) if stop_path.exists() else None
                )
                current = transport.clock() // HOUR * HOUR
                if current != boundary and not stopping:
                    candles, history_report = history.load(current)
                    boundary = current
                event = feed.snapshot(15000)
                if manifest["start_ms"] is None:
                    manifest["start_ms"] = event["timestamp_ms"]
                    atomic_json(directory / "manifest.json", manifest)
                    atomic_json(pointer(config), manifest)
                try:
                    rates = feed.funding(manifest["start_ms"], event["timestamp_ms"])
                except (ValueError, RuntimeError, httpx.HTTPError):
                    if not stopping:
                        raise
                    rates = []  # Emergency closes do not depend on funding availability.
                for name, (cfg, broker, runner) in accounts.items():
                    subset = {t: event["quotes"][t] for t in broker.instruments}
                    tick = dict(
                        event, quotes=subset, event_id=manifest["id"] + ":" + event["event_id"]
                    )
                    try:
                        broker.snapshot(tick["event_id"], tick["timestamp_ms"], subset)
                        for rate in rates:
                            if rate["ticker"] in subset:
                                broker.apply_historical_funding(rate)
                        if stopping:
                            result = (
                                runner.flatten(stopping["id"])
                                if stopping["flatten"]
                                else broker.report()
                            )
                            state = "stopped"
                        else:
                            rows = {t: candles[t] for t in subset}
                            basis = {
                                t: abs(dec(rows[t][-1]["close"]) / dec(subset[t]["mark"]) - 1)
                                * 10000
                                for t in subset
                            }
                            if any(
                                v > dec(data.get("max_basis_bps", "500")) for v in basis.values()
                            ):
                                result = {}
                                state = "signal_basis_blocked"
                            else:
                                result = runner.process(tick, rows)
                                state = result["state"]
                        report["accounts"][name] = dict(
                            state=state,
                            metrics=metrics(
                                broker.store, manifest["start_ms"], event["timestamp_ms"]
                            ),
                            leverage_limits=result.get("leverage_limits"),
                            history={t: history_report[t] for t in subset}
                            if history_report
                            else None,
                            database=str(cfg.database),
                        )
                    except (ValueError, RuntimeError) as exc:
                        report["accounts"][name] = dict(
                            state="blocked",
                            error=str(exc),
                            metrics=metrics(
                                broker.store, manifest["start_ms"], event["timestamp_ms"]
                            ),
                            database=str(cfg.database),
                        )
                report.pop("error", None)  # A recovered feed has no current error.
                report.update(
                    snapshot_collection_ms=event.get("collection_duration_ms"),
                    state="stopped" if stopping else "running",
                    timestamp_ms=event["timestamp_ms"],
                    updated_at=datetime.now(ZoneInfo("America/Detroit")).isoformat(),
                    start_ms=manifest["start_ms"],
                    pid=os.getpid(),
                    leverage_policy=manifest["leverage_policy"],
                )
                write_report(manifest, report)
                print(
                    canonical(
                        dict(
                            state=report["state"],
                            updated_at=report["updated_at"],
                            accounts={n: a["state"] for n, a in report["accounts"].items()},
                        )
                    ),
                    flush=True,
                )
                if once or stopping:
                    return report
            except (ValueError, RuntimeError, httpx.HTTPError) as exc:
                report.update(
                    state="data_blocked",
                    error=str(exc),
                    timestamp_ms=transport.clock(),
                    updated_at=datetime.now(ZoneInfo("America/Detroit")).isoformat(),
                )
                write_report(manifest, report)
                print(canonical(dict(state="data_blocked", error=str(exc))), flush=True)
                if once:
                    raise
            for _ in range(manifest["poll_seconds"]):
                if stop_path.exists():
                    break
                time.sleep(1)
