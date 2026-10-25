"""Read-only paper-ledger watcher with a durable Telegram outbox."""

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .domain import ZERO, dec
from .operations import NoRedirect
from .paper import equity

ACCOUNTS = {
    "default": ("/accounts/default/paper-doge-sol-adx14.sqlite", "DEFAULT · max leverage"),
    "shadow": ("/accounts/shadow/paper-doge-sol-trend-shadow-v2.sqlite", "SHADOW · 1x"),
}
SIZES = {"KXDOGEPERP": dec("100"), "KXSOLPERP": dec(".1")}


def open_outbox(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS cursors (account TEXT PRIMARY KEY, sequence INTEGER)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS outbox (account TEXT,event TEXT,text TEXT,status TEXT,attempts INTEGER DEFAULT 0,next_try REAL DEFAULT 0,receipt TEXT,PRIMARY KEY(account,event))"
    )
    # A crash after dispatch has an ambiguous delivery outcome. Do not duplicate it.
    db.execute("UPDATE outbox SET status='uncertain' WHERE status='sending'")
    db.commit()
    return db


def ledger_alerts(db, label, after=0):
    """Reconstruct both directions and allocate funding/entry fees to reductions."""
    from .ledger import ordered_events

    state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
    maximum = db.execute("SELECT coalesce(max(rowid),0) FROM events").fetchone()[0]
    positions = {}
    fees = {}
    funding = {}
    alerts = []
    for when, sequence, key, p, r in ordered_events(db, liquidations=True):
        if key.startswith("funding:"):
            t = p["ticker"]
            funding[t] = funding.get(t, ZERO) + dec(r["payment"])
            continue
        fills = r.get("fills", []) if key.startswith("order:") else r.get("liquidations", [])
        amounts = {}
        reason = p.get("reason", "liquidation")
        reduction_net = ZERO
        allocated_funding = ZERO
        for f in fills:
            t = f["ticker"]
            old = positions.get(t, ZERO)
            delta = dec(f["count"])
            fee = dec(f["fee"])
            if not old or old * delta > 0:
                if not old:
                    fees[t] = ZERO
                    funding[t] = ZERO
                fees[t] = fees.get(t, ZERO) + fee
                kind = "ENTRY" if reason == "entry" else None
                side = "LONG" if delta > 0 else "SHORT"
            else:
                if abs(delta) > abs(old):
                    raise ValueError("fill crossing zero requires explicit split")
                ratio = abs(delta) / abs(old)
                ef = fees.get(t, ZERO) * ratio
                ff = funding.get(t, ZERO) * ratio
                fees[t] = fees.get(t, ZERO) - ef
                funding[t] = funding.get(t, ZERO) - ff
                reduction_net += dec(f["realized_pnl"]) - fee - ef + ff
                allocated_funding += ff
                kind = "TAKE PROFIT" if reason in ("partial_tp", "take_profit") else None
                side = "LONG" if old > 0 else "SHORT"
            positions[t] = old + delta
            if kind and t in SIZES:
                a = amounts.setdefault((t, kind, side), dict(quantity=ZERO, value=ZERO))
                a["quantity"] += abs(delta)
                a["value"] += abs(delta) * dec(f["price"])
        if sequence <= after:
            continue
        for (t, kind, side), a in amounts.items():
            quantity = a["quantity"]
            price = a["value"] / quantity / SIZES[t]
            asset = t.removeprefix("KX").removesuffix("PERP")
            cash = equity(state)
            account_net = cash - dec(state["initial"])
            message = (
                f"[KALSHI PERPS PAPER] {label}\n{kind}: {asset} {side}"
                f"{' · partial take-profit' if reason == 'partial_tp' else ''}\n"
                f"Fill VWAP: ${price:,.8f} per {asset}\n"
                f"Filled: {quantity} contracts ({quantity * SIZES[t]} {asset})\n"
                f"Funding — account total: ${dec(state['funding']):+,.4f}\n"
                f"Net P&L — account: ${account_net:+,.2f} (realized + unrealized, after fees/funding)\n"
            )
            if kind == "TAKE PROFIT":
                message += f"Net P&L — this reduction: ${reduction_net:+,.4f}\nFunding allocated to reduction: ${allocated_funding:+,.4f}\n"
            message += f"Fill time: {datetime.fromtimestamp(when / 1000, UTC).isoformat()}\nAccount marks as of: {datetime.fromtimestamp(state['timestamp_ms'] / 1000, UTC).isoformat()}\nFill reference: {key.removeprefix('order:')}\nSimulated fills; paper account."
            alerts.append(dict(sequence=sequence, event=key, text=message))
    return maximum, alerts, state["timestamp_ms"]


def scan(outbox, account, path, label):
    source = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True, timeout=5)
    try:
        cursor = outbox.execute(
            "SELECT sequence FROM cursors WHERE account=?", (account,)
        ).fetchone()
        source.execute("BEGIN")
        maximum, alerts, source_ms = ledger_alerts(
            source, label, after=cursor[0] if cursor else 2**63 - 1
        )
    finally:
        source.close()
    cursor = outbox.execute("SELECT sequence FROM cursors WHERE account=?", (account,)).fetchone()
    with outbox:
        if cursor is None:
            # Arm for future committed fills, without replaying old account history.
            outbox.execute("INSERT INTO cursors VALUES(?,?)", (account, maximum))
            return dict(state="armed", new_alerts=0, source_ms=source_ms)
        eligible = [a for a in alerts if a["sequence"] > cursor[0]]
        for a in eligible:
            outbox.execute(
                "INSERT OR IGNORE INTO outbox(account,event,text,status) VALUES(?,?,?,'pending')",
                (account, a["event"], a["text"]),
            )
        outbox.execute("UPDATE cursors SET sequence=? WHERE account=?", (maximum, account))
    return dict(state="watching", new_alerts=len(eligible), source_ms=source_ms)


def send_message(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat = os.environ["TELEGRAM_CHAT_ID"].strip()
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=json.dumps(dict(chat_id=chat, text=text)).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=8) as response:
            payload = json.loads(response.read(65536))
        if payload.get("ok") is True and payload.get("result", {}).get("message_id"):
            return "sent", str(payload["result"]["message_id"])
        return "pending", "Telegram rejected request"
    except urllib.error.HTTPError as exc:
        return (
            "pending" if exc.code in (429, 500, 502, 503, 504) else "failed"
        ), f"HTTP {exc.code}"
    except Exception as exc:
        return "uncertain", type(exc).__name__  # Never log a token-bearing URL.


def dispatch(db, send=send_message, now=None, accounts=None):
    now = time.time() if now is None else now
    selected = tuple(ACCOUNTS if accounts is None else accounts)
    if not selected:
        return
    rows = db.execute(
        "SELECT account,event,text,attempts FROM outbox WHERE status='pending' AND next_try<=? AND account IN ("
        + ",".join("?" for _ in selected)
        + ") ORDER BY rowid LIMIT 20",
        (now, *selected),
    ).fetchall()
    for account, event, text, attempts in rows:
        with db:
            db.execute(
                "UPDATE outbox SET status='sending',attempts=attempts+1 WHERE account=? AND event=?",
                (account, event),
            )
        try:
            status, receipt = send(text)
        except Exception as exc:
            status, receipt = "uncertain", type(exc).__name__
        with db:
            db.execute(
                "UPDATE outbox SET status=?,receipt=?,next_try=? WHERE account=? AND event=?",
                (status, receipt, now + min(3600, 60 * 2 ** min(attempts, 6)), account, event),
            )


def enabled_accounts(value=None):
    """Explicit allowlist; retired accounts keep their cursors/outbox untouched."""
    value = os.getenv("TRADE_ALERT_ACCOUNTS", "default") if value is None else value
    names = value.split(",")
    if not names or any(name not in ACCOUNTS for name in names) or len(set(names)) != len(names):
        raise ValueError("TRADE_ALERT_ACCOUNTS must list distinct registered accounts")
    return {name: ACCOUNTS[name] for name in names}


def main():
    root = Path("/var/lib/trade-alerts")
    root.mkdir(exist_ok=True)
    if not all(os.environ.get(k, "").strip() for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")):
        raise SystemExit("Telegram configuration missing")
    selected = enabled_accounts()
    db = open_outbox(root / "outbox.sqlite")
    while True:
        accounts = {}
        for account, (path, label) in selected.items():
            try:
                accounts[account] = scan(db, account, path, label)
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
                accounts[account] = dict(state="blocked", error=type(exc).__name__)
        dispatch(db, accounts=selected)
        report = dict(
            updated_at=datetime.now(UTC).isoformat(),
            accounts=accounts,
            delivery_counts=dict(
                db.execute(
                    "SELECT status,count(*) FROM outbox WHERE account IN ("
                    + ",".join("?" for _ in selected)
                    + ") GROUP BY status",
                    tuple(selected),
                ).fetchall()
            ),
        )
        target = root / "status.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2))
        tmp.replace(target)
        print(json.dumps(report), flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
