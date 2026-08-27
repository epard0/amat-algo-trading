from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from bot import KalshiCryptoBot
from execution.trade_ledger import TradeLedger


async def test_daily_halt_survives_process_restart(tmp_path):
    path = tmp_path / "risk.db"
    first = TradeLedger(path, paper_starting_balance=Decimal("20"))
    today = datetime.now(timezone.utc).date().isoformat()
    first.upsert_risk_state(
        mode="paper",
        environment="production",
        risk_date=today,
        starting_balance=Decimal("20"),
        peak_balance=Decimal("20"),
        current_balance=Decimal("17"),
        realized_pnl=Decimal("-3"),
        consecutive_losses=3,
        halted=True,
        halt_reason="three losses tripped the persisted rail",
    )
    first.close()

    bot = object.__new__(KalshiCryptoBot)
    bot.live = False
    bot.mode = "paper"
    bot.ledger = TradeLedger(path, paper_starting_balance=Decimal("20"))
    bot.settings = SimpleNamespace(
        environment="production",
        max_consecutive_losses=3,
        max_drawdown_fraction=Decimal("0.30"),
        max_daily_loss_usd=Decimal("2"),
    )
    bot._peak_balance = Decimal("0")

    result = await bot._check_capital_breaker()
    bot.ledger.close()

    assert result.allowed is False
    assert result.reason == "three losses tripped the persisted rail"
