"""Replay ordering and working memory checks; no provider or notification calls."""

import json
import sqlite3
import tracemalloc

from kalshi_perps.ledger import ordered_events


def database(path, count):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE events(id TEXT PRIMARY KEY,body TEXT,result TEXT)")
    for index in range(count):
        db.execute(
            "INSERT INTO events VALUES(?,?,?)",
            (
                f"order:{index}",
                json.dumps(dict(timestamp_ms=count - index, padding="x" * 2000)),
                "{}",
            ),
        )
    db.commit()
    return db


def test_sql_replay_preserves_late_funding_ties_and_liquidations(tmp_path):
    db = database(tmp_path / "ledger.sqlite", 3)
    for key, when, result in [
        ("funding:late", 2, {"payment": "-1"}),
        ("order:tie", 2, {}),
        ("snapshot:liquidation", 3, {"liquidations": [{"count": "-1"}]}),
        ("snapshot:ordinary", 1, {"liquidations": []}),
    ]:
        db.execute(
            "INSERT INTO events VALUES(?,?,?)",
            (key, json.dumps(dict(timestamp_ms=when)), json.dumps(result)),
        )
    expected = []
    for sequence, key, body, result in db.execute("SELECT rowid,id,body,result FROM events"):
        p = json.loads(body)
        if key.startswith("snapshot:") and not json.loads(result).get("liquidations"):
            continue
        expected.append((p["timestamp_ms"], 0 if key.startswith("funding:") else 1, sequence, key))
    actual = [
        (when, 0 if key.startswith("funding:") else 1, sequence, key)
        for when, sequence, key, _, _ in ordered_events(db, liquidations=True)
    ]
    assert actual == sorted(expected)
    assert not any(key.startswith("snapshot:") for _, _, key, _, _ in ordered_events(db))


def test_large_event_traversal_has_bounded_python_working_memory(tmp_path):
    peaks = []
    for count in (500, 6000):
        db = database(tmp_path / f"{count}.sqlite", count)
        tracemalloc.start()
        seen = 0
        for when, _, _, _, _ in ordered_events(db):
            seen += 1
            assert when == seen
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        db.close()
        peaks.append(peak)
    assert peaks[1] < 512_000
    assert peaks[1] < peaks[0] + 128_000


def test_metrics_and_alerts_match_original_replay_with_late_funding_after_close(
    tmp_path, monkeypatch
):
    from test_adx_forward import T, setup, step

    import kalshi_perps.ledger as ledger
    from kalshi_perps.adx_forward import account_metrics
    from kalshi_perps.comparison_metrics import metrics
    from kalshi_perps.trade_alerts import ledger_alerts

    runner = setup(tmp_path)
    step(runner, 100, ".98")
    step(runner, 200, ".735", reason="partial_tp")
    step(runner, 300, "0", reason="take_profit")
    runner.broker.apply_historical_funding(
        dict(event_id="late", ticker=T, timestamp_ms=150, rate=".001", mark="10")
    )
    store = runner.broker.store
    expected = (
        account_metrics(store, 0, 300),
        metrics(store, 0, 300),
        ledger_alerts(store.db, "DEFAULT"),
    )

    def original_replay(db, *, liquidations=False):
        events = []
        for sequence, key, body, result in db.execute("SELECT rowid,id,body,result FROM events"):
            if not key.startswith(("order:", "funding:", "snapshot:")):
                continue
            p = json.loads(body)
            r = json.loads(result)
            if key.startswith("snapshot:") and (not liquidations or not r.get("liquidations")):
                continue
            events.append(
                (p["timestamp_ms"], 0 if key.startswith("funding:") else 1, sequence, key, p, r)
            )
        for when, _, sequence, key, p, r in sorted(events):
            yield when, sequence, key, p, r

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "ordered_events", original_replay)
        assert (
            account_metrics(store, 0, 300),
            metrics(store, 0, 300),
            ledger_alerts(store.db, "DEFAULT"),
        ) == expected
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    store.close()
    store.db = sqlite3.connect(path)
    assert (
        account_metrics(store, 0, 300),
        metrics(store, 0, 300),
        ledger_alerts(store.db, "DEFAULT"),
    ) == expected
    store.close()
