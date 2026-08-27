"""Async Kalshi SDK wrapper for discovery, book depth, and bounded execution."""

from __future__ import annotations

import asyncio

import aiohttp
import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Sequence

from loguru import logger

from config import KalshiSettings, SERIES_BY_ASSET

try:
    from kalshi_python_async import (
        ApiException,
        Configuration,
        KalshiAuth,
        KalshiClient,
        MarketApi,
        OrdersApi,
        PortfolioApi,
    )
except ImportError as exc:  # pragma: no cover - exercised only on bad installs
    raise RuntimeError(
        "The official async Kalshi SDK is missing. Run: uv sync"
    ) from exc


CENT = Decimal("0.01")
CONTRACT_STEP = Decimal("0.01")
MAX_PRICE = Decimal("0.99")
# The generated SDK otherwise permits a GET to wait five minutes. Live market
# discovery and position supervision need a much tighter failure boundary.
KALSHI_READ_ATTEMPT_TIMEOUT_SECONDS = 3.0
KALSHI_DISCOVERY_ASSET_TIMEOUT_SECONDS = 12.0

# Idempotent venue reads can be repeated when the API explicitly asks us to
# retry or reports a temporary server failure.  Keep this separate from order
# submission: retrying an ambiguous money-bearing request can duplicate a fill.
RETRYABLE_IDEMPOTENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Order rejections that mean "the book moved", not "the bot is broken". They are
# expected on thin 15-minute markets and must never abort a trading cycle.
BENIGN_ORDER_ERRORS = frozenset(
    {
        "fill_or_kill_insufficient_resting_volume",
        "insufficient_resting_volume",
        "market_not_active",
        "market_closed",
        "price_out_of_range",
        "too_late_to_trade",
    }
)


@dataclass(frozen=True)
class KalshiMarket:
    asset: str
    ticker: str
    event_ticker: str
    title: str
    target_price: Decimal
    open_time: datetime
    close_time: datetime
    yes_bid: Decimal
    yes_ask: Decimal
    yes_bid_size: Decimal
    yes_ask_size: Decimal
    fee_multiplier: Decimal
    exchange_index: int = 0

    @property
    def no_ask(self) -> Decimal:
        return Decimal("1") - self.yes_bid


@dataclass(frozen=True)
class BookQuote:
    """Aggregated executable liquidity for one outcome at one moment.

    ``best_price`` is what the outcome actually costs to buy right now, and
    ``depth_at_limit`` is how many contracts are resting at or better than the
    limit we intend to send. Sizing against this instead of a cached
    top-of-book size is what prevents unfillable orders.
    """

    outcome: str
    best_price: Decimal
    limit_price: Decimal
    depth_at_limit: Decimal


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Raw normalized depth plus the derived microstructure measures."""

    ticker: str
    observed_at: datetime
    yes_levels: tuple[tuple[Decimal, Decimal], ...]
    no_levels: tuple[tuple[Decimal, Decimal], ...]
    analysis: Any
    source: str = "rest"
    sequence: int | None = None


@dataclass(frozen=True)
class OrderPlan:
    ticker: str
    outcome: str
    sdk_book_side: str
    sdk_yes_leg_price: Decimal
    outcome_limit_price: Decimal
    contracts: Decimal
    maximum_cost: Decimal
    fee_reserve: Decimal
    maximum_total_debit: Decimal
    client_order_id: str
    sizing_fraction: Decimal = Decimal("0")
    strategy: str = "predictor"
    intent: str = "entry"
    trade_id: str | None = None
    exchange_index: int = 0


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    client_order_id: str
    fill_count: Decimal
    remaining_count: Decimal
    average_fill_price: Decimal | None
    error_code: str | None = None

    @property
    def filled(self) -> bool:
        return self.fill_count > 0


@dataclass(frozen=True)
class MarketStatus:
    ticker: str
    status: str
    result: str | None
    settlement_value: Decimal | None
    settlement_time: datetime | None


def _levels(raw: Sequence[Sequence[str]] | None) -> list[tuple[Decimal, Decimal]]:
    if not raw:
        return []
    parsed: list[tuple[Decimal, Decimal]] = []
    for level in raw:
        if len(level) < 2:
            continue
        parsed.append((Decimal(str(level[0])), Decimal(str(level[1]))))
    return parsed


def quote_from_orderbook(
    outcome: str,
    yes_bid_levels: Sequence[Sequence[str]] | None,
    no_bid_levels: Sequence[Sequence[str]] | None,
    max_slippage: Decimal,
) -> BookQuote | None:
    """Turn Kalshi's bids-only book into an executable quote for one outcome.

    Kalshi returns resting bids for both sides and no asks, because a YES bid at
    ``p`` *is* a NO ask at ``1 - p`` for the same size. So buying YES means
    crossing the NO bids, and buying NO means crossing the YES bids.
    """
    normalized = outcome.lower()
    if normalized not in {"yes", "no"}:
        raise ValueError("outcome must be 'yes' or 'no'")

    # To buy an outcome we lift the opposite side's resting bids.
    opposing = _levels(no_bid_levels if normalized == "yes" else yes_bid_levels)
    if not opposing:
        return None

    best_opposing_bid = max(price for price, _ in opposing)
    best_price = Decimal("1") - best_opposing_bid
    limit_price = min(MAX_PRICE, best_price + max_slippage)
    if not CENT <= limit_price <= MAX_PRICE:
        return None

    # Our limit of L can lift any opposing bid at or above 1 - L.
    threshold = Decimal("1") - limit_price
    depth = sum(
        (size for price, size in opposing if price >= threshold),
        Decimal("0"),
    )
    return BookQuote(
        outcome=normalized,
        best_price=best_price,
        limit_price=limit_price,
        depth_at_limit=depth,
    )


def _error_code(exc: ApiException) -> str | None:
    payload: Any = None
    body = getattr(exc, "body", None)
    if body:
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = None
    if not isinstance(payload, dict):
        data = getattr(exc, "data", None)
        if hasattr(data, "model_dump"):
            payload = data.model_dump(exclude_none=True)
        elif isinstance(data, dict):
            payload = data
    if isinstance(payload, dict):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            code = error.get("code")
            return str(code) if code else None
    return None


def _error_message(exc: ApiException) -> str:
    """Return the useful venue text without logging credentials or headers.

    The generated SDK currently deserializes Kalshi's nested error object into
    an empty flat model. ``str(exc)`` consequently prints headers followed by
    ``code=None`` and hides the raw JSON body. Prefer the raw body and support
    both the nested and flat response shapes so a future SDK update does not
    make order failures opaque again.
    """
    body = getattr(exc, "body", None)
    if body:
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return str(body)[:500]
        if isinstance(payload, dict):
            error = payload.get("error", payload)
            if isinstance(error, dict):
                parts = [error.get("message"), error.get("details")]
                useful = ": ".join(str(part) for part in parts if part)
                if useful:
                    return useful[:500]
            return json.dumps(payload, separators=(",", ":"))[:500]
    data = getattr(exc, "data", None)
    if hasattr(data, "model_dump"):
        dumped = data.model_dump(exclude_none=True)
        if dumped:
            return json.dumps(dumped, separators=(",", ":"))[:500]
    return str(getattr(exc, "reason", None) or "venue rejected the order")[:500]


class AsyncKalshiTradingClient:
    """A narrow async facade over the official SDK.

    The V2 event order endpoint quotes one YES book. A bid buys YES. An ask
    sells YES, which is economically equivalent to buying NO at ``1 - price``.
    """

    def __init__(self, settings: KalshiSettings, authenticated: bool) -> None:
        self.settings = settings
        self.authenticated = authenticated
        self._fee_multiplier_cache: dict[str, Decimal] = {}
        self._gate = asyncio.Semaphore(settings.request_concurrency)
        self._pace_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._closed = False
        self._orders_enabled = True
        self._reopens = 0
        self._reopen_lock = asyncio.Lock()
        self._transport_generation = 0
        self._last_balance_breakdown: dict[int, Decimal] = {}
        self._last_available_balance: Decimal | None = None
        self._event_ledger: Any = None
        self._run_id_provider: Any = lambda: None
        self._build_transport()

    def bind_event_ledger(self, ledger: Any, run_id_provider: Any) -> None:
        """Attach normalized lifecycle recording after the bot builds its ledger."""
        self._event_ledger = ledger
        self._run_id_provider = run_id_provider

    def _build_transport(self) -> None:
        """Construct the SDK client and its API handles.

        Separated from ``__init__`` so a dead aiohttp session can be replaced
        without discarding this object - every collaborator holds a reference to
        *it*, not to the session underneath, so rebuilding here heals all of
        them at once.
        """
        settings, authenticated = self.settings, self.authenticated
        configuration = Configuration(host=settings.rest_url)
        self._client = KalshiClient(configuration)
        if authenticated:
            if not settings.api_key_id:
                raise RuntimeError("KALSHI_API_KEY is required")
            if not settings.private_key_path.is_file():
                raise RuntimeError(
                    f"Kalshi private key not found at {settings.private_key_path}"
                )
            # Do not use ``ApiClient.set_kalshi_auth``: in kalshi-python-async
            # 3.26.0 it references ``KalshiAuth`` without importing it (NameError)
            # and, once that is worked around, passes the key *path* into a
            # constructor that wants the PEM *text*. Build the signer directly.
            self._client.kalshi_auth = KalshiAuth(
                settings.api_key_id,
                settings.private_key_path.read_text(),
            )
        self._markets = MarketApi(self._client)
        self._orders = OrdersApi(self._client)
        self._portfolio = PortfolioApi(self._client)
        self._transport_generation = getattr(self, "_transport_generation", 0) + 1

    async def close(self) -> None:
        """Close the transport. The object stays usable: a later call reopens it.

        `close()` used to be terminal, and a mode switch closes the client then
        builds a new one — which left every collaborator that had captured the
        old object raising ``RuntimeError: Session is closed`` on its next
        order. Marking rather than destroying means the worst case is a
        reconnect, not a dead engine.
        """
        self._closed = True
        try:
            await self._client.close()
        except Exception:  # noqa: BLE001 - closing must never raise upward
            pass

    def stop_order_submission(self) -> None:
        """Reject any order that has not crossed the client boundary yet.

        Shutdown must still allow cancellation and read-side reconciliation,
        but it must not begin a new entry or exit.  An order already awaiting a
        venue response is deliberately allowed to finish so its fill state can
        be adopted and written to the ledger instead of becoming orphaned.
        """
        self._orders_enabled = False

    async def _reopen(self) -> None:
        """Replace a dead transport in place."""
        self._reopens += 1
        logger.warning(
            "Kalshi session was closed; rebuilding transport (reopen #{})",
            self._reopens,
        )
        try:
            await self._client.close()
        except Exception:  # noqa: BLE001
            pass
        self._build_transport()
        self._closed = False

    async def _reopen_if_stale(self, failed_generation: int) -> None:
        """Single-flight recovery for a burst that shared one dead session.

        Every concurrent request remembers the transport generation it used.
        The first failure rebuilds it; later failures from that same generation
        observe the increment and reuse the new client instead of closing and
        rebuilding it again.
        """
        lock = getattr(self, "_reopen_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._reopen_lock = lock
        async with lock:
            current = getattr(self, "_transport_generation", 0)
            if current != failed_generation and not getattr(self, "_closed", False):
                return
            await self._reopen()

    async def get_available_balance(self, exchange_index: int | None = None) -> Decimal:
        """Return spendable cash, optionally for one matching-engine shard.

        Kalshi exposes an aggregate account balance and an indexed breakdown.
        An order can only spend the balance attached to its market's exchange
        index, so entry sizing must pass that index instead of treating the
        account-wide total as fungible at the order boundary.
        """
        self._require_authentication()
        kwargs: dict[str, Any] = {"subaccount": self.settings.subaccount}
        if exchange_index is not None:
            kwargs["exchange_index"] = exchange_index
        response = await self._call_with_backoff(
            lambda: self._portfolio.get_balance(**kwargs)
        )
        breakdown: dict[int, Decimal] = {}
        for entry in getattr(response, "balance_breakdown", None) or []:
            try:
                index = int(getattr(entry, "exchange_index"))
                legacy_dollars = getattr(entry, "balance_dollars", None)
                raw_balance = getattr(entry, "balance", None)
                if legacy_dollars is not None:
                    amount = Decimal(str(legacy_dollars))
                elif isinstance(raw_balance, str):
                    # Current IndexedBalance.balance is already a fixed-point
                    # dollar string. Dividing it by 100 produced the live
                    # $0.68/$0.13 display from real ~$68/~$13 balances and
                    # falsely suppressed affordable orders.
                    amount = Decimal(raw_balance)
                else:
                    # Compatibility with older generated SDK/test models that
                    # exposed this nested field as integer cents.
                    amount = Decimal(str(raw_balance)) / Decimal("100")
                breakdown[index] = amount
            except (ArithmeticError, AttributeError, TypeError, ValueError):
                continue
        if breakdown:
            self._last_balance_breakdown = breakdown
        # The integer cents field is explicitly documented as available cash.
        # It is slightly conservative when fee rebates leave sub-cent residue,
        # which is exactly the safe direction for an order ceiling.
        cents = getattr(response, "balance", None)
        if exchange_index is not None and exchange_index in breakdown:
            available = breakdown[exchange_index]
        elif cents is not None:
            available = Decimal(str(cents)) / Decimal("100")
        else:
            available = Decimal(response.balance_dollars)
        if exchange_index is not None and exchange_index not in breakdown:
            self._last_balance_breakdown[exchange_index] = available
        if exchange_index is None:
            self._last_available_balance = available
        return available

    def cached_balance_breakdown(self) -> dict[int, Decimal]:
        """Return the last API-provided per-index cash values for diagnostics."""
        return dict(getattr(self, "_last_balance_breakdown", {}))

    def cached_available_balance(self) -> Decimal | None:
        """Return the last verified aggregate cash without performing a GET."""
        return getattr(self, "_last_available_balance", None)

    async def get_active_markets(self) -> list[KalshiMarket]:
        """Return the nearest-closing active 15-minute contract per asset.

        All assets are discovered concurrently; the semaphore keeps the burst
        inside Kalshi's public rate limit.
        """

        async def bounded(asset: str) -> KalshiMarket | None:
            return await asyncio.wait_for(
                self._get_asset_market(asset),
                timeout=KALSHI_DISCOVERY_ASSET_TIMEOUT_SECONDS,
            )

        results = await asyncio.gather(
            *(bounded(asset) for asset in self.settings.assets),
            return_exceptions=True,
        )
        markets: list[KalshiMarket] = []
        failures: list[tuple[str, BaseException]] = []
        for asset, result in zip(self.settings.assets, results):
            if isinstance(result, BaseException):
                failures.append((asset, result))
                continue
            if result is not None:
                markets.append(result)
        # The scheduler can retain the last still-open contract for only the
        # assets whose REST reads failed. Successful assets stay eligible for
        # entries; cached assets remain supervision-only until refreshed.
        self.last_discovery_failed_assets = frozenset(asset for asset, _ in failures)
        if failures and not markets:
            counts: dict[str, int] = {}
            for _, exc in failures:
                name = type(exc).__name__
                counts[name] = counts.get(name, 0) + 1
            logger.error(
                "Market discovery REST unavailable for all {} assets ({})",
                len(failures),
                ", ".join(f"{name} x{count}" for name, count in counts.items()),
            )
        elif failures:
            for asset, exc in failures:
                detail = str(exc).strip() or type(exc).__name__
                logger.warning("{}: market discovery failed: {}", asset, detail)
        return markets

    async def _get_asset_market(self, asset: str) -> KalshiMarket | None:
        now = datetime.now(timezone.utc)
        series_ticker = SERIES_BY_ASSET[asset]
        fee_multiplier = await self._get_fee_multiplier(series_ticker)
        response = await self._call_with_backoff(
            lambda: self._markets.get_markets(
                series_ticker=series_ticker,
                status="open",
                limit=20,
            )
        )
        candidates: list[KalshiMarket] = []
        for market in response.markets:
            close_time = self._as_utc(market.close_time)
            open_time = self._as_utc(market.open_time)
            if close_time <= now or open_time > now:
                continue
            if market.strike_type not in {"greater", "greater_or_equal"}:
                continue
            if market.floor_strike is None:
                continue
            candidates.append(
                KalshiMarket(
                    asset=asset,
                    ticker=market.ticker,
                    event_ticker=market.event_ticker,
                    title=market.title,
                    target_price=Decimal(str(market.floor_strike)),
                    open_time=open_time,
                    close_time=close_time,
                    yes_bid=Decimal(market.yes_bid_dollars),
                    yes_ask=Decimal(market.yes_ask_dollars),
                    yes_bid_size=Decimal(market.yes_bid_size_fp),
                    yes_ask_size=Decimal(market.yes_ask_size_fp),
                    fee_multiplier=fee_multiplier,
                    exchange_index=int(getattr(market, "exchange_index", 0) or 0),
                )
            )
        if not candidates:
            return None
        return min(candidates, key=lambda item: item.close_time)

    async def get_quote(self, ticker: str, outcome: str) -> BookQuote | None:
        """Read the live book immediately before sizing an order."""
        response = await self._call_with_backoff(
            lambda: self._markets.get_market_orderbook(ticker=ticker, depth=10)
        )
        book = response.orderbook_fp
        if book is None:
            return None
        return quote_from_orderbook(
            outcome=outcome,
            yes_bid_levels=book.yes_dollars,
            no_bid_levels=book.no_dollars,
            max_slippage=self.settings.max_slippage,
        )

    async def get_book_analysis(self, ticker: str, depth: int = 20) -> Any:
        """Read the full depth snapshot and analyse it.

        Deeper than ``get_quote``'s 10 levels because the imbalance and slope
        measures are about the *shape* of the book, not just what is executable
        at the touch - truncating at the touch is what makes a thin book look
        like a deep one.

        Returns None rather than raising on any failure: this informs a decision,
        it does not gate the cycle, and a book that cannot be read is handled by
        the ordinary liquidity gates.
        """
        snapshot = await self.get_orderbook_snapshot(ticker, depth=depth)
        return snapshot.analysis if snapshot is not None else None

    async def get_orderbook_snapshot(
        self, ticker: str, depth: int = 20
    ) -> OrderBookSnapshot | None:
        """Read and retain exact depth levels for replay and training."""
        from core.strategy_brain.strategies.orderbook import analyse

        try:
            response = await self._call_with_backoff(
                lambda: self._markets.get_market_orderbook(ticker=ticker, depth=depth)
            )
        except Exception:
            return None
        book = getattr(response, "orderbook_fp", None)
        if book is None:
            return None
        yes_levels = tuple(_levels(book.yes_dollars))
        no_levels = tuple(_levels(book.no_dollars))
        return OrderBookSnapshot(
            ticker=ticker,
            observed_at=datetime.now(timezone.utc),
            yes_levels=yes_levels,
            no_levels=no_levels,
            analysis=analyse(yes_levels, no_levels),
        )

    async def get_market_status(self, ticker: str) -> MarketStatus:
        """Read the authoritative lifecycle and final outcome for a market."""
        response = await self._call_with_backoff(
            lambda: self._markets.get_market(ticker=ticker)
        )
        market = response.market
        result = market.result.lower() if market.result else None
        return MarketStatus(
            ticker=market.ticker,
            status=market.status,
            result=result if result in {"yes", "no", "void"} else None,
            settlement_value=(
                Decimal(market.settlement_value_dollars)
                if market.settlement_value_dollars is not None
                else None
            ),
            settlement_time=(
                self._as_utc(market.settlement_ts)
                if market.settlement_ts is not None
                else None
            ),
        )

    async def already_traded(self, ticker: str) -> bool:
        """Whether Kalshi currently reports non-zero exposure in this market.

        Historical volume is not an open position. Using ``total_traded`` here
        permanently blocked the explicitly bounded opposite-side re-entry after
        a profitable exit; ``position_fp`` retains restart safety without
        confusing history with exposure.
        """
        self._require_authentication()
        response = await self._call_with_backoff(
            lambda: self._portfolio.get_positions(
                ticker=ticker,
                count_filter="total_traded",
                subaccount=self.settings.subaccount,
                limit=10,
            )
        )
        return any(
            position.ticker == ticker and Decimal(position.position_fp) != 0
            for position in response.market_positions
        )

    async def get_position_contracts(self, ticker: str, outcome: str) -> Decimal:
        """Return venue-reported contracts held on one outcome.

        Kalshi represents YES exposure as positive and NO exposure as negative.
        Returning only the requested side prevents an exit reconciler from
        interpreting an opposite position as inventory it is allowed to sell.
        """
        self._require_authentication()
        response = await self._call_with_backoff(
            lambda: self._portfolio.get_positions(
                ticker=ticker,
                count_filter="total_traded",
                subaccount=self.settings.subaccount,
                limit=10,
            )
        )
        for position in response.market_positions:
            if position.ticker != ticker:
                continue
            signed = Decimal(str(position.position_fp or 0))
            if outcome == "yes":
                return max(Decimal("0"), signed)
            return max(Decimal("0"), -signed)
        return Decimal("0")

    def build_order_plan(
        self,
        *,
        market: KalshiMarket,
        quote: BookQuote,
        available_balance: Decimal,
        model_probability: Decimal,
        maximum_budget: Decimal | None = None,
    ) -> OrderPlan:
        """Size one fixed-risk order and clip it to executable book depth.

        Paper and live deliberately call this exact path. ``model_probability``
        remains in the interface because the decision log and replay tools pass
        it, but probability only decides whether an order is admitted; it never
        increases the stake. ``maximum_budget`` is a backwards-compatible
        additional ceiling, not a mode-specific sizing rule.
        """
        outcome = quote.outcome
        if outcome not in {"yes", "no"}:
            raise ValueError("outcome must be 'yes' or 'no'")
        if available_balance <= 0:
            raise ValueError("Available balance must be positive")
        if not CENT <= quote.limit_price <= MAX_PRICE:
            raise ValueError("No executable price is available for the outcome")

        del model_probability
        budget = min(available_balance, self.settings.trade_budget_usd)
        if maximum_budget is not None:
            if maximum_budget <= 0:
                raise ValueError("maximum_budget must be positive")
            budget = min(budget, maximum_budget)
        budget = budget.quantize(CENT, rounding=ROUND_DOWN)
        if budget < CENT:
            raise ValueError("Calculated order budget is below $0.01")
        sizing_fraction = budget / available_balance

        if outcome == "yes":
            book_side = "bid"
            yes_leg_price = quote.limit_price
        else:
            book_side = "ask"
            yes_leg_price = Decimal("1") - quote.limit_price

        # Never let a configured estimate understate the series' actual fee
        # multiplier. The extra cent covers fee and final balance rounding.
        quadratic_fee_per_contract = (
            Decimal("0.07")
            * market.fee_multiplier
            * quote.limit_price
            * (Decimal("1") - quote.limit_price)
        )
        fee_per_contract = max(
            self.settings.fee_reserve_per_contract,
            quadratic_fee_per_contract,
        )
        spendable_before_rounding = budget - CENT
        if spendable_before_rounding <= 0:
            raise ValueError("Calculated order budget is too small after fee reserve")
        contracts = (
            spendable_before_rounding / (quote.limit_price + fee_per_contract)
        ).quantize(CONTRACT_STEP, rounding=ROUND_DOWN)
        contracts = min(
            contracts,
            quote.depth_at_limit.quantize(CONTRACT_STEP, rounding=ROUND_DOWN),
        )
        if contracts < self.settings.min_contracts:
            raise ValueError(
                f"Executable size {contracts} is below MIN_CONTRACTS "
                f"{self.settings.min_contracts}"
            )

        maximum_cost = (contracts * quote.limit_price).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        fee_reserve = (contracts * fee_per_contract + CENT).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        maximum_total_debit = maximum_cost + fee_reserve
        if maximum_total_debit > budget:
            raise RuntimeError("Internal sizing error: order exceeds its budget")
        return OrderPlan(
            ticker=market.ticker,
            outcome=outcome,
            sdk_book_side=book_side,
            sdk_yes_leg_price=yes_leg_price.quantize(
                Decimal("0.0001"), rounding=ROUND_DOWN
            ),
            outcome_limit_price=quote.limit_price.quantize(
                Decimal("0.0001"), rounding=ROUND_DOWN
            ),
            contracts=contracts,
            maximum_cost=maximum_cost,
            fee_reserve=fee_reserve,
            maximum_total_debit=maximum_total_debit,
            client_order_id=str(uuid.uuid4()),
            sizing_fraction=sizing_fraction,
            exchange_index=market.exchange_index,
        )

    # ---------------------------------------------------------- ordering --
    #
    # All three order paths go through _submit, so the resilience below cannot
    # drift between them. There are four layers, and which one applies depends
    # entirely on whether the venue can possibly have seen the order:
    #
    #   1. benign rejection      -> zero fill, caller cools the ticker down
    #   2. dead session          -> provably PRE-transmission, so heal and retry
    #   3. anything else ambiguous -> do NOT retry blind; ask the venue whether
    #                              our client_order_id already exists, and adopt
    #                              the answer
    #   4. still unresolved      -> zero fill with an error code, never an
    #                              exception into the trading cycle
    #
    # Layer 3 is the one that makes any of this safe on a money path. A blind
    # retry after a timeout is how a bot buys the same position twice.

    def _result_from(self, response: Any, plan: OrderPlan) -> OrderResult:
        average = (
            Decimal(response.average_fill_price)
            if getattr(response, "average_fill_price", None) is not None
            else None
        )
        return OrderResult(
            order_id=getattr(response, "order_id", "") or "",
            client_order_id=getattr(response, "client_order_id", None)
            or plan.client_order_id,
            fill_count=Decimal(str(getattr(response, "fill_count", 0) or 0)),
            remaining_count=Decimal(str(getattr(response, "remaining_count", 0) or 0)),
            average_fill_price=average,
        )

    def _unfilled(self, plan: OrderPlan, code: str | None) -> OrderResult:
        return OrderResult(
            order_id="",
            client_order_id=plan.client_order_id,
            fill_count=Decimal("0"),
            remaining_count=plan.contracts,
            average_fill_price=None,
            error_code=code,
        )

    async def get_queue_positions(
        self, tickers: Sequence[str] | None = None
    ) -> dict[str, Decimal]:
        """Contracts that must match ahead of each resting order, by order id.

        Kalshi computes this on price-time priority, so it counts **matches**
        and nothing else. That is what makes it different from watching resting
        size fall on the book, which also drops when someone ahead of us
        cancels - the scalper's paper fill model can only do the latter, and
        this is the number that says how wrong that approximation is.

        A failure returns an empty mapping rather than raising. This informs a
        fill model and a log line; it must never be the reason a cycle dies or
        a position goes unsupervised.
        """
        if not self.authenticated:
            return {}
        try:
            response = await self._call_with_backoff(
                lambda: self._orders.get_order_queue_positions(
                    market_tickers=",".join(tickers) if tickers else None
                )
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic, never fatal
            logger.debug("Queue positions unavailable: {}", exc)
            return {}

        out: dict[str, Decimal] = {}
        for entry in getattr(response, "queue_positions", None) or []:
            order_id = getattr(entry, "order_id", None)
            position = getattr(entry, "queue_position", None)
            if order_id is None or position is None:
                continue
            try:
                out[str(order_id)] = Decimal(str(position))
            except (ArithmeticError, TypeError, ValueError):
                continue
        return out

    async def find_submitted_order(self, plan: OrderPlan) -> OrderResult | None:
        """Ask the venue whether our own ``client_order_id`` already exists.

        This is what turns an ambiguous failure into a decidable one. The
        client_order_id is generated per plan and sent with the order, so if the
        request did reach Kalshi the order is findable, and the correct action is
        to adopt it rather than send a second one.

        Returns None when the order provably is not there, or when the lookup
        itself fails - in which case the caller must assume the worst and NOT
        retry.
        """
        if not self.authenticated:
            return None
        try:
            response = await self._call_with_backoff(
                lambda: self._orders.get_orders(
                    ticker=plan.ticker,
                    limit=50,
                    subaccount=self.settings.subaccount,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a failed lookup is "unknown"
            logger.warning(
                "Could not reconcile {} by client_order_id: {}",
                plan.ticker,
                exc,
            )
            return None
        for order in getattr(response, "orders", None) or []:
            if getattr(order, "client_order_id", None) == plan.client_order_id:
                logger.warning(
                    "Reconciled {}: the venue already has our order {}",
                    plan.ticker,
                    getattr(order, "order_id", "?"),
                )
                return self._result_from(order, plan)
        return None

    async def _submit(
        self,
        plan: OrderPlan,
        *,
        _entry_balance_lock_held: bool = False,
        **kwargs: Any,
    ) -> OrderResult:
        """Send one order, with the four layers above applied."""
        self._require_authentication()
        if not getattr(self, "_orders_enabled", True):
            logger.warning(
                "Order suppressed on {}: graceful shutdown is in progress",
                plan.ticker,
            )
            return self._unfilled(plan, "shutdown_requested")
        is_entry = not bool(kwargs.get("reduce_only", False))
        if is_entry and not _entry_balance_lock_held:
            lock = getattr(self, "_entry_submission_lock", None)
            if lock is None:
                lock = asyncio.Lock()
                self._entry_submission_lock = lock
            async with lock:
                return await self._submit(
                    plan,
                    _entry_balance_lock_held=True,
                    **kwargs,
                )
        audit_id: str | None = None
        if getattr(self, "_event_ledger", None) is not None:
            tif = str(kwargs.get("time_in_force", "immediate_or_cancel"))
            order_type = {
                "immediate_or_cancel": "ioc",
                "fill_or_kill": "fok",
                "good_till_canceled": "gtc",
            }.get(tif, "gtc")
            audit_id = self._event_ledger.record_order(
                run_id=self._run_id_provider(),
                trade_id=plan.trade_id,
                client_order_id=plan.client_order_id,
                ticker=plan.ticker,
                strategy=plan.strategy,
                intent=plan.intent,
                book_side=plan.sdk_book_side,
                outcome=plan.outcome,
                order_type=order_type,
                limit_price=plan.outcome_limit_price,
                requested_contracts=plan.contracts,
                reduce_only=bool(kwargs.get("reduce_only", False)),
                post_only=bool(kwargs.get("post_only", False)),
            )
            self._event_ledger.record_order_transition(
                order_record_id=audit_id,
                state="submitted",
                remaining_count=plan.contracts,
            )

        def done(result: OrderResult) -> OrderResult:
            if audit_id is None:
                return result
            if result.error_code:
                state = (
                    "unknown"
                    if result.error_code.startswith("transport_")
                    else "rejected"
                )
            elif result.fill_count >= plan.contracts:
                state = "filled"
            elif result.fill_count > 0:
                state = "partial"
            else:
                state = "resting" if kwargs.get("post_only") else "expired"
            self._event_ledger.record_order_transition(
                order_record_id=audit_id,
                state=state,
                venue_order_id=result.order_id or None,
                fill_count=result.fill_count,
                remaining_count=result.remaining_count,
                error_code=result.error_code,
            )
            if result.fill_count > 0 and result.average_fill_price is not None:
                outcome_price = (
                    result.average_fill_price
                    if plan.outcome == "yes"
                    else Decimal("1") - result.average_fill_price
                )
                self._event_ledger.record_fill(
                    order_record_id=audit_id,
                    price=outcome_price,
                    contracts=result.fill_count,
                    fee=Decimal("0"),
                    liquidity_role="maker" if kwargs.get("post_only") else "taker",
                )
            return result

        if is_entry and hasattr(self, "_portfolio"):
            try:
                spendable = await self.get_available_balance(plan.exchange_index)
            except Exception as exc:  # fail closed on a money boundary
                logger.warning(
                    "Order suppressed on {}: exchange-index {} balance could "
                    "not be verified ({})",
                    plan.ticker,
                    plan.exchange_index,
                    type(exc).__name__,
                )
                return done(self._unfilled(plan, "balance_unavailable"))
            if plan.maximum_total_debit > spendable:
                logger.warning(
                    "Order suppressed on {}: planned maximum debit ${} exceeds "
                    "the ${} available on exchange index {}",
                    plan.ticker,
                    plan.maximum_total_debit,
                    spendable,
                    plan.exchange_index,
                )
                return done(self._unfilled(plan, "insufficient_indexed_balance"))

        async def send() -> Any:
            return await self._orders.create_order_v2(
                ticker=plan.ticker,
                # Cash and order routing are scoped to the market's exchange
                # index. An aggregate/unscoped value can appear insufficient
                # to the matching engine that actually hosts the ticker.
                exchange_index=plan.exchange_index,
                client_order_id=plan.client_order_id,
                side=plan.sdk_book_side,
                count=f"{plan.contracts:.2f}",
                price=f"{plan.sdk_yes_leg_price:.4f}",
                cancel_order_on_pause=True,
                subaccount=self.settings.subaccount,
                **kwargs,
            )

        for attempt in range(2):
            generation = getattr(self, "_transport_generation", 0)
            try:
                return done(self._result_from(await send(), plan))
            except ApiException as exc:
                # Layer 1. A rejection is an answer: the venue saw the order and
                # declined it, so there is nothing resting and nothing to undo.
                status = getattr(exc, "status", None)
                code = _error_code(exc) or f"http_{status or 'error'}"
                detail = _error_message(exc)
                if code == "header_timestamp_expired" and attempt == 0:
                    # Kalshi rejected authentication before admitting the
                    # order, so this is one of the rare 4xx responses that is
                    # both deterministic and safe to resubmit. A fresh SDK
                    # call creates fresh auth headers; the unchanged
                    # client_order_id remains the venue-side deduplication key.
                    logger.warning(
                        "Order auth timestamp expired on {}; retrying once "
                        "with fresh headers and the same client_order_id",
                        plan.ticker,
                    )
                    await asyncio.sleep(0)
                    continue
                if status is not None and 400 <= status < 500 and status != 409:
                    logger.warning(
                        "Order rejected on {} ({}): {}",
                        plan.ticker,
                        code,
                        detail,
                    )
                    return done(self._unfilled(plan, code))
                if code in BENIGN_ORDER_ERRORS or "post_only" in code:
                    return done(self._unfilled(plan, code))

                # A duplicate client_order_id (409) or a server failure can be
                # ambiguous: the request may already have reached the matcher.
                # Reconcile it exactly like a transport timeout and never send
                # a second money-bearing request blindly.
                logger.error(
                    "Ambiguous order response on {} ({}): {}",
                    plan.ticker,
                    code,
                    detail,
                )
                existing = await self.find_submitted_order(plan)
                if existing is not None:
                    return done(existing)
                return done(self._unfilled(plan, code))
            except BaseException as exc:
                if self.is_dead_session(exc) and attempt == 0:
                    # Layer 2. aiohttp raised before transmitting, so the venue
                    # never saw this. Rebuilding and resending cannot duplicate.
                    await self._reopen_if_stale(generation)
                    continue
                # Layer 3. Ambiguous. The order may or may not be live.
                logger.error(
                    "Order transport failure on {}: {}: {}",
                    plan.ticker,
                    type(exc).__name__,
                    exc,
                )
                existing = await self.find_submitted_order(plan)
                if existing is not None:
                    return done(existing)
                # Layer 4. Not found, or the lookup failed. Either way this pass
                # gets no fill; the caller cools the ticker down rather than
                # sending a second order into an unknown state.
                return done(self._unfilled(plan, f"transport_{type(exc).__name__}"))
        return done(
            self._unfilled(plan, "transport_retries_exhausted")
        )  # pragma: no cover

    async def place_order(self, plan: OrderPlan) -> OrderResult:
        """Place an immediate-or-cancel V2 order.

        IOC replaces fill-or-kill deliberately: a partial fill at our limit is a
        good trade, whereas FOK rejects the whole order the moment the resting
        size we measured moves. Neither can leave quantity resting.
        """
        return await self._submit(
            plan,
            time_in_force="immediate_or_cancel",
            self_trade_prevention_type="taker_at_cross",
        )

    async def place_resting_order(self, plan: OrderPlan) -> OrderResult:
        """Rest a maker order inside the spread.

        ``post_only`` is what makes this a maker order rather than a hopeful
        one: an order that would cross is **rejected by the venue** instead of
        being silently converted into a taker fill at a worse price. That
        rejection is an expected outcome on a moving book, so it is mapped to a
        zero-fill result like the other benign errors rather than raised.

        ``time_in_force="fill_or_kill"`` is deliberately *not* used here. The
        whole point is to leave quantity resting.
        """
        return await self._submit(
            plan,
            time_in_force="good_till_canceled",
            post_only=True,
            self_trade_prevention_type="maker",
        )

    async def place_exit_order(self, plan: OrderPlan) -> OrderResult:
        """Sell a held position back into the book.

        ``reduce_only`` is the guarantee that matters here: it makes it
        impossible for an exit to overshoot and open a short position in the
        opposite direction, which is the one way an exit path can turn a bounded
        loss into an unbounded one. It also makes the reconciliation above safe
        for this path in particular: even a duplicated exit cannot go short.

        Kalshi's matcher requires reduce-only orders to be IOC.  A moving book
        can therefore produce a partial fill; callers must apply the returned
        fill count to their durable remaining-position state and retry only the
        residual quantity on a later book event.
        """
        return await self._submit(
            plan,
            time_in_force="immediate_or_cancel",
            reduce_only=True,
            self_trade_prevention_type="taker_at_cross",
        )

    async def cancel_order(self, order_id: str, exchange_index: int = 0) -> bool:
        """Cancel a resting order. A already-gone order is a success, not a fault.

        An order that filled or expired between the decision to cancel and the
        call is the common case on a moving book, and treating it as an error
        would turn routine housekeeping into a cycle-aborting exception.
        """
        self._require_authentication()
        if not order_id:
            return False
        try:
            await self._call_with_backoff(
                lambda: self._orders.cancel_order_v2(
                    order_id=order_id,
                    subaccount=self.settings.subaccount,
                    exchange_index=exchange_index,
                )
            )
        except ApiException as exc:
            if getattr(exc, "status", None) in {404, 400}:
                return False
            raise
        return True

    async def get_order(self, order_id: str) -> OrderResult | None:
        """Read a resting order's current fill state."""
        self._require_authentication()
        if not order_id:
            return None
        try:
            response = await self._call_with_backoff(
                lambda: self._orders.get_order(order_id=order_id)
            )
        except ApiException as exc:
            if getattr(exc, "status", None) == 404:
                return None
            raise
        order = getattr(response, "order", response)
        filled = Decimal(str(getattr(order, "fill_count", 0) or 0))
        remaining = Decimal(str(getattr(order, "remaining_count", 0) or 0))
        average = getattr(order, "average_fill_price", None)
        return OrderResult(
            order_id=order_id,
            client_order_id=str(getattr(order, "client_order_id", "") or ""),
            fill_count=filled,
            remaining_count=remaining,
            average_fill_price=Decimal(str(average)) if average is not None else None,
            error_code=str(getattr(order, "status", "") or "") or None,
        )

    @staticmethod
    def is_dead_session(exc: BaseException) -> bool:
        """Whether this exception means the transport died *before* transmitting.

        aiohttp raises ``RuntimeError("Session is closed")`` from ``_request``
        before a byte goes out, and ``ClientConnectorError`` when the connection
        could not be established at all. Both are provably pre-transmission,
        which is what makes retrying them safe even on an order: the venue never
        saw it. Anything ambiguous - a timeout, a reset mid-flight - is
        deliberately excluded and handled by reconciliation instead.
        """
        if isinstance(exc, RuntimeError):
            text = str(exc).lower()
            return "session is closed" in text or "event loop is closed" in text
        return isinstance(
            exc, (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError)
        )

    @staticmethod
    def is_retryable_read_transport_error(exc: BaseException) -> bool:
        """Whether an idempotent GET may be repeated on a fresh transport.

        A pooled keep-alive connection can be closed by the venue between
        requests.  aiohttp then raises ``ClientConnectionResetError`` while it
        is writing the next request's headers ("Cannot write to closing
        transport").  Reads are safe to repeat whether or not the first GET
        reached Kalshi, unlike order submissions, so their recovery boundary is
        intentionally broader than :meth:`is_dead_session`.
        """
        return isinstance(
            exc,
            (
                aiohttp.ClientConnectionError,
                aiohttp.ClientPayloadError,
                asyncio.TimeoutError,
            ),
        )

    @staticmethod
    def read_error_requires_reopen(exc: BaseException) -> bool:
        """Only rebuild a read transport when its connection is actually bad.

        A request timeout is transient and does not imply that the shared pool
        is corrupt. Closing it would cancel unrelated in-flight market reads
        and turn one slow request into an all-asset reconnect storm.
        """
        return isinstance(
            exc,
            (
                aiohttp.ClientConnectionResetError,
                aiohttp.ServerDisconnectedError,
                aiohttp.ClientPayloadError,
            ),
        ) and not isinstance(exc, asyncio.TimeoutError)

    async def _call_with_backoff(self, operation: Any) -> Any:
        """Retry an idempotent operation after throttling or transport failure.

        HTTP retry statuses use bounded exponential backoff.  Connection
        failures additionally replace the SDK's aiohttp pool before retrying;
        this repairs both explicitly closed sessions and stale keep-alive
        sockets. This helper is used for reads and cancellation by an immutable
        order id. Money-bearing create-order requests retain their stricter
        reconcile-before-resend path in :meth:`_submit`.
        """
        async with self._gate:
            for attempt in range(4):
                generation = getattr(self, "_transport_generation", 0)
                try:
                    await self._pace_request()
                    return await asyncio.wait_for(
                        operation(), timeout=KALSHI_READ_ATTEMPT_TIMEOUT_SECONDS
                    )
                except ApiException as exc:
                    status = getattr(exc, "status", None)
                    if status not in RETRYABLE_IDEMPOTENT_HTTP_STATUSES or attempt == 3:
                        raise
                    await asyncio.sleep((0.5 * (2**attempt)) + random.uniform(0, 0.1))
                except BaseException as exc:
                    retryable = self.is_retryable_read_transport_error(exc)
                    # Two bounded attempts are sufficient for a timeout. Four
                    # three-second waits would still freeze a live scan for 12s.
                    timed_out = isinstance(exc, asyncio.TimeoutError)
                    if not retryable or attempt == 3 or (timed_out and attempt >= 1):
                        raise
                    if self.read_error_requires_reopen(exc):
                        await self._reopen_if_stale(generation)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _pace_request(self) -> None:
        """Apply a process-wide request-rate ceiling in addition to concurrency.

        With twelve assets, four fast SDK requests can finish and be replaced
        immediately, so a semaphore alone still emits a rate-limit burst. The
        lock reserves monotonically spaced start times across discovery, book,
        status, and order calls. Direct test doubles without the new setting
        retain zero-delay behavior.
        """
        interval = float(
            getattr(self.settings, "kalshi_request_min_interval_seconds", 0.0)
        )
        if interval <= 0:
            return
        lock = getattr(self, "_pace_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._pace_lock = lock
            self._next_request_at = 0.0
        async with lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            deadline = getattr(self, "_next_request_at", 0.0)
            if deadline > now:
                await asyncio.sleep(deadline - now)
                now = loop.time()
            self._next_request_at = max(now, deadline) + interval

    def _require_authentication(self) -> None:
        if not self.authenticated:
            raise RuntimeError("This Kalshi operation requires authentication")

    async def _get_fee_multiplier(self, series_ticker: str) -> Decimal:
        cached = self._fee_multiplier_cache.get(series_ticker)
        if cached is not None:
            return cached
        response = await self._call_with_backoff(
            lambda: self._markets.get_series(series_ticker=series_ticker)
        )
        multiplier = Decimal(str(response.series.fee_multiplier))
        self._fee_multiplier_cache[series_ticker] = multiplier
        return multiplier

    @staticmethod
    def _as_utc(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


# Backwards-compatible alias for existing imports.
KalshiTradingClient = AsyncKalshiTradingClient


__all__ = [
    "AsyncKalshiTradingClient",
    "BENIGN_ORDER_ERRORS",
    "BookQuote",
    "KalshiMarket",
    "KalshiTradingClient",
    "MarketStatus",
    "OrderPlan",
    "OrderResult",
    "quote_from_orderbook",
]
