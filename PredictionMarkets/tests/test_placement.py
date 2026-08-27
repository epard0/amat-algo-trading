"""Placement-path behaviour: a moving book must never crash a trading cycle."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot import KalshiCryptoBot, TradeCandidate
from core.strategy_brain.strategies.crypto_15min_predictor import Prediction
from execution.decision_log import DecisionLog
from execution.kalshi_client import BookQuote, KalshiMarket
from execution.risk_gate import GateResult
from execution.trade_ledger import TradeLedger


def market() -> KalshiMarket:
    now = datetime.now(timezone.utc)
    return KalshiMarket(
        asset="ETH",
        ticker="KXETH15M-26JUL282245-45",
        event_ticker="KXETH15M-26JUL282245",
        title="ETH up?",
        target_price=Decimal("1912.85"),
        open_time=now - timedelta(minutes=12),
        close_time=now + timedelta(minutes=2),
        yes_bid=Decimal("0.86"),
        yes_ask=Decimal("0.88"),
        yes_bid_size=Decimal("50"),
        yes_ask_size=Decimal("50"),
        fee_multiplier=Decimal("1"),
    )


def candidate(target: KalshiMarket) -> TradeCandidate:
    probability = Decimal("0.70")
    return TradeCandidate(
        market=target,
        prediction=Prediction(
            probability_yes=Decimal("0.30"),
            current_spot=Decimal("1912.17"),
            target_price=target.target_price,
            minutes_remaining=Decimal("2"),
            distance_return=Decimal("-0.0004"),
            momentum_return=Decimal("-0.0001"),
            volatility_per_minute=Decimal("0.0006"),
        ),
        outcome="no",
        outcome_probability=probability,
        top_of_book_price=Decimal("0.14"),
        buffered_price=Decimal("0.17"),
        edge=Decimal("0.53"),
    )


def bot_for(tmp_path: Path, *, quote, live: bool = True) -> KalshiCryptoBot:
    bot = object.__new__(KalshiCryptoBot)
    bot.live = live
    bot.mode = "live" if live else "paper"
    bot.ledger = TradeLedger(tmp_path / "ledger.db", Decimal("20"))
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")
    bot._cooldowns = {}
    bot._halt_reason = None
    bot._peak_balance = Decimal("0")
    bot.settings = SimpleNamespace(
        environment="production",
        min_edge=Decimal("0.05"),
        fee_reserve_per_contract=Decimal("0.02"),
        live_capital_limit_usd=Decimal("10"),
        order_failure_cooldown_seconds=60,
        status_check_lead_seconds=5,
        trade_log_path=tmp_path / "audit.jsonl",
    )

    async def already_traded(ticker):
        return False

    async def get_quote(ticker, outcome):
        return quote

    async def balance(exchange_index=None):
        return Decimal("29.02")

    bot.client = SimpleNamespace(
        already_traded=already_traded,
        get_quote=get_quote,
        get_available_balance=balance,
    )

    async def allow():
        return GateResult(True)

    bot._check_capital_breaker = allow
    return bot


async def test_empty_book_cools_down_instead_of_ordering(tmp_path: Path) -> None:
    target = market()
    bot = bot_for(tmp_path, quote=None)

    placed = await bot._place_candidate(candidate(target))
    bot.ledger.close()

    assert placed is False
    assert bot._in_cooldown(target.ticker) is True


async def test_decayed_quote_is_abandoned_before_sizing(tmp_path: Path) -> None:
    target = market()
    # The book moved: NO now costs 0.66, so the live edge is gone.
    moved = BookQuote(
        outcome="no",
        best_price=Decimal("0.66"),
        limit_price=Decimal("0.67"),
        depth_at_limit=Decimal("500"),
    )
    bot = bot_for(tmp_path, quote=moved)

    placed = await bot._place_candidate(candidate(target))
    bot.ledger.close()

    assert placed is False
    # A quote that simply moved is not a failure; no cooldown is applied.
    assert bot._in_cooldown(target.ticker) is False


async def test_failed_evaluation_is_still_recorded(tmp_path: Path) -> None:
    """A Kraken failure must leave a record, not a hole in the log.

    Without this, an asset that never trades because its price feed is broken
    looks exactly like an asset that was never scanned.
    """
    bot = bot_for(tmp_path, quote=None)
    bot.decisions = DecisionLog(tmp_path / "decisions.jsonl")

    async def explode(market, trace):
        raise TimeoutError("kraken timed out")

    bot._evaluate_market_traced = explode

    with pytest.raises(TimeoutError):
        await bot._evaluate_market(market(), "cycle-1")
    bot.ledger.close()

    (record,) = bot.decisions.read_all()
    assert record["decision"] == "error"
    assert record["blocked_by"] == "evaluation_completed"
    assert "kraken timed out" in record["decision_reason"]


async def test_capital_breaker_halts_placement(tmp_path: Path) -> None:
    target = market()
    bot = bot_for(
        tmp_path,
        quote=BookQuote(
            outcome="no",
            best_price=Decimal("0.14"),
            limit_price=Decimal("0.15"),
            depth_at_limit=Decimal("500"),
        ),
    )

    async def deny():
        return GateResult(False, "drawdown 35.0% from peak $20.00")

    bot._check_capital_breaker = deny

    placed = await bot._place_candidate(candidate(target))
    bot.ledger.close()

    assert placed is False
    assert bot._halt_reason is not None
    assert "drawdown" in bot._halt_reason


async def test_safe_mode_requires_market_consensus_after_model_confidence(
    tmp_path: Path,
) -> None:
    target = market()
    target = SimpleNamespace(
        **{**target.__dict__, "yes_bid": Decimal("0.15"), "yes_ask": Decimal("0.17")}
    )
    bot = bot_for(
        tmp_path,
        quote=BookQuote(
            outcome="no",
            best_price=Decimal("0.84"),
            limit_price=Decimal("0.85"),
            depth_at_limit=Decimal("500"),
        ),
    )
    bot._safe_mode = True
    bot.settings.safe_mode_min_probability = Decimal("0.92")
    bot.settings.safe_mode_min_market_price = Decimal("0.85")
    bot.settings.safe_mode_min_settlement_sigma = Decimal("2")
    setup = candidate(target)
    setup = TradeCandidate(
        market=target,
        prediction=Prediction(
            probability_yes=Decimal("0.05"),
            current_spot=Decimal("1900"),
            target_price=target.target_price,
            minutes_remaining=Decimal("2"),
            distance_return=Decimal("-0.01"),
            momentum_return=Decimal("-0.001"),
            volatility_per_minute=Decimal("0.001"),
            regime="DOWNTREND",
            distance_z=Decimal("-2.5"),
        ),
        outcome="no",
        outcome_probability=Decimal("0.95"),
        top_of_book_price=Decimal("0.83"),
        buffered_price=Decimal("0.85"),
        edge=Decimal("0.10"),
    )
    assert await bot._place_candidate(setup) is False
    bot.ledger.close()
