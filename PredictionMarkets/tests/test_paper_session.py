import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from bot import KalshiCryptoBot
from execution.trade_ledger import TradeLedger


async def test_short_paper_session_runs_each_asset_once_per_pass(
    tmp_path: Path,
    capsys,
) -> None:
    bot = object.__new__(KalshiCryptoBot)
    bot.live = False
    bot.mode = "paper"
    bot._stopping = False
    bot._wake_event = asyncio.Event()
    bot._halt_reason = None
    bot.settings = SimpleNamespace(
        environment="production",
        assets=("BTC", "ETH", "SOL", "HYPE", "DOGE"),
    )
    bot.ledger = TradeLedger(tmp_path / "session.db", Decimal("20"))
    cycle_calls = []

    async def fake_cycle(evaluation_counts):
        cycle_calls.append(1)
        for asset in bot.settings.assets:
            evaluation_counts[asset] += 1
        return False

    bot.run_cycle = fake_cycle
    report = await bot.run_paper_session(
        duration_seconds=0.08,
        evaluation_passes=8,
    )
    bot.ledger.close()

    assert len(cycle_calls) == 8
    assert report.scheduled_passes == 8
    assert report.completed_passes == 8
    assert report.skipped_passes == 0
    assert report.starting_balance == Decimal("20")
    assert report.ending_balance == Decimal("20")
    assert report.trades == 0
    assert all(asset.evaluations == 8 for asset in report.assets)
    assert "PAPER SESSION RESULTS" in capsys.readouterr().out


def test_one_hour_passes_align_to_two_minutes_before_close() -> None:
    started = datetime(2026, 7, 28, 18, 9, tzinfo=timezone.utc)

    delay = KalshiCryptoBot._aligned_first_pass_delay(started, 450)
    first_pass = started.timestamp() + delay

    assert delay == 240
    assert (first_pass + 120) % 900 == 0


def _exited_trade(pnl: str, asset: str = "BTC"):
    """A position sold back into the book rather than carried to settlement."""
    return SimpleNamespace(
        asset=asset,
        status="EXITED",
        pnl=Decimal(pnl),
        fees_paid=Decimal("0.05"),
        exit_fees=Decimal("0.03"),
    )


def _settled_trade(status: str, pnl: str, asset: str = "BTC"):
    return SimpleNamespace(
        asset=asset,
        status=status,
        pnl=Decimal(pnl),
        fees_paid=Decimal("0.05"),
        exit_fees=None,
    )


def test_an_early_exit_counts_toward_realised_pnl() -> None:
    """The headline used to disagree with its own per-asset table.

    EXITED was missing from the realised set, so a session whose only trade was
    sold back into the book at a loss printed "Net P&L +0.00" above a table
    reading -2.35. An early exit is as realised as a settlement.
    """
    from execution.paper_session import build_paper_session_report

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    report = build_paper_session_report(
        started_at=now,
        ended_at=now,
        requested_duration_seconds=60,
        scheduled_passes=1,
        completed_passes=1,
        skipped_passes=0,
        starting_balance=Decimal("20"),
        ending_balance=Decimal("17.65"),
        configured_assets=("BTC",),
        evaluation_counts={"BTC": 1},
        trades=[_exited_trade("-2.35")],
    )

    assert report.net_pnl == Decimal("-2.35")
    assert report.losses == 1
    assert report.wins == 0
    # Both legs of the round trip are costs the operator actually paid.
    assert report.assets[0].fees == Decimal("0.08")
    assert report.assets[0].pnl == Decimal("-2.35")


def test_a_profitable_exit_counts_as_a_win() -> None:
    """Otherwise a strategy that exits every loser reports a spotless record."""
    from execution.paper_session import build_paper_session_report

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    report = build_paper_session_report(
        started_at=now,
        ended_at=now,
        requested_duration_seconds=60,
        scheduled_passes=1,
        completed_passes=1,
        skipped_passes=0,
        starting_balance=Decimal("20"),
        ending_balance=Decimal("21"),
        configured_assets=("BTC",),
        evaluation_counts={"BTC": 2},
        trades=[_exited_trade("1.00"), _exited_trade("-0.40")],
    )

    assert report.wins == 1
    assert report.losses == 1
    assert report.net_pnl == Decimal("0.60")


def test_settled_trades_are_unaffected_by_the_exit_accounting() -> None:
    """Back-compat: nothing about WON/LOST/VOID changed."""
    from execution.paper_session import build_paper_session_report

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    report = build_paper_session_report(
        started_at=now,
        ended_at=now,
        requested_duration_seconds=60,
        scheduled_passes=1,
        completed_passes=1,
        skipped_passes=0,
        starting_balance=Decimal("20"),
        ending_balance=Decimal("22"),
        configured_assets=("BTC",),
        evaluation_counts={"BTC": 3},
        trades=[
            _settled_trade("WON", "2.00"),
            _settled_trade("LOST", "-1.00"),
            _settled_trade("VOID", "0.00"),
        ],
    )

    assert (report.wins, report.losses, report.voids) == (1, 1, 1)
    assert report.net_pnl == Decimal("1.00")


async def test_a_held_position_always_gets_a_fresh_prediction(tmp_path) -> None:
    """Exit-on-reversal is unreachable without this.

    The predictor's own `not_already_traded` gate short-circuits evaluation
    before a probability is computed, so from the cycle after entry there is no
    prediction for that market. The exit path reads exactly that probability.
    Measured on a live session: a position entered at 21:58:30 had no prediction
    from 21:58:40 until it settled at a loss, and was never once considered for
    a cut.

    A held market must therefore be priced regardless of the close-time filter
    that governs quotable markets.
    """
    from datetime import timedelta

    from execution.kalshi_client import KalshiMarket
    from execution.trade_ledger import calculate_paper_fill

    now = datetime.now(timezone.utc)
    # Closing sooner than the quote window, so only the held-position branch
    # can rescue it.
    held = KalshiMarket(
        asset="SOL",
        ticker="KXSOL15M-HELD",
        event_ticker="KXSOL15M",
        title="SOL",
        target_price=Decimal("100"),
        open_time=now - timedelta(minutes=14),
        close_time=now + timedelta(seconds=60),
        yes_bid=Decimal("0.55"),
        yes_ask=Decimal("0.57"),
        yes_bid_size=Decimal("100"),
        yes_ask_size=Decimal("100"),
        fee_multiplier=Decimal("1"),
    )

    bot = object.__new__(KalshiCryptoBot)
    bot.mode = "paper"
    bot.settings = SimpleNamespace(
        environment="production",
        scalp_min_seconds_to_close=120,
        max_candle_age_seconds=120,
    )
    bot.ledger = TradeLedger(tmp_path / "held.db", Decimal("20"))
    fill = calculate_paper_fill(Decimal("2"), Decimal("0.57"))
    bot.ledger.record_open_trade(
        mode="paper",
        environment="production",
        strategy="predictor",
        asset="SOL",
        ticker=held.ticker,
        event_ticker="KXSOL15M",
        order_id="o",
        client_order_id="c",
        outcome="no",
        placed_at=now,
        market_open_time=held.open_time,
        market_close_time=held.close_time,
        status_check_time=held.close_time,
        contracts=fill.contracts,
        entry_price=fill.price,
        entry_cost=fill.entry_cost,
        fees_paid=fill.fees_paid,
        total_debit=fill.total_debit,
        target_price=Decimal("100"),
        model_probability=Decimal("0.64"),
        model_edge=Decimal("0.07"),
    )

    asked: list[str] = []

    async def fake_candles(pair, limit=45):
        asked.append(pair)
        raise RuntimeError("stop here; reaching this proves the market was wanted")

    bot.prices = SimpleNamespace(get_one_minute_candles=fake_candles)
    bot._predictors = {}
    bot.predictor = None

    await bot._fill_missing_predictions([held], {})
    bot.ledger.close()

    assert asked, "a held position inside the quote window must still be priced"


async def test_live_sizing_uses_real_balance_and_shared_aggregate_cap(tmp_path) -> None:
    """Both halves of a real-money sizing bug, caught on the first live fill.

    The scalper read `paper_summary` unconditionally, so a LIVE session sized
    against the paper ledger - a number with no connection to the account being
    spent - and never applied LIVE_CAPITAL_LIMIT_USD at all. The first live
    scalp debited $11.82 against a $10 cap, sized off a paper balance of $24.85
    while the real account held $30.10.
    """
    from decimal import Decimal

    bot = object.__new__(KalshiCryptoBot)
    bot.live = True
    bot.mode = "live"
    bot.settings = SimpleNamespace(
        environment="production",
        scalp_max_capital_fraction=Decimal("0.50"),
        live_capital_limit_usd=Decimal("10"),
        trade_budget_usd=Decimal("10"),
        scalp_max_concurrent=5,
    )
    # A paper ledger that has compounded well beyond the real account. If this
    # is what gets read, the assertions below fail loudly.
    bot.ledger = TradeLedger(tmp_path / "live.db", Decimal("500"))

    async def balance(exchange_index=None):
        return Decimal("30.10")

    bot.client = SimpleNamespace(get_available_balance=balance)
    from execution.position_book import PositionBook

    bot.positions = PositionBook(max_concurrent={"scalper": 5})

    cash = await bot._available_cash()
    bot.ledger.close()

    # The aggregate pool is identical to paper: 50% of the real balance. Each
    # individual order is separately capped at the fixed $10 trade budget.
    assert cash == Decimal("15.050")
    assert bot.positions.ceiling_for("scalper") == Decimal("15.050")


async def test_paper_sizing_still_uses_the_paper_ledger(tmp_path) -> None:
    """The live fix must not change paper behaviour."""
    from decimal import Decimal

    bot = object.__new__(KalshiCryptoBot)
    bot.live = False
    bot.mode = "paper"
    bot.settings = SimpleNamespace(
        environment="production",
        scalp_max_capital_fraction=Decimal("0.50"),
        live_capital_limit_usd=Decimal("10"),
        trade_budget_usd=Decimal("10"),
        scalp_max_concurrent=5,
    )
    bot.ledger = TradeLedger(tmp_path / "paper.db", Decimal("20"))
    from execution.position_book import PositionBook

    bot.positions = PositionBook(max_concurrent={"scalper": 5})

    cash = await bot._available_cash()
    bot.ledger.close()

    # 50% of $20 under the same aggregate rule as live.
    assert cash == Decimal("10.00")
