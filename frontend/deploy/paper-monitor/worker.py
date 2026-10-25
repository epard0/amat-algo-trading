#!/usr/bin/env python3
"""Bounded, read-only VPS observation; daily evidence survives desktop absence."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paper_session import project  # noqa: E402

STORE = Path(
    os.environ.get("AMAT_MONITOR_STORE", str(Path.home() / ".local/share/amat/paper-monitor"))
)
ZONE = ZoneInfo("America/Detroit")
MIN_SESSION = dt.date(2026, 10, 21)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def selected_session(now, calendar):
    today = max(now.astimezone(ZONE).date(), MIN_SESSION)
    available = [x for x in calendar if dt.date.fromisoformat(x["date"]) >= today]
    if not available:
        raise ValueError("Broker calendar has no upcoming session")
    return min(available, key=lambda x: x["date"])


def calendar_for(now):
    day = max(now.astimezone(ZONE).date(), MIN_SESSION)
    path = STORE / "calendar.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    if previous.get("requested_day") == str(day):
        return previous["sessions"]
    code = """import datetime as dt,json;from executor.config import ExecutorSettings,PAPER_TRADING_HOST;from executor.broker import AlpacaPaperClient;s=ExecutorSettings.from_environment();assert PAPER_TRADING_HOST=='paper-api.alpaca.markets';c=AlpacaPaperClient(s.trading_key_id,s.trading_secret_key);start=dt.date.fromisoformat('__DAY__');print(json.dumps([{'date':str(x.date),'open':str(x.open_local),'close':str(x.close_local)} for x in c.get_calendar(start,start+dt.timedelta(days=14))]))""".replace(
        "__DAY__", str(day)
    )
    p = subprocess.run(
        ["docker", "exec", "amat-executor", "python", "-c", code],
        capture_output=True,
        text=True,
        timeout=35,
    )
    if p.returncode:
        raise RuntimeError("Broker calendar request failed")
    rows = json.loads(p.stdout)
    selected_session(now, rows)
    atomic(path, dict(requested_day=str(day), sessions=rows, source="Alpaca paper calendar"))
    return rows


def update_alerts(previous, issues, now, session, store, reliable=True, source_available=True):
    current = {
        digest({"session": session, "message": issue}): issue for issue in sorted(set(issues))
    }
    before = previous.get("active", {})
    details = previous.get("details", {})
    active = dict(before)
    events = []
    for key, text in current.items():
        active[key] = text
        details[key] = dict(session=session, message=text)
        if key not in before:
            events.append(
                dict(id=key, session=session, message=text, state="open", at=now.isoformat())
            )
    for key, text in before.items():
        original = details.get(key, {}).get("session", previous.get("session", session))
        transient = text.startswith("Remote monitor cycle failed:") or text in (
            "Perps paper observation is stale or unavailable",
            "Perps strategy decision is stale or not ready",
            "AMAT portfolio reconciliation blocks order readiness",
        )
        if (
            (original == session and reliable) or (transient and source_available)
        ) and key not in current:
            active.pop(key, None)
            events.append(
                dict(id=key, session=original, message=text, state="resolved", at=now.isoformat())
            )
    ledger = store / "alerts.jsonl"
    if events:
        with ledger.open("a") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
    recent = [*previous.get("recent", []), *events][-100:]
    value = dict(
        active=active, recent=recent, observed_at=now.isoformat(), session=session, details=details
    )
    atomic(store / "alerts.json", value)
    return value


def prune(store, now, active_session=None):
    from detail_archive import archive_completed

    try:
        result = archive_completed(store, now, active_session=active_session)
    except (OSError, ValueError) as exc:
        # Detail retention failure cannot erase a successful engine observation.
        result = dict(status="deferred", error_type=type(exc).__name__)
    atomic(store / "archive-status.json", dict(observed_at=now.isoformat(), **result))
    return result


def observation_issues(publication, observation, now):
    """Pure alert derivation; unavailable signals never interrupt collection."""
    issues = list(publication["audit"].get("issues", []))
    portfolio = observation.get("amat", {}).get("portfolio")
    if portfolio:
        stamp = portfolio.get("observed_at")
        age = (now - dt.datetime.fromisoformat(stamp)).total_seconds() if stamp else None
        if portfolio.get("status") != "reconciled" or age is None or not 0 <= age <= 720:
            issues.append("AMAT portfolio reconciliation blocks order readiness")
    decision = observation.get("perps", {}).get("decision")
    if "decision" in observation.get("perps", {}) and (
        not isinstance(decision, dict)
        or decision.get("ready") is not True
        or not isinstance(decision.get("as_of_ms"), int)
        or not 0 <= now.timestamp() * 1000 - decision["as_of_ms"] <= 2 * 60 * 60 * 1000
    ):
        issues.append("Perps strategy decision is stale or not ready")
    if not publication["perps_fresh"]:
        issues.append("Perps paper observation is stale or unavailable")
    if observation.get("amat", {}).get("health", {}).get("healthy") is False:
        issues.append("AMAT daemon health check failed")
    return issues


def final_findings(folder, pointer, publication):
    """Review saved observations after close without exercising trading authority."""
    from detail_archive import detail_names, read_detail

    expected = (
        publication.get("diagnostics", {})
        .get("amat", {})
        .get("required_execution", {})
        .get("expected_plans", {})
        .get(pointer["session"])
    )
    count = errors = decision_count = nonzero = 0
    complete = True
    configs = set()
    plans = set()
    orders = set()
    fills = set()
    for name in detail_names(folder, "evidence"):
        r = json.loads(read_detail(folder, name))
        count += 1
        amat = r.get("amat", {})
        perps = r.get("perps", {})
        errors += bool(amat.get("error") or perps.get("error"))
        complete = complete and amat.get("orders_complete") is True and bool(amat.get("plan"))
        configs.add(amat.get("config_sha256", "unavailable"))
        plans.add((amat.get("plan") or {}).get("sha256", "unavailable"))
        orders.update(o["order_ref"] for o in amat.get("orders", []))
        decision = perps.get("decision")
        if isinstance(decision, dict):
            decision_count += 1
            nonzero += any(float(w) != 0 for w in decision.get("weights", {}).values())
        if perps.get("fills") is not None:
            fills.add(perps["fills"])
    configs = sorted(configs)
    plans = sorted(plans)
    complete = bool(count) and complete
    verified_hold = (
        bool(expected)
        and plans == [expected]
        and complete
        and not errors
        and not orders
        and publication["audit"].get("planned") == 0
        and publication["audit"]["status"] == "verified"
    )
    end = publication.get("performance_end")
    return dict(
        schema="paper-session-findings/1",
        source=pointer,
        through=publication["observed_at"],
        reviewed_at=publication["observed_at"],
        final=True,
        summary=(
            "Verified hold: all saved observations retained the pinned plan, with zero broker orders, missed intents, duplicates or overfills."
            if verified_hold
            else "Final review requires attention: see order audit, coverage and alert evidence below."
        ),
        perps=f"Saved minute observations: {decision_count} decision polls, {nonzero} polls requesting nonzero exposure. Cumulative fill counts observed: {sorted(set(fills))}. These are sampled observations, not an exhaustive signal-event audit.",
        limitations=(
            "Closing performance captured at " + end + ". "
            if end
            else "A valid closing performance observation is unavailable. "
        )
        + "Open-position profit since entry is separate from session equity change. AMAT fees and opening-VWAP slippage remain unavailable. No-trade sessions cannot establish actual-fill parity; source marks may differ in time and gaps between samples remain possible.",
        evidence_counts=dict(
            observations=count,
            source_errors=errors,
            complete_order_coverage=complete,
            unique_broker_orders=len(orders),
            config_hashes=configs,
            plan_digests=plans,
        ),
        bots_at_review=publication["bots"],
    )


def main():
    STORE.mkdir(parents=True, exist_ok=True)
    with (STORE / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = dt.datetime.now(dt.UTC)
        manifest = json.loads((HERE / "manifest.json").read_text())
        for name, expected in manifest["files"].items():
            if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != expected:
                raise ValueError("Monitor source attestation failed")
        selected = selected_session(now, calendar_for(now))
        session = selected["date"]
        env = dict(os.environ, AMAT_MONITOR_SESSION=session)
        p = subprocess.run(
            [sys.executable, str(HERE / "observer.py")],
            capture_output=True,
            text=True,
            timeout=80,
            env=env,
        )
        if p.returncode:
            raise RuntimeError("Read-only collector failed")
        observation = json.loads(p.stdout)
        requirements = STORE / "requirements.json"
        if requirements.is_file():
            required = json.loads(requirements.read_text())
            if session >= required["effective_session"]:
                observation.setdefault("amat", {})["required_execution"] = required
        observation.setdefault("amat", {}).setdefault("calendar", selected)
        folder = STORE / session
        folder.mkdir(exist_ok=True)
        old = (
            json.loads((folder / "state.json").read_text())
            if (folder / "state.json").exists()
            else None
        )
        publication, state = project(observation, now, old, session)
        issues = observation_issues(publication, observation, now)
        alerts = update_alerts(
            json.loads((STORE / "alerts.json").read_text())
            if (STORE / "alerts.json").exists()
            else {},
            issues,
            now,
            session,
            STORE,
            reliable=bool(observation.get("amat", {}).get("plan"))
            and observation.get("amat", {}).get("orders_complete", False)
            and not observation.get("amat", {}).get("error"),
            source_available=publication["perps_fresh"]
            and not observation.get("amat", {}).get("error"),
        )
        publication.update(
            collection_location="remote VPS",
            collection_interval_seconds=60,
            alerts=[
                dict(
                    message=v,
                    id=k,
                    session=alerts.get("details", {}).get(k, {}).get("session", session),
                )
                for k, v in alerts["active"].items()
            ],
            alert_events=alerts["recent"],
            collection_independent_of_desktop=True,
        )
        name = "observation-" + now.strftime("%Y%m%dT%H%M%S%f") + ".json"
        atomic(folder / name, publication)
        atomic(folder / "state.json", state)
        evidence = folder / "evidence"
        evidence.mkdir(exist_ok=True)
        atomic(evidence / name, observation)
        pointer = dict(file=name, sha256=digest(publication), session=session)
        atomic(folder / "latest.json", pointer)
        atomic(STORE / "latest.json", pointer)
        if publication["status"] == "complete":
            atomic(folder / "findings.json", final_findings(folder, pointer, publication))
            atomic(folder / "final.json", publication)
            audit = publication["audit"]
            lines = [
                "# Paper session " + session,
                "",
                "Verification: " + audit["status"],
                "Observed at: " + now.isoformat(),
                "",
                "Planned orders: " + str(audit.get("planned")),
                "Filled: " + str(audit.get("filled")),
                "Unfilled: " + str(audit.get("missed")),
                "Duplicate identities: " + str(audit.get("duplicates")),
                "Overfilled: " + str(audit.get("overfilled")),
                "",
            ]
            for bot in publication["bots"]:
                lines.extend(
                    [
                        bot["name"]
                        + ": equity="
                        + str(bot["equity"])
                        + ", session return="
                        + str(bot["net_return"])
                        + ", fees="
                        + str(bot["fees"])
                        + ", funding="
                        + str(bot["funding"])
                    ]
                )
            lines.extend(
                [
                    "",
                    "Unresolved issues:",
                    *[str(v) for v in alerts["active"].values()],
                    "",
                    *publication["limitations"],
                ]
            )
            temporary = folder / "final.md.tmp"
            temporary.write_text("\n".join(lines) + "\n")
            temporary.replace(folder / "final.md")
        atomic(
            STORE / "health.json",
            dict(
                status="running",
                last_success_at=now.isoformat(),
                session=session,
                active_alerts=len(alerts["active"]),
            ),
        )
        prune(STORE, now, active_session=session)
        print(
            json.dumps(
                dict(
                    status="observed",
                    session=session,
                    alerts=len(alerts["active"]),
                    audit=publication["audit"]["status"],
                )
            )
        )


if __name__ == "__main__":
    try:
        main()
    except BlockingIOError:
        print("Another monitor cycle owns the lock; skipped.")
    except Exception as exc:
        STORE.mkdir(parents=True, exist_ok=True)
        failed_at = dt.datetime.now(dt.UTC)
        atomic(
            STORE / "health.json",
            dict(status="failed", at=failed_at.isoformat(), error_type=type(exc).__name__),
        )
        previous = (
            json.loads((STORE / "alerts.json").read_text())
            if (STORE / "alerts.json").exists()
            else {}
        )
        update_alerts(
            previous,
            ["Remote monitor cycle failed: " + type(exc).__name__],
            failed_at,
            previous.get("session", "unknown"),
            STORE,
            reliable=False,
            source_available=False,
        )
        raise
