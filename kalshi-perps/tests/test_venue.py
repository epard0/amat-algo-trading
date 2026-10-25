import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from kalshi_perps.domain import Order
from kalshi_perps.venue import KalshiBroker, Transport, VenueError

T = "KXBTCPERP"


def make(tmp_path, btc, handler, *, mode="demo"):
    key = ed25519.Ed25519PrivateKey.generate()
    tr = Transport(
        "demo",
        key_id="test-key",
        private_key=key,
        writable=True,
        clock=lambda: 10000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return KalshiBroker(
        tmp_path / "venue.sqlite",
        {T: btc},
        tr,
        mode=mode,
        account="test-account",
        policy_hash="policy",
        expected_key_id="test-key",
    )


def response(request):
    path = request.url.path
    if path.endswith("/enabled"):
        body = {"enabled": True}
    elif path.endswith("/balance"):
        assert request.url.params["compute_available_balance"] == "true"
        body = {
            "subaccount_balances": [
                dict(
                    subaccount=0,
                    account_equity="10000",
                    maintenance_margin="0",
                    initial_margin="0",
                    resting_orders_margin="0",
                    available_balance="10000",
                )
            ]
        }
    elif path.endswith("/positions") or path.endswith("/risk"):
        body = {"positions": []}
    elif path.endswith("/orders") and request.method == "GET":
        body = {"orders": [], "cursor": ""}
    elif path.endswith("/fills"):
        body = {"fills": [], "cursor": ""}
    else:
        raise AssertionError(f"unexpected request {request.method} {path}")
    return httpx.Response(200, json=body)


def test_readonly_transport_cannot_write():
    tr = Transport("production")
    with pytest.raises(ValueError, match="authorize"):
        tr.request("POST", "/orders", payload={})


def test_signed_path_excludes_query():
    key = ed25519.Ed25519PrivateKey.generate()

    def handler(request):
        assert request.url.host == "external-api.demo.kalshi.co"
        key.public_key().verify(
            base64.b64decode(request.headers["KALSHI-ACCESS-SIGNATURE"]),
            b"10000GET/trade-api/v2/margin/orders",
        )
        return httpx.Response(200, json={"orders": [], "cursor": ""})

    tr = Transport(
        "demo",
        key_id="key",
        private_key=key,
        clock=lambda: 10000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    tr.request("GET", "/orders", params={"cursor": "abc"})


def test_live_cannot_use_demo(tmp_path, btc):
    with pytest.raises(ValueError, match="environment"):
        make(tmp_path, btc, response, mode="live")


def test_unknown_ack_persists_no_resubmission(tmp_path, btc):
    writes = []

    def handler(request):
        if request.method == "POST":
            writes.append(json.loads(request.content))
            raise httpx.ReadTimeout("dropped ack", request=request)
        return response(request)

    b = make(tmp_path, btc, handler)
    b.reconcile()
    order = Order("unique", T, "bid", 10, "8.01", 1)
    with pytest.raises(httpx.ReadTimeout):
        b.submit(order, timestamp_ms=10000)
    assert b.submit(order, timestamp_ms=10000)["status"] == "submission_unknown"
    b.reconcile()
    with pytest.raises(ValueError, match="unresolved"):
        b.submit(Order("replacement", T, "bid", 10, "8.01", 1), timestamp_ms=10000)
    b.store.close()
    b = make(tmp_path, btc, handler)
    assert b.submit(order, timestamp_ms=10000)["status"] == "submission_unknown"
    assert len(writes) == 1


def test_confirmed_order_reconciles_by_identity(tmp_path, btc):
    accepted = []

    def handler(request):
        if request.method == "POST":
            p = json.loads(request.content)
            accepted.append(
                dict(
                    order_id="venue-1",
                    client_order_id=p["client_order_id"],
                    ticker=p["ticker"],
                    side=p["side"],
                    price=p["price"],
                    fill_count=p["count"],
                    remaining_count="0",
                )
            )
            return httpx.Response(201, json=accepted[-1])
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json={"orders": accepted, "cursor": ""})
        return response(request)

    b = make(tmp_path, btc, handler)
    b.reconcile()
    b.submit(Order("new", T, "bid", 10, "8.01", 1), timestamp_ms=10000)
    b.reconcile()
    assert b.report()["orders"] == [{"client_order_id": "new", "status": "terminal"}]


def test_pagination_cycles_fail():
    tr = Transport(
        "demo",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"orders": [], "cursor": "same"})
            )
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        tr.pages("/orders", "orders")


def test_deterministic_rejection_not_retried(tmp_path, btc):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(409, json={"code": "market_version_mismatch"})
        return response(request)

    b = make(tmp_path, btc, handler)
    b.reconcile()
    order = Order("new", T, "bid", 10, "8.01", 1)
    with pytest.raises(VenueError):
        b.submit(order, timestamp_ms=10000)
    assert b.submit(order, timestamp_ms=10000)["status"] == "rejected"


def test_stale_quote_and_order_cap(tmp_path, btc):
    b = make(tmp_path, btc, response)
    b.reconcile()
    with pytest.raises(ValueError, match="fresh"):
        b.submit(Order("old", T, "bid", 1, "8.01", 1), timestamp_ms=0)
    with pytest.raises(ValueError, match="maximum order"):
        b.submit(Order("large", T, "bid", 1000, "8.01", 1), timestamp_ms=10000)


def test_transient_read_retries_but_writes_do_not(monkeypatch):
    monkeypatch.setattr("kalshi_perps.venue.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": True})

    tr = Transport(
        "demo", writable=True, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert tr.request("GET", "/markets")["ok"]
    assert calls == ["GET", "GET"]
    calls.clear()
    with pytest.raises(VenueError):
        tr.request("POST", "/orders", payload={})
    assert calls == ["POST"]
