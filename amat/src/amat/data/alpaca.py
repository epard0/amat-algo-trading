"""Read-only Alpaca historical market-data client.

Scope and safety
----------------
This module reaches exactly one host, :data:`DATA_HOST`, and issues only ``GET``
requests against the historical market-data API. :func:`_get` refuses any path
outside :data:`_PERMITTED_PATH_PREFIXES`, so the reachable surface is bars and
corporate actions and nothing else. ``tests/unit/test_read_only_data_access.py``
asserts that property across the whole package, so a later edit widening the
surface fails the suite instead of reaching a trading venue.

Credentials are read from the environment by name
(``ALPACA_MARKET_API_KEY`` / ``ALPACA_MARKET_API_SECRET``). Their values are held
only for the lifetime of a request, are redacted in ``repr``, and are never
logged, returned, or written to a snapshot.

Timing
------
Alpaca stamps a daily bar at the session's ``00:00`` exchange-local time expressed
in UTC, so the exchange session date is recovered by converting to the exchange
timezone rather than by truncating the UTC instant -- truncating would shift every
session back one day. Intraday bars are stamped at the *start* of their interval
in true UTC; the execution window is therefore resolved per session through
:class:`zoneinfo.ZoneInfo`, which keeps the 09:30 America/New_York window correct
across daylight-saving transitions.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from zoneinfo import ZoneInfo

from amat.errors import ConfigurationError, DataIntegrityError

__all__ = [
    "DATA_HOST",
    "AlpacaCredentials",
    "AlpacaMarketData",
    "DailyBar",
    "DividendEvent",
    "SplitEvent",
    "WindowBar",
]

#: The only host this package contacts. Historical market data, read-only.
DATA_HOST = "data.alpaca.markets"

#: Every request path must start with one of these. The prefix list is the
#: whole reachable API surface: historical bars and corporate actions.
_PERMITTED_PATH_PREFIXES = ("/v2/stocks/bars", "/v1/corporate-actions")

#: Alpaca's documented basic-plan ceiling. The client self-throttles below it
#: rather than relying on the server to reject, because a 429 mid-pagination
#: would otherwise surface as silently truncated coverage.
_REQUESTS_PER_MINUTE = 190
#: Below the vendor's 10,000 ceiling on purpose. Full-size pages of daily bars
#: run to roughly a megabyte and were truncated mid-transfer often enough to
#: matter; smaller pages cost a few more requests and transfer reliably.
_MAX_PAGE = 5000
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 5

Adjustment = Literal["raw", "all"]


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One completed daily session bar for one symbol."""

    symbol: str
    session_date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float
    trade_count: int


@dataclass(frozen=True, slots=True)
class WindowBar:
    """The execution window's raw VWAP and traded volume for one symbol."""

    symbol: str
    session_date: dt.date
    window_start: dt.datetime
    window_end: dt.datetime
    vwap: float
    volume: float


@dataclass(frozen=True, slots=True)
class DividendEvent:
    """A cash distribution, keyed by its ex-date."""

    symbol: str
    ex_date: dt.date
    rate: float


@dataclass(frozen=True, slots=True)
class SplitEvent:
    """A share split, keyed by its ex-date. ``ratio`` is new shares per old."""

    symbol: str
    ex_date: dt.date
    ratio: float


@dataclass(frozen=True)
class AlpacaCredentials:
    """API credentials held in memory only, never rendered or persisted."""

    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        *,
        key_variable: str = "ALPACA_MARKET_API_KEY",
        secret_variable: str = "ALPACA_MARKET_API_SECRET",
    ) -> AlpacaCredentials:
        """Load credentials by environment-variable name.

        Raises:
            ConfigurationError: naming the missing variables. The message
                contains variable *names* only; a value is never echoed.
        """
        missing = [name for name in (key_variable, secret_variable) if not os.environ.get(name)]
        if missing:
            message = (
                f"missing market-data credentials: {', '.join(missing)}. "
                "Export them in your shell or secret manager; this repository "
                "never stores credential values."
            )
            raise ConfigurationError(message)
        return cls(key_id=os.environ[key_variable], secret_key=os.environ[secret_variable])

    def __repr__(self) -> str:
        return "AlpacaCredentials(key_id=<redacted>, secret_key=<redacted>)"


class AlpacaMarketData:
    """Paginated, rate-limited, read-only access to Alpaca historical bars."""

    def __init__(
        self,
        credentials: AlpacaCredentials | None = None,
        *,
        feed: str = "sip",
        timeout_seconds: float = 45.0,
    ) -> None:
        self._credentials = credentials or AlpacaCredentials.from_environment()
        self._feed = feed
        self._timeout = timeout_seconds
        self._request_times: list[float] = []

    # -- HTTP ---------------------------------------------------------------

    def _throttle(self) -> None:
        """Block until another request fits inside the per-minute allowance."""
        now = time.monotonic()
        self._request_times = [stamp for stamp in self._request_times if now - stamp < 60.0]
        if len(self._request_times) >= _REQUESTS_PER_MINUTE:
            time.sleep(max(0.0, 60.0 - (now - self._request_times[0])) + 0.05)
            self._throttle()
            return
        self._request_times.append(now)

    def _get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        """Issue one GET and return the decoded body.

        Raises:
            ConfigurationError: if ``path`` is outside the permitted read-only
                prefixes.
            DataIntegrityError: if the request fails after retries. Failing
                closed here is deliberate: guardrail 9 forbids treating a
                partial or errored pull as complete coverage.
        """
        if not path.startswith(_PERMITTED_PATH_PREFIXES):
            message = f"refusing a request outside the read-only market-data surface: {path!r}"
            raise ConfigurationError(message)

        url = f"https://{DATA_HOST}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(  # noqa: S310 - scheme and host are literals above
            url,
            headers={
                "APCA-API-KEY-ID": self._credentials.key_id,
                "APCA-API-SECRET-KEY": self._credentials.secret_key,
                "Accept": "application/json",
            },
            method="GET",
        )
        last_error = ""
        for attempt in range(_MAX_ATTEMPTS):
            self._throttle()
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                    payload: dict[str, Any] = json.load(response)
                    return payload
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code not in _RETRY_STATUSES:
                    break
            # A truncated chunked response (``IncompleteRead``) is the common
            # failure on multi-megabyte pages and is transient, so it must be
            # retried rather than propagated -- but it is also indistinguishable
            # from short coverage at the JSON layer, which is exactly why the
            # partial body is discarded here instead of being parsed.
            except (
                http.client.HTTPException,
                OSError,
                TimeoutError,
                json.JSONDecodeError,
            ) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(min(2.0**attempt, 30.0))

        # The path is echoed without its query string so date ranges and symbol
        # lists stay out of the message; credentials were never in it.
        message = f"market-data request to {path} failed after {attempt + 1} attempts: {last_error}"
        raise DataIntegrityError(message)

    def _paginate(self, path: str, params: dict[str, str | int]) -> Iterator[dict[str, Any]]:
        """Yield every page, following ``next_page_token`` to exhaustion.

        A partially consumed pagination is exactly the silent-truncation failure
        guardrail 9 names, so the loop terminates only on a null token.
        """
        page_params = dict(params)
        while True:
            body = self._get(path, page_params)
            yield body
            token = body.get("next_page_token")
            if not token:
                return
            page_params["page_token"] = str(token)

    # -- daily bars ---------------------------------------------------------

    def daily_bars(
        self,
        symbols: Sequence[str],
        start: dt.date,
        end: dt.date,
        *,
        adjustment: Adjustment,
        exchange_timezone: str = "America/New_York",
    ) -> list[DailyBar]:
        """Fetch completed daily bars for ``symbols`` over ``[start, end]``.

        ``adjustment="all"`` returns split- and dividend-adjusted prices for
        features; ``adjustment="raw"`` returns unadjusted prices for valuation.
        The strategy contract requires both, kept distinct.
        """
        zone = ZoneInfo(exchange_timezone)
        # Daily bars are indexed by their exchange-local session opening date.
        # An explicit timestamp selects the inclusive final daily bar without
        # a date-only end being expanded into still-future intraday SIP time.
        # Completion is enforced by the caller's completed-session cutoff.
        first_stamp = dt.datetime.combine(start, dt.time.min, zone).astimezone(dt.UTC)
        last_stamp = dt.datetime.combine(end, dt.time.min, zone).astimezone(dt.UTC)
        bars: list[DailyBar] = []
        for page in self._paginate(
            "/v2/stocks/bars",
            {
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": first_stamp.isoformat().replace("+00:00", "Z"),
                "end": last_stamp.isoformat().replace("+00:00", "Z"),
                "feed": self._feed,
                "adjustment": adjustment,
                "limit": _MAX_PAGE,
                "sort": "asc",
            },
        ):
            for symbol, rows in page.get("bars", {}).items():
                for row in rows:
                    bars.append(
                        DailyBar(
                            symbol=symbol,
                            session_date=_session_date(row["t"], zone),
                            open=float(row["o"]),
                            high=float(row["h"]),
                            low=float(row["l"]),
                            close=float(row["c"]),
                            volume=float(row["v"]),
                            vwap=float(row["vw"]),
                            trade_count=int(row["n"]),
                        )
                    )
        return bars

    # -- execution-window bars ---------------------------------------------

    def execution_window_bars(
        self,
        symbols: Sequence[str],
        session: dt.date,
        *,
        window_start: dt.time,
        window_end: dt.time,
        exchange_timezone: str = "America/New_York",
    ) -> list[WindowBar]:
        """Fetch the execution-window bar for one session.

        The window is resolved in the exchange timezone for *this* session, so
        the request tracks daylight saving rather than assuming a fixed UTC
        offset. Only bars stamped exactly at the window start are kept: Alpaca's
        ``end`` parameter is inclusive, so a neighbouring interval could be
        returned and must be discarded rather than averaged in.
        """
        zone = ZoneInfo(exchange_timezone)
        start_local = dt.datetime.combine(session, window_start, tzinfo=zone)
        end_local = dt.datetime.combine(session, window_end, tzinfo=zone)
        if end_local <= start_local:
            message = "window_end must be strictly after window_start"
            raise ConfigurationError(message)
        start_utc = start_local.astimezone(dt.UTC)
        end_utc = end_local.astimezone(dt.UTC)
        minutes = int((end_utc - start_utc).total_seconds() // 60)

        window: list[WindowBar] = []
        for page in self._paginate(
            "/v2/stocks/bars",
            {
                "symbols": ",".join(symbols),
                "timeframe": f"{minutes}Min",
                "start": start_utc.isoformat().replace("+00:00", "Z"),
                # Exclusive-by-one-second so the interval starting at the window
                # end is not returned at all.
                "end": (end_utc - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                "feed": self._feed,
                "adjustment": "raw",
                "limit": _MAX_PAGE,
                "sort": "asc",
            },
        ):
            for symbol, rows in page.get("bars", {}).items():
                for row in rows:
                    stamp = dt.datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
                    if stamp != start_utc:
                        continue
                    window.append(
                        WindowBar(
                            symbol=symbol,
                            session_date=session,
                            window_start=start_utc,
                            window_end=end_utc,
                            vwap=float(row["vw"]),
                            volume=float(row["v"]),
                        )
                    )
        return window

    # -- corporate actions --------------------------------------------------

    def corporate_actions(
        self,
        symbols: Sequence[str],
        start: dt.date,
        end: dt.date,
    ) -> tuple[list[DividendEvent], list[SplitEvent]]:
        """Fetch cash dividends and splits over ``[start, end]``.

        Only unambiguous events are returned. A split record whose ratio cannot
        be reconstructed, or a dividend without a usable ex-date, raises rather
        than being dropped: an unexplained corporate action must fail closed.
        """
        dividends: list[DividendEvent] = []
        splits: list[SplitEvent] = []
        for page in self._paginate(
            "/v1/corporate-actions",
            {
                "symbols": ",".join(symbols),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 1000,
                "sort": "asc",
            },
        ):
            actions = page.get("corporate_actions", {})
            for row in actions.get("cash_dividends", []):
                ex_date = row.get("ex_date")
                if not ex_date:
                    message = f"cash dividend for {row.get('symbol')!r} has no ex_date"
                    raise DataIntegrityError(message)
                dividends.append(
                    DividendEvent(
                        symbol=str(row["symbol"]),
                        ex_date=dt.date.fromisoformat(ex_date),
                        rate=float(row["rate"]),
                    )
                )
            for kind in ("forward_splits", "reverse_splits"):
                for row in actions.get(kind, []):
                    ex_date = row.get("ex_date") or row.get("process_date")
                    old = float(row.get("old_rate", 0.0))
                    new = float(row.get("new_rate", 0.0))
                    if not ex_date or old <= 0 or new <= 0:
                        message = (
                            f"{kind[:-1]} for {row.get('symbol')!r} is ambiguous "
                            f"(ex_date={ex_date!r}, old_rate={old!r}, new_rate={new!r})"
                        )
                        raise DataIntegrityError(message)
                    splits.append(
                        SplitEvent(
                            symbol=str(row["symbol"]),
                            ex_date=dt.date.fromisoformat(ex_date),
                            ratio=new / old,
                        )
                    )
        return dividends, splits


def _session_date(stamp: str, zone: ZoneInfo) -> dt.date:
    """Recover the exchange session date from a daily-bar UTC timestamp."""
    moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return moment.astimezone(zone).date()
