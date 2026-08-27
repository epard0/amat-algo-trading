"""What the bot says out loud, and what it writes down when a write fails.

The console is a safety device here, not decoration: an operator who cannot see
a fill cannot tell a working bot from a blind one, and an operator drowning in
ten gate lines per asset per five seconds sees neither. These tests hold the
line count down and the important lines in.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger

import bot as bot_module
from bot import KalshiCryptoBot, TradeCandidate, _strict_signal_payload, parse_args
from core.strategy_brain.strategies.crypto_15min_predictor import Prediction
from execution.decision_log import DecisionLog
from execution.kalshi_client import BookQuote, KalshiMarket, MarketStatus, OrderPlan
from execution.kalshi_client import OrderResult
from execution.risk_gate import GateResult
from execution.run_log import (
    COUNTDOWN_SECONDS,
    KIND_EVENT,
    KIND_SCAN,
    KIND_STATS,
    KIND_WAIT,
    LEDGER_AUDIT_NAME,
    HourlyClock,
    LedgerAudit,
    PeriodCounters,
    console_admits,
)
from execution.trade_ledger import TradeLedger, calculate_paper_fill


class Recorder:
    """Collects records the way a console sink would see them."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str | None, str]] = []

    def __call__(self, message) -> None:
        record = message.record
        self.records.append(
            (
                record["level"].name,
                record["extra"].get("kind"),
                record["message"],
            )
        )

    def of_kind(self, kind: str) -> list[str]:
        return [text for _, tag, text in self.records if tag == kind]

    @property
    def console_lines(self) -> list[str]:
        """What a quiet console would show: tagged lines, plus warnings."""
        levels = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
        return [
            text
            for level, tag, text in self.records
            if console_admits(level_no=levels[level], kind=tag)
        ]


def test_probability_decision_normalizes_for_strict_signal_ledger() -> None:
    payload = {
        "record_type": "decision",
        "cycle_id": "cycle-7",
        "timestamp": "2026-08-24T22:00:00+00:00",
        "asset": "SILVER",
        "ticker": "KXSILVER15M-TEST",
        "decision": "skipped",
        "decision_reason": "probability below floor",
        "blocked_by": "min_probability",
        "seconds_to_close": 90,
        "prediction": {"probability_yes": "0.21", "regime": "TREND"},
        "market": {"implied_probability_yes": "0.78"},
        "candidate": {
            "outcome": "no",
            "top_of_book_price": "0.23",
            "edge": "0.56",
        },
        "thresholds": {"min_probability": "0.72"},
        "gates": [{"name": "min_probability", "allowed": False}],
    }

    normalized = _strict_signal_payload(payload)

    assert normalized is not None
    assert normalized["model_probability"] == Decimal("0.21")
    assert normalized["market_probability"] == Decimal("0.78")
    assert normalized["outcome"] == "no"
    assert normalized["executable_price"] == Decimal("0.23")
    assert normalized["net_edge"] == Decimal("0.56")
    assert normalized["verdict"] == "blocked"
    assert normalized["gate"] == "min_probability"


def test_clock_only_decision_is_not_forced_into_probability_schema() -> None:
    assert (
        _strict_signal_payload(
            {
                "record_type": "decision",
                "ticker": "KXBTC15M-TEST",
                "decision": "skipped",
                "blocked_by": "entry_window",
            }
        )
        is None
    )


@pytest.fixture
def recorder():
    sink = Recorder()
    logger.remove()
    handle = logger.add(sink, level="DEBUG", format="{message}")
    yield sink
    logger.remove(handle)


def market(ticker: str, asset: str = "BTC") -> KalshiMarket:
    now = datetime.now(timezone.utc)
    return KalshiMarket(
        asset=asset,
        ticker=ticker,
        event_ticker=ticker.rsplit("-", 1)[0],
        title=f"{asset} up?",
        target_price=Decimal("100"),
        open_time=now - timedelta(minutes=13),
        close_time=now + timedelta(minutes=2),
        yes_bid=Decimal("0.58"),
        yes_ask=Decimal("0.62"),
        yes_bid_size=Decimal("100"),
        yes_ask_size=Decimal("100"),
        fee_multiplier=Decimal("1"),
    )


def prediction() -> Prediction:
    return Prediction(
        probability_yes=Decimal("0.70"),
        current_spot=Decimal("101"),
        target_price=Decimal("100"),
        minutes_remaining=Decimal("2"),
        distance_return=Decimal("0.01"),
        momentum_return=Decimal("0.001"),
        volatility_per_minute=Decimal("0.002"),
    )


def candidate(target: KalshiMarket) -> TradeCandidate:
    return TradeCandidate(
        market=target,
        prediction=prediction(),
        outcome="yes",
        outcome_probability=Decimal("0.70"),
        top_of_book_price=Decimal("0.60"),
        buffered_price=Decimal("0.63"),
        edge=Decimal("0.07"),
        edge_floor=Decimal("0.05"),
    )


def plan_for(target: KalshiMarket) -> OrderPlan:
    return OrderPlan(
        ticker=target.ticker,
        outcome="yes",
        sdk_book_side="bid",
        sdk_yes_leg_price=Decimal("0.61"),
        outcome_limit_price=Decimal("0.61"),
        contracts=Decimal("10"),
        maximum_cost=Decimal("6.10"),
        fee_reserve=Decimal("0.20"),
        maximum_total_debit=Decimal("6.30"),
        client_order_id="client-1",
        sizing_fraction=Decimal("0.12"),
    )


def placing_bot(tmp_path: Path, *, live: bool = True) -> KalshiCryptoBot:
    """A bot wired far enough to reach the ledger write and no further."""
    target = market("KXBTC15M-PLACE")
    bot = object.__new__(KalshiCryptoBot)
    bot.live = live
    bot.mode = "live" if live else "paper"
    bot.ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    bot._cooldowns = {}
    bot._halt_reason = None
    bot._peak_balance = Decimal("0")
    bot._ledger_audit = LedgerAudit(tmp_path / LEDGER_AUDIT_NAME)
    bot.settings = SimpleNamespace(
        environment="production",
        min_edge=Decimal("0.05"),
        max_edge=Decimal("0.20"),
        max_quote_improvement=Decimal("0.05"),
        fee_reserve_per_contract=Decimal("0.02"),
        live_capital_limit_usd=Decimal("10"),
        order_failure_cooldown_seconds=60,
        status_check_lead_seconds=5,
        trade_log_path=tmp_path / "trades.jsonl",
    )

    async def already_traded(ticker):
        return False

    async def get_quote(ticker, outcome):
        return BookQuote(
            outcome="yes",
            best_price=Decimal("0.60"),
            limit_price=Decimal("0.61"),
            depth_at_limit=Decimal("500"),
        )

    async def balance(exchange_index=None):
        return Decimal("50.00")

    async def place_order(plan):
        return OrderResult(
            order_id="venue-order-99",
            client_order_id=plan.client_order_id,
            fill_count=Decimal("10"),
            remaining_count=Decimal("0"),
            average_fill_price=Decimal("0.61"),
        )

    bot.client = SimpleNamespace(
        already_traded=already_traded,
        get_quote=get_quote,
        get_available_balance=balance,
        place_order=place_order,
        build_order_plan=lambda **kwargs: plan_for(target),
    )

    async def allow():
        return GateResult(True)

    bot._check_capital_breaker = allow
    return bot


# ------------------------------------------------------------- the console --


async def test_a_whole_pass_is_one_console_line(recorder, tmp_path: Path) -> None:
    """Ten gates across five assets used to be fifty lines every five seconds."""
    markets = [
        market("KXBTC15M-A", "BTC"),
        market("KXETH15M-B", "ETH"),
        market("KXSOL15M-C", "SOL"),
    ]

    async def active_markets():
        return markets

    async def evaluate(item, cycle_id):
        bot._note_refusal(item, "min_edge", "no-edge 0.011")
        return None

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot.mode = "paper"
    bot.settings = SimpleNamespace(environment="production", assets=("BTC",))
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl", enabled=False)
    bot.client = SimpleNamespace(get_active_markets=active_markets)
    bot._evaluate_market = evaluate

    assert await bot.run_cycle() is False

    scans = recorder.of_kind(KIND_SCAN)
    assert len(scans) == 1
    assert scans[0] == (
        "scan  BTC no-edge 0.011 | ETH no-edge 0.011 | SOL no-edge 0.011"
    )
    assert recorder.console_lines == scans


async def test_the_selected_candidate_is_named_in_the_same_line(
    recorder,
    tmp_path: Path,
) -> None:
    first = market("KXBTC15M-A", "BTC")
    second = market("KXETH15M-B", "ETH")

    async def active_markets():
        return [first, second]

    async def evaluate(item, cycle_id):
        if item is first:
            bot._note_refusal(item, "min_edge", "no-edge 0.011")
            return None
        bot.scan_verdicts[item.ticker] = (item.asset, "edge 0.070")
        return candidate(item)

    async def place(chosen):
        return True

    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._empty_discovery_cycles = 0
    bot.mode = "paper"
    bot.settings = SimpleNamespace(environment="production", assets=("BTC",))
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl", enabled=False)
    bot.client = SimpleNamespace(get_active_markets=active_markets)
    bot._evaluate_market = evaluate
    bot._place_candidate = place

    assert await bot.run_cycle() is True

    assert recorder.of_kind(KIND_SCAN) == ["scan  BTC no-edge 0.011 | >ETH edge 0.070"]


def test_a_gate_refusal_is_counted_for_the_hour() -> None:
    bot = object.__new__(KalshiCryptoBot)

    bot._note_refusal(market("KXBTC15M-A"), "model_market_divergence", "divergence")

    assert bot.period.top_gate() == ("model_market_divergence", 1)


def test_verbose_and_silent_are_opposites_at_the_command_line() -> None:
    assert parse_args([]).verbose is False
    assert parse_args(["--verbose"]).verbose is True
    assert parse_args(["--silent"]).silent is True
    with pytest.raises(SystemExit):
        parse_args(["--silent", "--verbose"])


# ------------------------------------------------------------- the waiting --


def waiting_bot(*, tty: bool, monkeypatch) -> tuple[KalshiCryptoBot, list[float]]:
    slept: list[float] = []
    bot = object.__new__(KalshiCryptoBot)
    bot._stopping = False
    bot._wake_event = asyncio.Event()

    async def sleep(timeout: float) -> None:
        slept.append(timeout)

    bot._sleep_until_woken = sleep
    monkeypatch.setattr(bot_module, "_console_is_interactive", lambda: tty)
    return bot, slept


async def test_a_wait_is_silent_until_its_last_five_seconds(
    recorder,
    monkeypatch,
) -> None:
    bot, slept = waiting_bot(tty=True, monkeypatch=monkeypatch)
    bot._countdown_label = "status check"

    await bot._interruptible_wait(Decimal("60"))

    assert slept == [55.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert recorder.of_kind(KIND_WAIT) == [
        "5s to status check",
        "4s to status check",
        "3s to status check",
        "2s to status check",
        "1s to status check",
    ]


async def test_a_log_file_gets_one_countdown_line_not_five(
    recorder,
    monkeypatch,
) -> None:
    """Five lines a wait is a terminal courtesy and a log file's noise."""
    bot, slept = waiting_bot(tty=False, monkeypatch=monkeypatch)
    bot._countdown_label = "close"

    await bot._interruptible_wait(Decimal("60"))

    assert slept == [55.0, 5.0]
    assert recorder.of_kind(KIND_WAIT) == ["5s to close"]


async def test_no_countdown_line_carries_an_ansi_escape(
    recorder,
    monkeypatch,
) -> None:
    bot, _ = waiting_bot(tty=True, monkeypatch=monkeypatch)
    bot._countdown_label = "close"

    await bot._interruptible_wait(Decimal("30"))

    assert all("\x1b" not in line for line in recorder.of_kind(KIND_WAIT))


async def test_an_unarmed_wait_stays_a_single_sleep(recorder, monkeypatch) -> None:
    bot, slept = waiting_bot(tty=True, monkeypatch=monkeypatch)

    await bot._interruptible_wait(Decimal("5"))

    assert slept == [5.0]
    assert recorder.of_kind(KIND_WAIT) == []


async def test_a_countdown_shorter_than_its_lead_is_not_counted(
    recorder,
    monkeypatch,
) -> None:
    bot, slept = waiting_bot(tty=True, monkeypatch=monkeypatch)
    bot._countdown_label = "close"

    await bot._interruptible_wait(Decimal(str(COUNTDOWN_SECONDS)))

    assert slept == [5.0]
    assert recorder.of_kind(KIND_WAIT) == []


async def test_stopping_mid_countdown_returns_immediately(monkeypatch) -> None:
    """The existing early-return-on-stop contract survives the countdown."""
    bot, slept = waiting_bot(tty=True, monkeypatch=monkeypatch)
    bot._countdown_label = "close"

    async def sleep(timeout: float) -> None:
        slept.append(timeout)
        bot._stopping = True

    bot._sleep_until_woken = sleep

    await bot._interruptible_wait(Decimal("60"))

    assert slept == [55.0]


# ------------------------------------------------------------ ledger audit --


async def test_every_ledger_write_lands_in_the_audit_log(tmp_path: Path) -> None:
    bot = placing_bot(tmp_path, live=False)

    assert await bot._place_candidate(candidate(market("KXBTC15M-PLACE"))) is True
    bot.ledger.close()

    audit = (tmp_path / LEDGER_AUDIT_NAME).read_text().splitlines()
    assert len(audit) == 1
    assert "method=record_open_trade" in audit[0]
    assert "ticker=KXBTC15M-PLACE" in audit[0]
    assert "mode=paper" in audit[0]
    assert "contracts=10" in audit[0]
    assert "status=OPEN" in audit[0]


async def test_a_live_fill_with_no_ledger_row_is_screamed_about(
    recorder,
    tmp_path: Path,
) -> None:
    """A funded position the ledger never saw is the one unrecoverable state."""
    bot = placing_bot(tmp_path, live=True)

    def explode(**kwargs):
        raise RuntimeError("disk I/O error")

    bot.ledger.record_open_trade = explode

    with pytest.raises(RuntimeError):
        await bot._place_candidate(candidate(market("KXBTC15M-PLACE")))
    bot.ledger.close()

    audit = (tmp_path / LEDGER_AUDIT_NAME).read_text()
    assert "| ERROR |" in audit
    assert "order_id=venue-order-99" in audit
    assert "contracts=10" in audit
    assert "RuntimeError: disk I/O error" in audit
    assert any(level == "ERROR" for level, _, _ in recorder.records)
    # A funded position nobody is monitoring must not be traded around.
    assert bot._halt_reason is not None


async def test_a_paper_ledger_failure_does_not_halt_the_run(tmp_path: Path) -> None:
    bot = placing_bot(tmp_path, live=False)

    def explode(**kwargs):
        raise RuntimeError("disk I/O error")

    bot.ledger.record_open_trade = explode

    with pytest.raises(RuntimeError):
        await bot._place_candidate(candidate(market("KXBTC15M-PLACE")))
    bot.ledger.close()

    assert bot._halt_reason is None
    assert "| ERROR |" in (tmp_path / LEDGER_AUDIT_NAME).read_text()


def monitoring_bot(tmp_path: Path, ledger: TradeLedger) -> KalshiCryptoBot:
    bot = object.__new__(KalshiCryptoBot)
    bot.ledger = ledger
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    bot.live = False
    bot.mode = "paper"
    bot._ledger_audit = LedgerAudit(tmp_path / LEDGER_AUDIT_NAME)
    bot.settings = SimpleNamespace(
        environment="production",
        settlement_poll_seconds=2,
        trade_log_path=tmp_path / "trades.jsonl",
    )
    return bot


def open_trade(ledger: TradeLedger, *, close_time: datetime):
    fill = calculate_paper_fill(Decimal("10"), Decimal("0.50"))
    return ledger.record_open_trade(
        mode="paper",
        environment="production",
        asset="BTC",
        ticker="KXBTC15M-SETTLE",
        event_ticker="KXBTC15M",
        order_id="paper-order",
        client_order_id="client-settle",
        outcome="yes",
        placed_at=close_time - timedelta(minutes=13),
        market_open_time=close_time - timedelta(minutes=15),
        market_close_time=close_time,
        status_check_time=close_time - timedelta(seconds=5),
        contracts=fill.contracts,
        entry_price=fill.price,
        entry_cost=fill.entry_cost,
        fees_paid=fill.fees_paid,
        total_debit=fill.total_debit,
        target_price=Decimal("100"),
        model_probability=Decimal("0.70"),
        model_edge=Decimal("0.10"),
    )


async def test_settling_a_trade_twice_writes_one_audit_event(
    tmp_path: Path,
) -> None:
    """settle_trade is idempotent; the trades.jsonl append was not."""
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    now = datetime.now(timezone.utc)
    trade = open_trade(ledger, close_time=now - timedelta(seconds=10))
    bot = monitoring_bot(tmp_path, ledger)

    async def final_status(ticker):
        return MarketStatus(
            ticker=ticker,
            status="finalized",
            result="yes",
            settlement_value=Decimal("101"),
            settlement_time=now,
        )

    bot.client = SimpleNamespace(get_market_status=final_status)

    await bot._monitor_open_trade(trade, once=True)
    # The same open record again: a restart, or a second monitor pass, reaches
    # settle_trade's early return with a record that still reads OPEN.
    await bot._monitor_open_trade(trade, once=True)
    ledger.close()

    events = (tmp_path / "trades.jsonl").read_text().splitlines()
    settled = [line for line in events if '"event":"settled"' in line]
    assert len(settled) == 1

    audit = (tmp_path / LEDGER_AUDIT_NAME).read_text()
    assert audit.count("method=settle_trade") == 2
    assert "already settled" in audit


# ----------------------------------------------------------------- hourly ---


def test_the_hourly_block_fires_once_on_the_boundary(recorder) -> None:
    bot = object.__new__(KalshiCryptoBot)
    bot.mode = "paper"
    bot.live = False
    bot._hourly = HourlyClock(datetime(2026, 8, 10, 9, 30))
    bot._period = PeriodCounters()
    bot._period.record_rejection("min_edge")
    bot._period.record_evaluation()
    bot._lifetime_stats = lambda: bot_module.LifetimeStats(
        trades=4, wins=3, losses=1, pnl=Decimal("21.71"), balance=Decimal("121.71")
    )

    bot._maybe_log_hourly(now=datetime(2026, 8, 10, 9, 59))
    assert recorder.of_kind(KIND_STATS) == []

    bot._maybe_log_hourly(now=datetime(2026, 8, 10, 10, 0, 1))
    bot._maybe_log_hourly(now=datetime(2026, 8, 10, 10, 30))

    blocks = recorder.of_kind(KIND_STATS)
    assert len(blocks) == 1
    assert "hour 2026-08-10 09:00-10:00 PAPER" in blocks[0]
    assert "top gate min_edge x1" in blocks[0]
    assert "balance $121.71" in blocks[0]
    # The period restarts empty, or the next block double-counts this one.
    assert bot._period.top_gate() is None


def test_a_half_built_bot_still_logs_and_audits_without_raising() -> None:
    """Every diagnostic degrades to a no-op rather than stranding a position."""
    bot = object.__new__(KalshiCryptoBot)

    assert bot.ledger_audit.enabled is False
    assert bot.ledger_audit.record("settle_trade", trade_id="t-1") is None
    assert bot.scan_verdicts == {}
    bot._maybe_log_hourly()


def test_the_startup_line_is_tagged_for_the_console(recorder) -> None:
    bot_module._event.info("PAPER mode")

    assert recorder.of_kind(KIND_EVENT) == ["PAPER mode"]


# --------------------------------------------------------------------------
# Where an installed copy reads its configuration from
# --------------------------------------------------------------------------


def test_an_installed_copy_reads_the_config_directory(tmp_path, monkeypatch) -> None:
    """The bug: `.env` was loaded from the install directory, unconditionally.

    An installed copy keeps its config outside the install directory on purpose
    — `kx15 update` replaces that directory wholesale — so that path names a
    file which by design never exists. `load_dotenv` on a missing path is a
    silent no-op, so the bot came up on pure defaults (demo instead of
    production, the default paper balance, the default thresholds) while
    `kx15 config --check` read the real file and reported it correctly.
    """
    import bot

    config = tmp_path / "cfg"
    config.mkdir()
    (config / ".env").write_text("KALSHI_ENVIRONMENT=production\n")
    monkeypatch.setenv("KX15_STATE_DIR", str(config))
    assert bot.config_file() == config / ".env"


def test_the_working_directory_is_the_second_choice(tmp_path, monkeypatch) -> None:
    """The launcher cd's into the config directory, so cwd is the fallback."""
    import bot

    monkeypatch.delenv("KX15_STATE_DIR", raising=False)
    (tmp_path / ".env").write_text("KALSHI_ENVIRONMENT=demo\n")
    monkeypatch.chdir(tmp_path)
    assert bot.config_file() == tmp_path / ".env"


def test_a_source_checkout_still_uses_its_own_env(tmp_path, monkeypatch) -> None:
    """Which is the case that always worked, and must keep working."""
    import bot

    monkeypatch.delenv("KX15_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    assert bot.config_file() == bot.PROJECT_ROOT / ".env"


def test_a_state_dir_without_an_env_falls_through(tmp_path, monkeypatch) -> None:
    """A configured-but-empty state directory must not shadow the fallbacks."""
    import bot

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("KX15_STATE_DIR", str(empty))
    monkeypatch.chdir(tmp_path)
    assert bot.config_file() == bot.PROJECT_ROOT / ".env"
