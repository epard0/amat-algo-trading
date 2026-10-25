import pytest
from test_shadow_forward import shadow, step

from kalshi_perps.trade_alerts import dispatch, open_outbox, scan


@pytest.mark.parametrize("direction,label", [(1, "LONG"), (-1, "SHORT")])
def test_real_entry_partial_profit_and_signed_funding(tmp_path, direction, label):
    r = shadow(tmp_path)
    out = open_outbox(tmp_path / "alerts.sqlite")
    dbpath = tmp_path / "paper.sqlite"
    assert scan(out, "shadow", dbpath, "SHADOW")["state"] == "armed"
    step(r, 100, ".98", direction, reason="entry")
    assert scan(out, "shadow", dbpath, "SHADOW")["new_alerts"] == 1
    text = out.execute("SELECT text FROM outbox").fetchone()[0]
    assert f"ENTRY: SOL {label}" in text and "Fill VWAP: $" in text
    assert "Funding — account total:" in text and "Net P&L — account:" in text
    assert scan(out, "shadow", dbpath, "SHADOW")["new_alerts"] == 0
    step(r, 175, ".98", direction)
    r.broker.apply_historical_funding(
        dict(event_id="native", ticker=r.symbols[0], timestamp_ms=150, rate=".001", mark="10")
    )
    step(r, 200, ".735", direction, reason="partial_tp")
    assert scan(out, "shadow", dbpath, "SHADOW")["new_alerts"] == 1
    text = out.execute("SELECT text FROM outbox ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    assert f"TAKE PROFIT: SOL {label}" in text and "Net P&L — this reduction:" in text
    assert "Funding allocated to reduction: $" + ("-" if direction > 0 else "+") in text
    sent = []
    dispatch(out, lambda message: sent.append(message) or ("sent", "telegram-id"), now=1)
    dispatch(out, lambda message: sent.append(message) or ("sent", "telegram-id"), now=10000)
    assert len(sent) == 2
    out.close()
    out = open_outbox(tmp_path / "alerts.sqlite")
    dispatch(out, lambda message: sent.append(message) or ("sent", "telegram-id"), now=20000)
    assert len(sent) == 2


def test_preexisting_fills_are_not_replayed(tmp_path):
    r = shadow(tmp_path)
    step(r, 100, ".98", reason="entry")
    out = open_outbox(tmp_path / "alerts.sqlite")
    scan(out, "default", tmp_path / "paper.sqlite", "DEFAULT")
    assert out.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    step(r, 200, "0", reason="take_profit")
    scan(out, "default", tmp_path / "paper.sqlite", "DEFAULT")
    assert out.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


def test_ambiguous_delivery_is_not_automatically_duplicated(tmp_path):
    db = open_outbox(tmp_path / "alerts.sqlite")
    with db:
        db.execute(
            "INSERT INTO outbox(account,event,text,status) VALUES('shadow','one','text','pending')"
        )
    calls = []

    def timeout(text):
        calls.append(text)
        raise TimeoutError("secret-bearing URL must not be recorded")

    dispatch(db, timeout, now=0)
    dispatch(db, timeout, now=10000)
    assert len(calls) == 1
    assert db.execute("SELECT status,receipt FROM outbox").fetchone() == (
        "uncertain",
        "TimeoutError",
    )
    with db:
        db.execute("UPDATE outbox SET status='sending'")
    db.close()
    db = open_outbox(tmp_path / "alerts.sqlite")
    assert db.execute("SELECT status FROM outbox").fetchone()[0] == "uncertain"


def test_explicit_rejection_retries_then_stops_after_acceptance(tmp_path):
    db = open_outbox(tmp_path / "alerts.sqlite")
    with db:
        db.execute(
            "INSERT INTO outbox(account,event,text,status) VALUES('default','one','text','pending')"
        )
    dispatch(db, lambda _: ("pending", "HTTP 429"), now=0)
    assert db.execute("SELECT attempts FROM outbox").fetchone()[0] == 1
    dispatch(db, lambda _: ("sent", "123"), now=10)
    assert db.execute("SELECT status FROM outbox").fetchone()[0] == "pending"
    dispatch(db, lambda _: ("sent", "123"), now=60)
    assert db.execute("SELECT status,attempts FROM outbox").fetchone() == ("sent", 2)


def test_retired_account_selection_preserves_pending_and_cursors_without_delivery(
    tmp_path, monkeypatch
):
    from kalshi_perps.trade_alerts import enabled_accounts

    monkeypatch.delenv("TRADE_ALERT_ACCOUNTS", raising=False)
    assert list(enabled_accounts()) == ["default"]
    with pytest.raises(ValueError):
        enabled_accounts("default,unknown")
    with pytest.raises(ValueError):
        enabled_accounts("default,default")
    db = open_outbox(tmp_path / "alerts.sqlite")
    with db:
        db.execute("INSERT INTO cursors VALUES('shadow',123)")
        for account in ("default", "shadow"):
            db.execute(
                "INSERT INTO outbox(account,event,text,status) VALUES(?,?,?,'pending')",
                (account, "one", account),
            )
    sent = []
    dispatch(
        db, lambda text: sent.append(text) or ("sent", "123"), now=0, accounts=enabled_accounts()
    )
    assert sent == ["default"]
    assert db.execute("SELECT sequence FROM cursors WHERE account='shadow'").fetchone() == (123,)
    assert db.execute("SELECT status FROM outbox WHERE account='shadow'").fetchone() == ("pending",)
    db.close()
    db = open_outbox(tmp_path / "alerts.sqlite")
    dispatch(
        db, lambda text: sent.append(text) or ("sent", "123"), now=1000, accounts=enabled_accounts()
    )
    assert sent == ["default"]
