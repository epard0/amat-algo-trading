from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from bot import KalshiCryptoBot
from execution.trade_ledger import TradeLedger, calculate_paper_fill


def add_result(ledger: TradeLedger, index: int, *, won: bool) -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc) + timedelta(seconds=index)
    fill = calculate_paper_fill(Decimal("2"), Decimal("0.50"))
    trade = ledger.record_open_trade(
        mode="paper", environment="production", asset="BTC",
        ticker=f"KXBTC15M-SAFE-{index}", event_ticker=f"EVENT-{index}",
        order_id=f"order-{index}", client_order_id=f"client-{index}",
        outcome="yes", placed_at=now, market_open_time=now,
        market_close_time=now + timedelta(minutes=15),
        status_check_time=now + timedelta(minutes=14), contracts=fill.contracts,
        entry_price=fill.price, entry_cost=fill.entry_cost,
        fees_paid=fill.fees_paid, total_debit=fill.total_debit,
        target_price=Decimal("100"), model_probability=Decimal("0.8"),
        model_edge=Decimal("0.1"),
    )
    ledger.settle_trade(
        trade.trade_id, "yes" if won else "no", "settled", now + timedelta(minutes=15)
    )


def bot_with_ledger(ledger: TradeLedger) -> KalshiCryptoBot:
    bot = object.__new__(KalshiCryptoBot)
    bot.mode = "paper"
    bot.ledger = ledger
    bot._safe_mode = False
    bot.settings = SimpleNamespace(
        environment="production", safe_mode_enabled=True,
        safe_mode_loss_trigger=3, safe_mode_min_probability=Decimal("0.92"),
        safe_mode_min_market_price=Decimal("0.85"),
        safe_mode_min_settlement_sigma=Decimal("2"), trade_budget_usd=Decimal("2"),
    )
    bot.decisions = SimpleNamespace(record_event=lambda *args, **kwargs: None)
    return bot


def test_three_consecutive_losses_enter_safe_mode_and_a_win_clears_it(tmp_path) -> None:
    ledger = TradeLedger(tmp_path / "safe.db", Decimal("20"))
    bot = bot_with_ledger(ledger)
    for index in range(3):
        add_result(ledger, index, won=False)
    bot._update_safe_mode_from_ledger()
    assert bot._safe_mode is True

    add_result(ledger, 3, won=True)
    bot._update_safe_mode_from_ledger()
    assert bot._safe_mode is False
    ledger.close()


def test_two_losses_do_not_enter_safe_mode(tmp_path) -> None:
    ledger = TradeLedger(tmp_path / "safe.db", Decimal("20"))
    bot = bot_with_ledger(ledger)
    add_result(ledger, 0, won=False)
    add_result(ledger, 1, won=False)
    bot._update_safe_mode_from_ledger()
    assert bot._safe_mode is False
    ledger.close()

