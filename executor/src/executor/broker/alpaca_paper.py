"""Alpaca **paper** trading client: the account, the calendar, the submission.

This is the module the strategy repository refuses to contain. Everything that
can change remote state lives here and nowhere else, so "what can this system
send to a broker?" is answered by reading one file.

Design notes that are not stylistic
-----------------------------------
* **The host is a constant.** Every request is asserted against
  :data:`~executor.config.PAPER_TRADING_HOST` immediately before it is sent.
  A typo, a refactor, or a merge cannot redirect this client at the live
  endpoint without deleting that assertion in the diff.
* **Retries are verb-aware.** ``GET`` is idempotent and is retried on transient
  failures. ``POST /v2/orders`` is *not* retried on a timeout: a request that
  timed out may still have been accepted, and a blind retry is how one intent
  becomes two positions. It fails, and the caller reports it.
* **Client order IDs are deterministic.** Each intent carries an ID derived
  from the plan's decision session and the symbol, so resubmitting the same
  plan twice is rejected by Alpaca as a duplicate rather than doubling a
  position. That is the executor's idempotency key.
* **Decimals, not floats.** Money and share counts are parsed as
  :class:`~decimal.Decimal` from the wire strings Alpaca sends.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from executor.config import PAPER_TRADING_HOST
from executor.errors import BrokerError, ConfigurationError

__all__ = [
    "Account",
    "AlpacaPaperClient",
    "CalendarSession",
    "Clock",
    "Order",
    "Position",
]

_MAX_GET_ATTEMPTS = 4
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_REQUESTS_PER_MINUTE = 180

#: Width of the throttle's sliding window.
_THROTTLE_WINDOW_SECONDS = 60.0


def _decimal(payload: dict[str, Any], key: str, *, default: str | None = None) -> Decimal:
    """Parse a wire string into a Decimal, failing closed on anything else."""
    raw = payload.get(key)
    if raw is None:
        if default is None:
            message = f"broker response is missing required numeric field {key!r}"
            raise BrokerError(message)
        raw = default
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        message = f"broker sent a non-numeric {key!r}: {raw!r}"
        raise BrokerError(message) from exc
    if not value.is_finite():
        message = f"broker sent a non-finite {key!r}: {raw!r}"
        raise BrokerError(message)
    return value


@dataclass(frozen=True, slots=True)
class Account:
    """The paper account as the broker sees it."""

    account_number: str
    status: str
    equity: Decimal
    last_equity: Decimal
    cash: Decimal
    buying_power: Decimal
    trading_blocked: bool
    transfers_blocked: bool
    account_blocked: bool
    pattern_day_trader: bool

    @property
    def is_tradable(self) -> bool:
        """Whether the broker will accept orders at all right now."""
        return self.status == "ACTIVE" and not self.trading_blocked and not self.account_blocked

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Account:
        return cls(
            account_number=str(payload.get("account_number", "")),
            status=str(payload.get("status", "")),
            equity=_decimal(payload, "equity"),
            last_equity=_decimal(payload, "last_equity", default="0"),
            cash=_decimal(payload, "cash"),
            buying_power=_decimal(payload, "buying_power", default="0"),
            trading_blocked=bool(payload.get("trading_blocked", False)),
            transfers_blocked=bool(payload.get("transfers_blocked", False)),
            account_blocked=bool(payload.get("account_blocked", False)),
            pattern_day_trader=bool(payload.get("pattern_day_trader", False)),
        )


@dataclass(frozen=True, slots=True)
class Position:
    """One open position."""

    symbol: str
    quantity: Decimal
    market_value: Decimal
    cost_basis: Decimal
    unrealized_pl: Decimal
    current_price: Decimal
    avg_entry_price: Decimal

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Position:
        return cls(
            symbol=str(payload["symbol"]).upper(),
            quantity=_decimal(payload, "qty"),
            market_value=_decimal(payload, "market_value", default="0"),
            cost_basis=_decimal(payload, "cost_basis", default="0"),
            unrealized_pl=_decimal(payload, "unrealized_pl", default="0"),
            current_price=_decimal(payload, "current_price", default="0"),
            avg_entry_price=_decimal(payload, "avg_entry_price", default="0"),
        )


@dataclass(frozen=True, slots=True)
class Order:
    """An order as returned by the broker."""

    id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    filled_quantity: Decimal
    filled_avg_price: Decimal | None
    status: str
    submitted_at: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """Whether this order will not change again without intervention."""
        return self.status in {
            "filled",
            "canceled",
            "expired",
            "rejected",
            "replaced",
        }

    @property
    def notional_filled(self) -> Decimal:
        if self.filled_avg_price is None:
            return Decimal("0")
        return self.filled_quantity * self.filled_avg_price

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Order:
        filled_avg = payload.get("filled_avg_price")
        return cls(
            id=str(payload.get("id", "")),
            client_order_id=str(payload.get("client_order_id", "")),
            symbol=str(payload.get("symbol", "")).upper(),
            side=str(payload.get("side", "")),
            quantity=_decimal(payload, "qty", default="0"),
            filled_quantity=_decimal(payload, "filled_qty", default="0"),
            filled_avg_price=(
                None if filled_avg in (None, "") else _decimal(payload, "filled_avg_price")
            ),
            status=str(payload.get("status", "")),
            submitted_at=str(payload.get("submitted_at", "")),
            raw=payload,
        )


@dataclass(frozen=True, slots=True)
class Clock:
    """The exchange clock, as the broker reports it."""

    timestamp: dt.datetime
    is_open: bool
    next_open: dt.datetime
    next_close: dt.datetime

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Clock:
        return cls(
            timestamp=_parse_instant(payload["timestamp"]),
            is_open=bool(payload["is_open"]),
            next_open=_parse_instant(payload["next_open"]),
            next_close=_parse_instant(payload["next_close"]),
        )


@dataclass(frozen=True, slots=True)
class CalendarSession:
    """One trading session: the authoritative answer to "is today a trading
    day, and when does it open?"

    Holidays are simply absent from the calendar, and half-days carry an early
    ``close``. Deriving either from a weekday check is how an executor ends up
    awake and confused on Thanksgiving.
    """

    date: dt.date
    open_local: dt.time
    close_local: dt.time

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CalendarSession:
        return cls(
            date=dt.date.fromisoformat(str(payload["date"])),
            open_local=dt.time.fromisoformat(str(payload["open"])),
            close_local=dt.time.fromisoformat(str(payload["close"])),
        )


def _parse_instant(raw: str) -> dt.datetime:
    """Parse an Alpaca RFC-3339 instant into an aware datetime."""
    text = str(raw)
    # Alpaca sends nanoseconds on some fields; `fromisoformat` accepts at most
    # microseconds, so the fraction is truncated rather than rejected.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        rest = ""
        for index, character in enumerate(tail):
            if character.isdigit():
                digits += character
            else:
                rest = tail[index:]
                break
        text = f"{head}.{digits[:6]}{rest}"
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        message = f"broker sent an unparseable timestamp {raw!r}"
        raise BrokerError(message) from exc
    if parsed.tzinfo is None:
        message = f"broker sent a naive timestamp {raw!r}; refusing to guess its zone"
        raise BrokerError(message)
    return parsed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward trading credentials or orders to a redirected endpoint."""

    def redirect_request(
        self, _req: Any, _fp: Any, _code: Any, _msg: Any, _headers: Any, _newurl: Any
    ) -> None:
        return None


class AlpacaPaperClient:
    """Everything this system can say to a broker.

    Args:
        key_id: Paper trading key.
        secret_key: Paper trading secret.
        timeout_seconds: Per-request socket timeout.
    """

    _host = PAPER_TRADING_HOST

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not key_id or not secret_key:
            message = "paper trading credentials are empty"
            raise ConfigurationError(message)
        self._key_id = key_id
        self._secret_key = secret_key
        self._timeout = timeout_seconds
        self._request_times: list[float] = []

    # -- transport ----------------------------------------------------------

    def _throttle(self) -> None:
        now = time.monotonic()
        self._request_times = [
            stamp for stamp in self._request_times if now - stamp < _THROTTLE_WINDOW_SECONDS
        ]
        if len(self._request_times) >= _REQUESTS_PER_MINUTE:
            time.sleep(max(0.0, _THROTTLE_WINDOW_SECONDS - (now - self._request_times[0])) + 0.05)
            self._throttle()
            return
        self._request_times.append(now)

    def _build_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> urllib.request.Request:
        if not path.startswith("/v2/"):
            message = f"refusing a request outside the /v2 trading surface: {path!r}"
            raise ConfigurationError(message)

        url = f"https://{self._host}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        # The assertion that makes "paper only" a property of the code rather
        # than a promise in a README. Removing it is a visible diff.
        if urllib.parse.urlsplit(url).hostname != self._host:
            message = f"refusing to contact a host other than {self._host}"
            raise ConfigurationError(message)

        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(  # noqa: S310 - scheme and host asserted above
            url, data=payload, headers=headers, method=method
        )

    def _read(self, request: urllib.request.Request) -> Any:
        with urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=self._timeout
        ) as response:
            raw = response.read()
        if not raw:
            return None
        return json.loads(raw)

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """Issue an idempotent GET, retrying transient failures."""
        request = self._build_request("GET", path, params=params)
        last_error = ""
        for attempt in range(_MAX_GET_ATTEMPTS):
            self._throttle()
            try:
                return self._read(request)
            except urllib.error.HTTPError as exc:
                detail = _error_detail(exc)
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code not in _RETRY_STATUSES:
                    break
            except (http.client.HTTPException, OSError, TimeoutError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(min(2.0**attempt, 15.0))
        message = f"GET {path} failed after {_MAX_GET_ATTEMPTS} attempts: {last_error}"
        raise BrokerError(message)

    def _post_once(self, path: str, body: dict[str, Any]) -> Any:
        """Issue a state-changing POST exactly once.

        Not retried, by design. See the module docstring: a timed-out order
        submission may already have been accepted.
        """
        request = self._build_request("POST", path, body=body)
        self._throttle()
        try:
            return self._read(request)
        except urllib.error.HTTPError as exc:
            from executor.errors import OrderOutcomeUnknownError  # noqa: PLC0415

            detail = _error_detail(exc)
            message = f"POST {path}: HTTP {exc.code}: {detail}"
            if exc.code >= 500 or exc.code == 408 or "duplicate" in detail.lower():  # noqa: PLR2004
                raise OrderOutcomeUnknownError(message) from exc
            raise BrokerError(message) from exc
        except (http.client.HTTPException, OSError, TimeoutError, ValueError) as exc:
            message = (
                f"POST {path} failed in transport ({type(exc).__name__}: {exc}). "
                f"The request may or may not have reached the broker; it is NOT "
                f"retried. Reconcile against open orders before acting."
            )
            from executor.errors import OrderOutcomeUnknownError  # noqa: PLC0415

            raise OrderOutcomeUnknownError(message) from exc

    # -- account ------------------------------------------------------------

    def get_account(self) -> Account:
        """Fetch the paper account."""
        payload = self._get("/v2/account")
        if not isinstance(payload, dict):
            message = "GET /v2/account did not return an object"
            raise BrokerError(message)
        return Account.from_payload(payload)

    def list_positions(self) -> tuple[Position, ...]:
        """Every open position, as the broker holds it."""
        payload = self._get("/v2/positions")
        if not isinstance(payload, list):
            message = "GET /v2/positions did not return a list"
            raise BrokerError(message)
        return tuple(Position.from_payload(item) for item in payload)

    def position_quantities(self) -> dict[str, Decimal]:
        """Symbol to share count, the shape ``amat plan --positions`` expects."""
        return {position.symbol: position.quantity for position in self.list_positions()}

    def list_activities(
        self, *, after: dt.datetime, max_pages: int = 100, until: dt.datetime | None = None
    ) -> tuple[dict[str, Any], ...]:
        """Read all activity types, with bounded, checked pagination.

        An exhausted bound or repeating page is an error, never a partial ledger.
        ``after`` filters creation time, including later-posted fees/dividends.
        """
        if after.tzinfo is None or max_pages < 1:
            raise ConfigurationError("activities require an aware start and positive page bound")
        params = {
            "after": after.astimezone(dt.UTC).isoformat(),
            "direction": "asc",
            "page_size": "100",
        }
        if until is not None:
            if until.tzinfo is None or until <= after:
                raise ConfigurationError("invalid activity end")
            params["until"] = until.astimezone(dt.UTC).isoformat()
        result: dict[str, dict[str, Any]] = {}
        for _ in range(max_pages):
            page = self._get("/v2/account/activities", params)
            if not isinstance(page, list):
                raise BrokerError("activities response is not a list")
            for row in page:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                    raise BrokerError("activity is missing its identity")
                if row["id"] in result:
                    raise BrokerError("activity pagination repeated an identity")
                result[row["id"]] = row
            if len(page) < 100:  # noqa: PLR2004 - documented maximum page size
                return tuple(result.values())
            params["page_token"] = page[-1]["id"]
        raise BrokerError("activity pagination exceeded its bound; history is incomplete")

    def activity_history(
        self, *, after: dt.datetime, until: dt.datetime, max_pages: int = 100
    ) -> tuple[dict[str, Any], ...]:
        """Split exhausted windows with overlap; reject conflicts and bounded exhaustion."""
        pending = [(after, until)]
        result: dict[str, dict[str, Any]] = {}
        partitions = 0
        while pending:
            start, end = pending.pop()
            partitions += 1
            if partitions > 127:  # noqa: PLR2004 - maximum partition requests
                raise BrokerError("activity partition bound exhausted")
            try:
                rows = self.list_activities(after=start, until=end, max_pages=max_pages)
            except BrokerError as exc:
                if "pagination exceeded its bound" not in str(exc):
                    raise
                if (end - start).total_seconds() <= 4:  # noqa: PLR2004 - overlap floor
                    raise BrokerError("dense activity window exceeds bounded retrieval") from exc
                middle = start + (end - start) / 2
                pending.extend(
                    [
                        (start, middle + dt.timedelta(seconds=1)),
                        (middle - dt.timedelta(seconds=1), end),
                    ]
                )
                continue
            for row in rows:
                identity = row["id"]
                if identity in result and result[identity] != row:
                    raise BrokerError("activity changed across overlapping partitions")
                result[identity] = row
        return tuple(result.values())

    # -- reference data -----------------------------------------------------

    def get_asset(self, symbol: str) -> dict[str, Any]:
        """Asset metadata, including ``tradable`` and ``fractionable``."""
        payload = self._get(f"/v2/assets/{urllib.parse.quote(symbol.upper())}")
        if not isinstance(payload, dict):
            message = f"GET /v2/assets/{symbol} did not return an object"
            raise BrokerError(message)
        return payload

    def fractionable_symbols(self, symbols: tuple[str, ...]) -> dict[str, bool]:
        """Which of ``symbols`` the broker will accept fractional orders for.

        A symbol that is not tradable at all maps to ``False`` here and is
        caught separately by the caller's tradability check -- this method
        answers only the rounding question.
        """
        result: dict[str, bool] = {}
        for symbol in symbols:
            asset = self.get_asset(symbol)
            result[symbol.upper()] = bool(asset.get("fractionable", False)) and bool(
                asset.get("tradable", False)
            )
        return result

    # -- calendar -----------------------------------------------------------

    def get_clock(self) -> Clock:
        """The exchange clock. The authority on "is the market open now?"."""
        payload = self._get("/v2/clock")
        if not isinstance(payload, dict):
            message = "GET /v2/clock did not return an object"
            raise BrokerError(message)
        return Clock.from_payload(payload)

    def get_calendar(self, start: dt.date, end: dt.date) -> tuple[CalendarSession, ...]:
        """Trading sessions in ``[start, end]``. Holidays are simply absent."""
        payload = self._get("/v2/calendar", {"start": start.isoformat(), "end": end.isoformat()})
        if not isinstance(payload, list):
            message = "GET /v2/calendar did not return a list"
            raise BrokerError(message)
        return tuple(CalendarSession.from_payload(item) for item in payload)

    # -- orders -------------------------------------------------------------

    def submit_market_order(
        self, *, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> Order:
        """Submit one market DAY order, without transport retries."""
        return self._submit_order(
            symbol=symbol, side=side, quantity=quantity, client_order_id=client_order_id
        )

    def submit_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_order_id: str,
        limit_price: Decimal,
    ) -> Order:
        """Submit a price-bounded DAY order; never substitute a market order."""
        return self._submit_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            client_order_id=client_order_id,
            limit_price=limit_price,
        )

    def _submit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_order_id: str,
        limit_price: Decimal | None = None,
    ) -> Order:
        """Submit one market DAY order.

        ``day`` is not a preference. Alpaca supports fractional quantities only
        with ``time_in_force=day``, and this strategy's contract executes in the
        session's opening window -- an order that survives the session would be
        a different instruction than the one the plan formed.

        Args:
            symbol: Instrument.
            side: ``buy`` or ``sell``.
            quantity: Positive share count.
            client_order_id: Deterministic idempotency key.

        Raises:
            BrokerError: on rejection, or on a transport failure whose outcome
                is unknown.
        """
        if side not in {"buy", "sell"}:
            message = f"refusing an order with side {side!r}"
            raise ConfigurationError(message)
        if not quantity.is_finite() or quantity <= 0:
            message = f"refusing a non-positive order quantity for {symbol}: {quantity}"
            raise ConfigurationError(message)

        body = {
            "symbol": symbol.upper(),
            "qty": format(quantity.normalize(), "f"),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }
        if limit_price is not None:
            if not limit_price.is_finite() or limit_price <= 0:
                raise ConfigurationError("limit price must be positive and finite")
            body.update(type="limit", limit_price=format(limit_price, "f"))
        payload = self._post_once("/v2/orders", body)
        from executor.errors import OrderOutcomeUnknownError  # noqa: PLC0415

        try:
            if not isinstance(payload, dict):
                raise ValueError("POST /v2/orders did not return an object")
            order = Order.from_payload(payload)
            if (
                not order.id
                or not order.status
                or order.client_order_id != client_order_id
                or order.symbol != symbol.upper()
                or order.side != side
                or order.quantity != quantity
                or (
                    limit_price is not None
                    and (
                        payload.get("type") != "limit"
                        or Decimal(str(payload.get("limit_price"))) != limit_price
                    )
                )
            ):
                raise ValueError("POST response does not match requested order")
            return order
        except (BrokerError, KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise OrderOutcomeUnknownError("unusable POST acknowledgment; reconcile order") from exc

    def cancel_order(self, order_id: str) -> None:
        """Request cancellation once. Only a later order observation confirms it."""
        if not order_id:
            raise ConfigurationError("cannot cancel an unidentified order")
        request = self._build_request(
            "DELETE", "/v2/orders/" + urllib.parse.quote(order_id, safe="")
        )
        self._throttle()
        try:
            self._read(request)
        except (
            urllib.error.HTTPError,
            http.client.HTTPException,
            OSError,
            TimeoutError,
            ValueError,
        ) as exc:
            from executor.errors import OrderOutcomeUnknownError  # noqa: PLC0415

            raise OrderOutcomeUnknownError(
                "cancellation outcome requires broker reconciliation"
            ) from exc

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        """Look an order up by the executor's own idempotency key."""
        try:
            payload = self._get(
                "/v2/orders:by_client_order_id", {"client_order_id": client_order_id}
            )
        except BrokerError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        if not isinstance(payload, dict):
            return None
        return Order.from_payload(payload)

    def list_orders(
        self,
        *,
        after: dt.datetime | None = None,
        status: str = "all",
        limit: int = 500,
    ) -> tuple[Order, ...]:
        """Orders, newest first, optionally bounded below by ``after``."""
        params: dict[str, str] = {"status": status, "limit": str(limit), "direction": "desc"}
        if after is not None:
            params["after"] = after.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
        payload = self._get("/v2/orders", params)
        if not isinstance(payload, list):
            message = "GET /v2/orders did not return a list"
            raise BrokerError(message)
        if len(payload) >= limit:
            raise BrokerError("order query reached its limit; completeness cannot be established")
        return tuple(Order.from_payload(item) for item in payload)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """Extract Alpaca's error message without leaking headers or credentials."""
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return exc.reason if isinstance(exc.reason, str) else "no detail"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body[:400]
    if isinstance(parsed, dict):
        return str(parsed.get("message", parsed))[:400]
    return str(parsed)[:400]
