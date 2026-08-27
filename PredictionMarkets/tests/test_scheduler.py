import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from loguru import logger
import httpx
import pytest

import bot as bot_module

from bot import (
    EMPTY_DISCOVERY_RECORD_EVERY,
    KalshiCryptoBot,
    TradeCandidate,
    _configure_logging,
    parse_args,
)
from core.strategy_brain.strategies.crypto_15min_predictor import Prediction
from core.strategy_brain.strategies.orderbook import analyse
from execution.decision_log import DecisionLog
from execution.kalshi_client import KalshiMarket, MarketStatus, OrderBookSnapshot
from execution.trade_ledger import TradeLedger, calculate_paper_fill


def test_silent_flag_is_disabled_by_default() -> None:
    assert parse_args([]).silent is False


def test_silent_flag_can_be_enabled() -> None:
    assert parse_args(["--silent"]).silent is True


async def test_slow_cycle_prints_phase_heartbeat_before_summary(monkeypatch) -> None:
    lines = []
    release = asyncio.Event()

    class ScanSink:
        def info(self, template: str, *values) -> None:
            lines.append(template.format(*values))

    async def slow_cycle(*, evaluation_counts=None) -> bool:
        del evaluation_counts
        instance._cycle_phase = "pricing 3 fallback markets"
        await release.wait()
        return False

    instance = object.__new__(KalshiCryptoBot)
    instance.settings = SimpleNamespace(scan_log_interval_seconds=0.01)
    instance._stopping = False
    instance._cycle_phase = "starting"
    instance.run_cycle = slow_cycle
    monkeypatch.setattr(bot_module, "_scan", ScanSink())

    task = asyncio.create_task(instance._run_cycle_with_status())
    await asyncio.sleep(0.025)
    release.set()

    assert await task is False
    assert any("scan  working" in line for line in lines)
    assert any("pricing 3 fallback markets" in line for line in lines)
    assert instance._cycle_phase == "idle"
    assert instance._completed_cycles == 1


async def test_cycle_heartbeat_reports_stream_health(monkeypatch) -> None:
    lines = []
    release = asyncio.Event()

    class ScanSink:
        def info(self, template: str, *values) -> None:
            lines.append(template.format(*values))

    async def slow_cycle(*, evaluation_counts=None) -> bool:
        del evaluation_counts
        await release.wait()
        return False

    instance = object.__new__(KalshiCryptoBot)
    instance.settings = SimpleNamespace(scan_log_interval_seconds=0.01)
    instance._stopping = False
    instance._cycle_phase = "evaluating 12 markets"
    instance._completed_cycles = 3
    instance.run_cycle = slow_cycle
    instance.price_stream = SimpleNamespace(
        health=lambda: {"live": True, "seconds_since_message": 0.5}
    )
    instance.kalshi_book_stream = SimpleNamespace(
        health=lambda: {"books": 12, "connected": True}
    )
    instance._report_mode = lambda: None
    monkeypatch.setattr(bot_module, "_scan", ScanSink())

    task = asyncio.create_task(instance._run_cycle_with_status())
    await asyncio.sleep(0.025)
    release.set()

    await task
    assert any("pass 4" in line for line in lines)
    assert any("Kraken live age=0.5s" in line for line in lines)
    assert any("books=12 connected=yes" in line for line in lines)


async def test_price_feed_timeout_enters_asset_cooldown(monkeypatch) -> None:
    async def never_returns(symbol, *, limit):
        del symbol, limit
        await asyncio.Event().wait()

    instance = object.__new__(KalshiCryptoBot)
    instance.prices = SimpleNamespace(get_one_minute_candles=never_returns)
    instance.settings = SimpleNamespace(order_failure_cooldown_seconds=60)
    instance._price_feed_failed_until = {}
    monkeypatch.setattr(bot_module, "PRICE_FEED_READ_TIMEOUT_SECONDS", 0.01)

    before = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await instance._get_price_candles("GOLD", limit=600)

    assert instance._price_feed_failed_until["GOLD"] >= before + 59
    with pytest.raises(RuntimeError, match="retry cooldown"):
        await instance._get_price_candles("GOLD", limit=600)


async def test_event_entry_is_single_flight_per_ticker() -> None:
    target = market("KXGOLD15M-SINGLE", "GOLD")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def event_check(market, analysis) -> None:
        del analysis
        calls.append(market.ticker)
        started.set()
        await release.wait()

    instance = object.__new__(KalshiCryptoBot)
    instance._stopping = False
    instance.take_profit = None
    instance.settings = SimpleNamespace(
        event_driven_entry_enabled=True,
        predictor_enabled=True,
        event_entry_min_interval_seconds=0.001,
    )
    instance._current_markets = {target.ticker: target}
    instance._halt_reason = None
    instance._event_entry_last = {}
    instance._event_entry_global_last = 0.0
    instance._event_entry_tasks = set()
    instance._event_entry_inflight = set()
    instance._event_entry_failed_until = {}
    instance._event_entry_check = event_check
    analysis = SimpleNamespace()

    await instance._on_kalshi_book(target.ticker, analysis)
    await asyncio.wait_for(started.wait(), timeout=1)
    await instance._on_kalshi_book(target.ticker, analysis)
    release.set()
    await asyncio.gather(*instance._event_entry_tasks)

    assert calls == [target.ticker]
    assert instance._event_entry_inflight == set()


async def test_event_feed_timeout_enters_cooldown_without_escaping() -> None:
    target = market("KXGOLD15M-TIMEOUT", "GOLD")

    async def fail(market, cycle_id):
        del market, cycle_id
        raise httpx.ConnectTimeout("Yahoo connect timeout")

    instance = object.__new__(KalshiCryptoBot)
    instance._stopping = False
    instance.settings = SimpleNamespace(
        minimum_seconds_to_close=20,
        entry_window_seconds=600,
        order_failure_cooldown_seconds=60,
    )
    instance._evaluate_market = fail
    instance._event_entry_failed_until = {}
    analysis = SimpleNamespace(
        yes_bid=Decimal("0.45"),
        yes_ask=Decimal("0.55"),
        yes_bid_size=Decimal("10"),
        no_bid_size=Decimal("10"),
    )
    before = asyncio.get_running_loop().time()

    await instance._event_entry_check(target, analysis)

    assert instance._event_entry_failed_until[target.ticker] >= before + 59


def market(ticker: str, asset: str = "BTC") -> KalshiMarket:
    now = datetime.now(timezone.utc)
    return KalshiMarket(
        asset=asset,
        ticker=ticker,
        event_ticker=ticker.rsplit("-", 1)[0],
        title=f"{asset} up?",
        target_price=Decimal("100"),
        open_time=now - timedelta(minutes=10),
        close_time=now + timedelta(minutes=5),
        yes_bid=Decimal("0.45"),
        yes_ask=Decimal("0.55"),
        yes_bid_size=Decimal("100"),
        yes_ask_size=Decimal("100"),
        fee_multiplier=Decimal("1"),
    )


def candidate(target: KalshiMarket, edge: str) -> TradeCandidate:
    probability = Decimal("0.75")
    return TradeCandidate(
        market=target,
        prediction=Prediction(
            probability_yes=probability,
            current_spot=Decimal("101"),
            target_price=Decimal("100"),
            minutes_remaining=Decimal("2"),
            distance_return=Decimal("0.01"),
            momentum_return=Decimal("0.001"),
            volatility_per_minute=Decimal("0.002"),
        ),
        outcome="yes",
        outcome_probability=probability,
        top_of_book_price=Decimal("0.55"),
        buffered_price=Decimal("0.58"),
        edge=Decimal(edge),
    )


def test_canonical_market_touch_is_derived_from_the_same_depth_snapshot() -> None:
    original = market("KXBTC15M-CANONICAL")
    yes = ((Decimal("0.60"), Decimal("44")),)
    no = ((Decimal("0.38"), Decimal("55")),)
    snapshot = OrderBookSnapshot(
        ticker=original.ticker,
        observed_at=datetime.now(timezone.utc),
        yes_levels=yes,
        no_levels=no,
        analysis=analyse(yes, no),
        source="websocket",
        sequence=9,
    )

    updated = KalshiCryptoBot._market_with_book(original, snapshot)

    assert (updated.yes_bid, updated.yes_ask) == (Decimal("0.60"), Decimal("0.62"))
    assert (updated.yes_bid_size, updated.yes_ask_size) == (
        Decimal("44"),
        Decimal("55"),
    )


async def test_rest_depth_reconciliation_never_blocks_the_trade_cycle() -> None:
    target = market("KXBTC15M-BACKGROUND")
    refresh_started = asyncio.Event()
    allow_refresh = asyncio.Event()

    async def active_markets():
        return [target]

    async def refresh_book(ticker):
        refresh_started.set()
        await allow_refresh.wait()
        return None

    async def evaluate(item, cycle_id):
        return None

    async def no_op(*args, **kwargs):
        return None

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot._safe_mode = False
    bot._update_safe_mode_from_ledger = lambda: None
    bot.settings = SimpleNamespace(
        environment="production",
        assets=("BTC",),
        market_data_enabled=True,
        orderbook_recording_assets=("BTC",),
        orderbook_rest_reconcile_seconds=30.0,
        kalshi_websocket_stale_seconds=10.0,
        pair_arb_enabled=False,
        predictor_enabled=True,
        scalp_enabled=False,
        predictor_max_candidates_per_cycle=1,
    )
    bot.client = SimpleNamespace(
        get_active_markets=active_markets,
        get_orderbook_snapshot=refresh_book,
    )
    bot.kalshi_book_stream = None
    bot.decisions = DecisionLog(Path("unused.jsonl"), enabled=False)
    bot._evaluate_market = evaluate
    bot._record_data_pass = no_op
    bot._service_positions_pass = no_op
    bot._log_scan = lambda selected: None
    bot._rest_book_cache = {}
    bot._rest_book_refresh_task = None
    bot._last_rest_book_refresh = 0.0

    assert await bot.run_cycle() is False
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    assert bot._rest_book_refresh_task is not None
    assert not bot._rest_book_refresh_task.done()
    bot._rest_book_refresh_task.cancel()
    await asyncio.gather(bot._rest_book_refresh_task, return_exceptions=True)


def open_trade(
    ledger: TradeLedger,
    *,
    check_time: datetime,
    close_time: datetime,
):
    fill = calculate_paper_fill(Decimal("10"), Decimal("0.50"))
    return ledger.record_open_trade(
        mode="paper",
        environment="production",
        asset="BTC",
        ticker=f"KXBTC15M-{check_time.timestamp()}",
        event_ticker="KXBTC15M",
        order_id="paper-order",
        client_order_id=f"client-{check_time.timestamp()}",
        outcome="yes",
        placed_at=check_time - timedelta(minutes=10),
        market_open_time=close_time - timedelta(minutes=15),
        market_close_time=close_time,
        status_check_time=check_time,
        contracts=fill.contracts,
        entry_price=fill.price,
        entry_cost=fill.entry_cost,
        fees_paid=fill.fees_paid,
        total_debit=fill.total_debit,
        target_price=Decimal("100"),
        model_probability=Decimal("0.70"),
        model_edge=Decimal("0.10"),
    )


async def test_cycle_places_only_highest_edge_candidate() -> None:
    first_market = market("KXBTC15M-FIRST")
    second_market = market("KXETH15M-SECOND", "ETH")
    candidates = {
        first_market.ticker: candidate(first_market, "0.08"),
        second_market.ticker: candidate(second_market, "0.15"),
    }
    selected = []

    async def active_markets():
        return [first_market, second_market]

    async def evaluate(item, cycle_id):
        return candidates[item.ticker]

    async def place(item):
        selected.append(item)
        return True

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot.decisions = DecisionLog(Path("unused.jsonl"), enabled=False)
    bot.client = SimpleNamespace(get_active_markets=active_markets)
    bot._evaluate_market = evaluate
    bot._place_candidate = place

    assert await bot.run_cycle() is True
    assert len(selected) == 1
    assert selected[0].market.ticker == second_market.ticker


async def test_cycle_prefers_consensus_tail_over_larger_ordinary_edge() -> None:
    first_market = market("KXBTC15M-CONSENSUS")
    second_market = market("KXETH15M-ORDINARY", "ETH")
    candidates = {
        first_market.ticker: replace(
            candidate(first_market, "0.08"), consensus_tail=True
        ),
        second_market.ticker: candidate(second_market, "0.20"),
    }
    selected = []

    async def active_markets():
        return [first_market, second_market]

    async def evaluate(item, cycle_id):
        return candidates[item.ticker]

    async def place(item):
        selected.append(item)
        return True

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot.decisions = DecisionLog(Path("unused.jsonl"), enabled=False)
    bot.client = SimpleNamespace(get_active_markets=active_markets)
    bot._evaluate_market = evaluate
    bot._place_candidate = place

    assert await bot.run_cycle() is True
    assert selected[0].market.ticker == first_market.ticker


async def test_cycle_can_place_two_uncorrelated_candidates() -> None:
    crypto = market("KXBTC15M-CRYPTO", "BTC")
    energy = market("KXWTI15M-ENERGY", "WTI")
    selected = []

    async def active_markets():
        return [crypto, energy]

    async def evaluate(item, cycle_id):
        return candidate(item, "0.10")

    async def place(item):
        selected.append(item.market.asset)
        return True

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot.settings = SimpleNamespace(
        environment="production",
        assets=("BTC", "WTI"),
        predictor_max_candidates_per_cycle=2,
    )
    bot.decisions = DecisionLog(Path("unused.jsonl"), enabled=False)
    bot.client = SimpleNamespace(get_active_markets=active_markets)
    bot._evaluate_market = evaluate
    bot._place_candidate = place

    assert await bot.run_cycle() is True
    assert set(selected) == {"BTC", "WTI"}


async def test_event_entry_reuses_the_normal_evaluator_with_updated_book() -> None:
    target = replace(
        market("KXBTC15M-EVENT"),
        close_time=datetime.now(timezone.utc) + timedelta(seconds=120),
    )
    seen = []
    placed = []

    async def evaluate(updated, cycle_id):
        seen.append((updated, cycle_id))
        return candidate(updated, "0.08")

    async def place(item):
        placed.append(item)
        return True

    bot = object.__new__(KalshiCryptoBot)
    bot.settings = SimpleNamespace(
        minimum_seconds_to_close=20, entry_window_seconds=180
    )
    bot._evaluate_market = evaluate
    bot._place_candidate = place
    analysis = SimpleNamespace(
        yes_bid=Decimal("0.61"),
        yes_ask=Decimal("0.63"),
        yes_bid_size=Decimal("44"),
        no_bid_size=Decimal("55"),
    )

    await bot._event_entry_check(target, analysis)

    updated, cycle_id = seen[0]
    assert cycle_id.startswith("event-")
    assert (updated.yes_bid, updated.yes_ask) == (Decimal("0.61"), Decimal("0.63"))
    assert (updated.yes_bid_size, updated.yes_ask_size) == (
        Decimal("44"),
        Decimal("55"),
    )
    assert len(placed) == 1


def _discovery_bot(tmp_path: Path, markets: list[KalshiMarket]) -> KalshiCryptoBot:
    async def active_markets():
        return markets

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot.mode = "live"
    bot.settings = SimpleNamespace(environment="production", assets=("BTC",))
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    bot.client = SimpleNamespace(get_active_markets=active_markets)
    return bot


async def test_empty_discovery_leaves_a_durable_record(tmp_path: Path) -> None:
    """A blind bot must not look identical to one that simply found no edge."""
    bot = _discovery_bot(tmp_path, [])

    assert await bot.run_cycle() is False

    records = bot.decisions.read_all()
    assert [r["record_type"] for r in records] == ["discovery_empty"]
    assert records[0]["consecutive_empty_cycles"] == 1
    assert records[0]["mode"] == "live"


async def test_empty_discovery_records_are_rate_limited(tmp_path: Path) -> None:
    bot = _discovery_bot(tmp_path, [])

    for _ in range(EMPTY_DISCOVERY_RECORD_EVERY * 2):
        await bot.run_cycle()

    counts = [
        r["consecutive_empty_cycles"]
        for r in bot.decisions.read_all()
        if r["record_type"] == "discovery_empty"
    ]
    assert counts == [1, EMPTY_DISCOVERY_RECORD_EVERY, EMPTY_DISCOVERY_RECORD_EVERY * 2]


async def test_discovery_outage_retains_current_market_for_supervision_only(
    tmp_path: Path,
) -> None:
    current = market("KXBTC15M-CACHED")
    serviced = []
    placed = []

    async def no_markets():
        return []

    async def evaluate(item, cycle_id):
        del cycle_id
        return candidate(item, "0.10")

    async def record(*args, **kwargs):
        del args, kwargs

    async def service(markets, snapshots):
        del snapshots
        serviced.extend(markets)

    async def place(item):
        placed.append(item)
        return True

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot._safe_mode = False
    bot._update_safe_mode_from_ledger = lambda: None
    bot._current_markets = {current.ticker: current}
    bot._discovery_client = SimpleNamespace(get_active_markets=no_markets)
    bot.client = SimpleNamespace()
    bot.settings = SimpleNamespace(
        environment="production",
        assets=("BTC",),
        market_data_enabled=False,
        pair_arb_enabled=False,
        predictor_enabled=True,
        scalp_enabled=False,
        predictor_max_candidates_per_cycle=1,
    )
    bot.kalshi_book_stream = None
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    bot._scan_verdicts = {}
    bot._evaluate_market = evaluate
    bot._record_data_pass = record
    bot._service_positions_pass = service
    bot._place_candidate = place
    bot._log_scan = lambda selected: None

    assert await bot.run_cycle() is False
    assert bot._discovery_degraded is True
    assert [item.ticker for item in serviced] == [current.ticker]
    assert placed == []


async def test_partial_discovery_retains_only_failed_assets_for_supervision(
    tmp_path: Path,
) -> None:
    fresh = market("KXBTC15M-FRESH")
    cached = market("KXGOLD15M-CACHED", "GOLD")
    serviced = []
    placed = []

    async def active_markets():
        return [fresh]

    discovery = SimpleNamespace(
        get_active_markets=active_markets,
        last_discovery_failed_assets=frozenset({"GOLD"}),
    )

    async def evaluate(item, cycle_id):
        del cycle_id
        return candidate(item, "0.10") if item.asset == "BTC" else None

    async def record(*args, **kwargs):
        del args, kwargs

    async def service(markets, snapshots):
        del snapshots
        serviced.extend(markets)

    async def place(item):
        placed.append(item.market.asset)
        return True

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot._safe_mode = False
    bot._update_safe_mode_from_ledger = lambda: None
    bot._current_markets = {cached.ticker: cached}
    bot._discovery_client = discovery
    bot.client = SimpleNamespace()
    bot.settings = SimpleNamespace(
        environment="production",
        assets=("BTC", "GOLD"),
        market_data_enabled=False,
        pair_arb_enabled=False,
        predictor_enabled=True,
        scalp_enabled=False,
        predictor_max_candidates_per_cycle=1,
    )
    bot.kalshi_book_stream = None
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    bot._scan_verdicts = {}
    bot._evaluate_market = evaluate
    bot._record_data_pass = record
    bot._service_positions_pass = service
    bot._place_candidate = place
    bot._log_scan = lambda selected: None

    assert await bot.run_cycle() is True
    assert bot._discovery_degraded is False
    assert bot._degraded_discovery_assets == {"GOLD"}
    assert {item.asset for item in serviced} == {"BTC", "GOLD"}
    assert placed == ["BTC"]


async def test_discovery_degraded_guard_blocks_event_placement(tmp_path: Path) -> None:
    target = market("KXBTC15M-DEGRADED-EVENT")
    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._discovery_degraded = True
    bot.mode = "live"
    bot.settings = SimpleNamespace(environment="production")
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")

    assert await bot._place_candidate_locked(candidate(target, "0.10")) is False

    records = bot.decisions.read_all()
    assert records[-1]["blocked_by"] == "fresh_market_discovery"


async def test_discovery_recovery_is_recorded_and_resets(tmp_path: Path) -> None:
    empty = _discovery_bot(tmp_path, [])
    await empty.run_cycle()
    await empty.run_cycle()

    recovered = market("KXBTC15M-RECOVERED")

    async def active_markets():
        return [recovered]

    async def evaluate(item, cycle_id):
        return None

    empty.client = SimpleNamespace(get_active_markets=active_markets)
    empty._evaluate_market = evaluate

    assert await empty.run_cycle() is False
    assert empty._empty_discovery_cycles == 0
    recovery = [
        r
        for r in empty.decisions.read_all()
        if r["record_type"] == "discovery_recovered"
    ]
    assert len(recovery) == 1
    assert recovery[0]["empty_cycles"] == 2


def test_silent_keeps_material_events_visible() -> None:
    """--silent drops scan chatter; fills, settlements and halts must survive."""
    levels: list[str] = []

    def fake_add(_sink, **kwargs):
        levels.append(kwargs["level"])
        return 1

    original_add, original_remove = logger.add, logger.remove
    logger.add, logger.remove = fake_add, lambda *a, **k: None
    try:
        _configure_logging(silent=True)
    finally:
        logger.add, logger.remove = original_add, original_remove

    assert levels == ["WARNING"]


async def test_open_trade_backs_off_without_polling_before_1455(tmp_path: Path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    now = datetime.now(timezone.utc)
    trade = open_trade(
        ledger,
        check_time=now + timedelta(minutes=1),
        close_time=now + timedelta(minutes=1, seconds=5),
    )
    waited = []

    class NoPollClient:
        async def get_market_status(self, ticker):
            raise AssertionError(f"Unexpected early status poll for {ticker}")

    bot = object.__new__(KalshiCryptoBot)
    bot.client = NoPollClient()
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    bot.ledger = ledger
    bot.live = False
    bot.settings = SimpleNamespace(
        environment="production",
        settlement_poll_seconds=2,
        trade_log_path=tmp_path / "audit.jsonl",
    )

    async def record_wait(seconds):
        waited.append(seconds)

    bot._interruptible_wait = record_wait

    await bot._monitor_open_trade(trade, once=False)
    ledger.close()

    assert len(waited) == 1
    assert Decimal("55") < waited[0] <= Decimal("60")


async def test_final_market_result_settles_recovered_trade(tmp_path: Path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    now = datetime.now(timezone.utc)
    trade = open_trade(
        ledger,
        check_time=now - timedelta(seconds=10),
        close_time=now - timedelta(seconds=5),
    )

    bot = object.__new__(KalshiCryptoBot)

    async def final_status(ticker):
        return MarketStatus(
            ticker=ticker,
            status="finalized",
            result="yes",
            settlement_value=Decimal("101"),
            settlement_time=now,
        )

    bot.client = SimpleNamespace(get_market_status=final_status)
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    bot.ledger = ledger
    bot.live = False
    bot.settings = SimpleNamespace(
        environment="production",
        settlement_poll_seconds=2,
        trade_log_path=tmp_path / "audit.jsonl",
    )

    await bot._monitor_open_trade(trade, once=True)
    settled = ledger.get_trade(trade.trade_id)
    ledger.close()

    assert settled is not None
    assert settled.status == "WON"
    assert settled.market_result == "yes"
    assert settled.pnl == Decimal("4.82")
