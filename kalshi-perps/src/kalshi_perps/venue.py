"""Kalshi margin REST, explicit environments and no automatic write retries."""

import base64
import json
import os
import time
from pathlib import Path
from threading import Lock
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from .domain import ZERO, canonical, dec, digest
from .storage import Store

HOSTS = {
    "demo": "https://external-api.demo.kalshi.co",
    "production": "https://external-api.kalshi.com",
}
PREFIX = "/trade-api/v2/margin"


class VenueError(RuntimeError):
    def __init__(self, status, code):
        self.status = status
        super().__init__(f"Kalshi HTTP {status}: {code}")


class Transport:
    def __init__(
        self, environment, *, key_id=None, private_key=None, writable=False, client=None, clock=None
    ):
        if environment not in HOSTS:
            raise ValueError("unknown Kalshi environment")
        self.environment, self.host = environment, HOSTS[environment]
        self.key_id, self.private_key, self.writable = key_id, private_key, writable
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)
        self.clock = clock or (lambda: int(time.time() * 1000))
        self._last_request = 0.0
        self._pace_lock = Lock()

    @classmethod
    def configured(cls, config, *, writable=False):
        venue = config.raw["venue"]
        if writable and not config.raw.get("execution_enabled", False):
            raise ValueError("venue execution is disabled in this configuration")
        key_id = os.environ.get(venue["key_id_env"])
        key_path = os.environ.get(venue["private_key_env"])
        if writable and (not key_id or key_id != venue.get("expected_key_id")):
            raise ValueError("venue writes require the configured expected_key_id")
        private = (
            serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
            if key_path
            else None
        )
        if writable and private is None:
            raise ValueError("venue private key required")
        return cls(venue["environment"], key_id=key_id, private_key=private, writable=writable)

    def headers(self, method, path):
        if not self.key_id or self.private_key is None:
            return {}
        ts = str(self.clock())
        message = (ts + method + path).encode()
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            signed = self.private_key.sign(message)
        elif isinstance(self.private_key, rsa.RSAPrivateKey):
            signed = self.private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256(),
            )
        else:
            raise ValueError("RSA or Ed25519 key required")
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signed).decode(),
        }

    def request(self, method, endpoint, *, params=None, payload=None):
        if not endpoint.startswith("/") or ".." in endpoint or "?" in endpoint or ":" in endpoint:
            raise ValueError("invalid margin endpoint")
        if method != "GET":
            if not self.writable or not (endpoint == "/orders" or endpoint.startswith("/orders/")):
                raise ValueError("transport does not authorize this write")
            if method not in ("POST", "DELETE"):
                raise ValueError("unsupported write")
        path = PREFIX + endpoint
        # Pace calls and retry transient reads only. Every write has one attempt;
        # uncertain writes must be reconciled against their durable identity.
        for attempt in range(5 if method == "GET" else 1):
            with self._pace_lock:
                delay = 0.25 - (time.monotonic() - self._last_request)
                if delay > 0:
                    time.sleep(delay)
                self._last_request = time.monotonic()
            try:
                response = self.client.request(
                    method,
                    self.host + path,
                    params=params,
                    json=payload,
                    headers=self.headers(method, path),
                )
            except httpx.TransportError:
                if method != "GET" or attempt == 4:
                    raise
                time.sleep(2**attempt)
                continue
            if (
                method == "GET"
                and response.status_code in (429, 500, 502, 503, 504)
                and attempt < 4
            ):
                time.sleep(min(10, max(1, 2**attempt)))
                continue
            break
        if not 200 <= response.status_code < 300:
            try:
                code = response.json().get("code", "request_failed")
            except (ValueError, AttributeError):
                code = "invalid_response"
            raise VenueError(response.status_code, str(code)[:100])
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("expected JSON object")
        return result

    def pages(self, endpoint, field, **params):
        rows, seen = [], set()
        for _ in range(1000):
            body = self.request("GET", endpoint, params=params)
            rows.extend(body[field])
            cursor = body.get("cursor")
            if not cursor:
                return rows
            if cursor in seen:
                raise ValueError("pagination cursor cycle")
            seen.add(cursor)
            params["cursor"] = cursor
        raise ValueError("pagination exceeds safety bound")


class KalshiBroker:
    """Authoritative venue state with a durable intent/outcome journal.

    V1 admits at most 1x gross using full-notional collateral reservations; it does
    not infer venue initial margin from a leverage estimate. Portfolio offsets
    cannot increase this conservative cap.
    """

    def __init__(
        self,
        path,
        instruments,
        transport,
        *,
        mode,
        account,
        policy_hash,
        max_gross="1",
        max_order_notional="100",
        max_loss_fraction=".1",
        subaccount=0,
        expected_key_id=None,
    ):
        if mode not in ("demo", "live") or transport.environment != (
            "demo" if mode == "demo" else "production"
        ):
            raise ValueError("broker environment mismatch")
        if not transport.writable or not expected_key_id or transport.key_id != expected_key_id:
            raise ValueError("explicit account key binding required")
        self.mode, self.instruments, self.transport = mode, instruments, transport
        self.subaccount = subaccount
        self.max_gross, self.max_order = dec(max_gross), dec(max_order_notional)
        self.max_loss = dec(max_loss_fraction)
        if not ZERO < self.max_gross <= 1 or self.max_order <= 0 or not ZERO < self.max_loss < 1:
            raise ValueError("invalid venue risk limits")
        if type(subaccount) is not int or subaccount < 0:
            raise ValueError("invalid subaccount")
        self.store = Store(
            path,
            {
                "mode": mode,
                "account": account,
                "key_id": expected_key_id,
                "environment": transport.environment,
                "subaccount": subaccount,
                "policy": policy_hash,
                "instruments": {k: v.identity() for k, v in sorted(instruments.items())},
                "max_gross": str(self.max_gross),
                "max_order": str(self.max_order),
                "max_loss": str(self.max_loss),
            },
            {"halted": False, "baseline_equity": None, "venue": None},
        )

    def reconcile(self):
        tr = self.transport
        if tr.request("GET", "/enabled").get("enabled") is not True:
            raise ValueError("margin is not enabled")
        balance = tr.request("GET", "/balance", params={"compute_available_balance": "true"})
        balances = [b for b in balance["subaccount_balances"] if b["subaccount"] == self.subaccount]
        if len(balances) != 1:
            raise ValueError("account balance unavailable")
        b = balances[0]
        for k in (
            "account_equity",
            "initial_margin",
            "maintenance_margin",
            "available_balance",
            "resting_orders_margin",
        ):
            dec(b[k])
        positions = tr.request("GET", "/positions", params={"subaccount": self.subaccount})[
            "positions"
        ]
        orders = tr.pages("/orders", "orders", subaccount=self.subaccount)
        fills = tr.pages("/fills", "fills", subaccount=self.subaccount)
        risk = tr.request("GET", "/risk")
        index = {o["client_order_id"]: o for o in orders}
        with self.store.transaction():
            for client_id, payload in self.store.db.execute(
                "SELECT id,payload FROM orders WHERE status IN ('submission_pending','submission_unknown','acknowledged')"
            ).fetchall():
                matched = index.get(client_id)
                if matched:
                    original = json.loads(payload)
                    if (
                        matched["ticker"] != original["ticker"]
                        or matched["side"] != original["side"]
                        or dec(matched["price"]) != dec(original["price"])
                        or dec(matched["fill_count"]) > dec(original["count"])
                    ):
                        raise ValueError("venue order identity conflicts with journal")
                    status = "terminal" if dec(matched["remaining_count"]) == 0 else "acknowledged"
                    self.store.db.execute(
                        "UPDATE orders SET status=?,response=? WHERE id=?",
                        (status, canonical(matched), client_id),
                    )
            state = self.store.state()
            state["venue"] = {
                "balance": b,
                "positions": positions,
                "orders": orders,
                "fills": fills,
                "risk": risk,
                "timestamp_ms": tr.clock(),
            }
            if state["baseline_equity"] is None:
                state["baseline_equity"] = b["account_equity"]
            if dec(b["account_equity"]) <= dec(state["baseline_equity"]) * (1 - self.max_loss):
                state["halted"] = True
            self.store.save(state)
        return self.report()

    def submit(self, order, *, timestamp_ms, quote_max_age_ms=5000):
        order.validate(self.instruments[order.ticker])
        payload = dict(order.payload(), subaccount=self.subaccount)
        with self.store.transaction():
            old = self.store.db.execute(
                "SELECT hash,status,response FROM orders WHERE id=?", (order.client_order_id,)
            ).fetchone()
            if old:
                if old[0] != digest(payload):
                    raise ValueError("client order identity conflict")
                return {"status": old[1], "response": json.loads(old[2]) if old[2] else None}
            state = self.store.state()
            if state["halted"] and not order.reduce_only:
                raise ValueError("venue trading halted")
            v = state["venue"]
            now = self.transport.clock()
            if (
                v is None
                or not 0 <= now - v["timestamp_ms"] <= quote_max_age_ms
                or not 0 <= now - timestamp_ms <= quote_max_age_ms
            ):
                raise ValueError("fresh quote and reconciled account required")
            unresolved = self.store.db.execute(
                "SELECT COUNT(*) FROM orders WHERE status IN ('submission_pending','submission_unknown','acknowledged')"
            ).fetchone()[0]
            if unresolved or any(dec(o["remaining_count"]) > 0 for o in v["orders"]):
                raise ValueError("unresolved or resting orders block submission")
            current = ZERO
            gross = ZERO
            risk_positions = {
                (p["subaccount"], p["market_ticker"]): p for p in v["risk"]["positions"]
            }
            for p in v["positions"]:
                q = dec(p["position"])
                if p["market_ticker"] == order.ticker:
                    current = q
                if q:
                    r = risk_positions.get((self.subaccount, p["market_ticker"]))
                    if r is None:
                        raise ValueError("missing position risk")
                    gross += dec(r["position_notional"])
            signed = order.count * (1 if order.side == "bid" else -1)
            order_notional = order.count * order.price
            if order_notional > self.max_order:
                raise ValueError("maximum order notional exceeded")
            if order.reduce_only:
                if current * signed >= 0 or order.count > abs(current):
                    raise ValueError("reduce-only would increase or reverse position")
            else:
                b = v["balance"]
                if gross + order_notional > dec(b["account_equity"]) * self.max_gross:
                    raise ValueError("gross exposure limit exceeded")
                # Full-notional plus 1% fee/slippage buffer: deliberately more conservative than venue margin.
                if order_notional * dec("1.01") > dec(b["available_balance"]):
                    raise ValueError("available collateral insufficient")
            self.store.db.execute(
                "INSERT INTO orders VALUES(?,?,?,?,NULL)",
                (order.client_order_id, digest(payload), canonical(payload), "submission_pending"),
            )
        try:
            response = self.transport.request("POST", "/orders", payload=payload)
            # A response is recorded, then REST reconciliation determines terminal state.
            if (
                not response.get("order_id")
                or response.get("client_order_id") != order.client_order_id
            ):
                raise ValueError("invalid create-order response identity")
        except Exception as exc:
            deterministic = isinstance(exc, VenueError) and exc.status in (
                400,
                401,
                403,
                404,
                409,
                422,
                429,
            )
            with self.store.transaction():
                self.store.db.execute(
                    "UPDATE orders SET status=? WHERE id=?",
                    ("rejected" if deterministic else "submission_unknown", order.client_order_id),
                )
            raise
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE orders SET status='acknowledged',response=? WHERE id=?",
                (canonical(response), order.client_order_id),
            )
        return response

    def cancel_all(self):
        self.reconcile()
        results = []
        for order in self.store.state()["venue"]["orders"]:
            if dec(order["remaining_count"]) > 0:
                # Only cancel orders owned by this package's journal.
                owned = self.store.db.execute(
                    "SELECT 1 FROM orders WHERE id=?", (order["client_order_id"],)
                ).fetchone()
                if owned:
                    results.append(
                        self.transport.request(
                            "DELETE",
                            "/orders/" + quote(order["order_id"], safe=""),
                            params={"subaccount": self.subaccount},
                        )
                    )
        self.reconcile()
        return results

    def report(self):
        state = self.store.state()
        local = [
            {"client_order_id": r[0], "status": r[1]}
            for r in self.store.db.execute("SELECT id,status FROM orders")
        ]
        return {
            "mode": self.mode,
            "halted": state["halted"],
            "account": state["venue"],
            "orders": local,
            "risk_model": "at most 1x gross; full-notional collateral reservation",
        }
