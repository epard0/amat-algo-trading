import sqlite3
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from execution.trade_ledger import TradeLedger, calculate_paper_fill


def record_trade(
    ledger: TradeLedger,
    *,
    ticker: str = "KXBTC15M-TEST",
    outcome: str = "yes",
):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fill = calculate_paper_fill(Decimal("10"), Decimal("0.50"))
    return ledger.record_open_trade(
        mode="paper",
        environment="production",
        asset="BTC",
        ticker=ticker,
        event_ticker=ticker.rsplit("-", 1)[0],
        order_id=f"paper-{ticker}",
        client_order_id=f"client-{ticker}",
        outcome=outcome,
        placed_at=now,
        market_open_time=now,
        market_close_time=now + timedelta(minutes=15),
        status_check_time=now + timedelta(minutes=14, seconds=55),
        contracts=fill.contracts,
        entry_price=fill.price,
        entry_cost=fill.entry_cost,
        fees_paid=fill.fees_paid,
        total_debit=fill.total_debit,
        target_price=Decimal("100000"),
        model_probability=Decimal("0.70"),
        model_edge=Decimal("0.10"),
        metadata={"test": True},
    )


def test_paper_fee_and_balance_rounding() -> None:
    fill = calculate_paper_fill(Decimal("10"), Decimal("0.50"))
    assert fill.entry_cost == Decimal("5.0000")
    assert fill.fees_paid == Decimal("0.1800")
    assert fill.total_debit == Decimal("5.18")


@pytest.mark.parametrize(
    ("outcome", "market_result", "expected_status", "expected_pnl"),
    [
        ("yes", "yes", "WON", Decimal("4.82")),
        ("yes", "no", "LOST", Decimal("-5.18")),
        ("no", "no", "WON", Decimal("4.82")),
        ("no", "yes", "LOST", Decimal("-5.18")),
    ],
)
def test_yes_and_no_payoff_accounting(
    tmp_path: Path,
    outcome: str,
    market_result: str,
    expected_status: str,
    expected_pnl: Decimal,
) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    trade = record_trade(ledger, outcome=outcome)
    settled = ledger.settle_trade(
        trade.trade_id,
        market_result=market_result,
        exchange_status="settled",
    )
    ledger.close()

    assert settled.status == expected_status
    assert settled.market_result == market_result
    assert settled.pnl == expected_pnl


def test_open_trade_and_win_survive_restart(tmp_path: Path) -> None:
    database = tmp_path / "ledger.db"
    ledger = TradeLedger(database, Decimal("100"))
    trade = record_trade(ledger)
    open_summary = ledger.paper_summary("production")
    assert open_summary.open_trades == 1
    assert open_summary.locked_capital == Decimal("5.18")
    assert open_summary.available_balance == Decimal("94.82")
    ledger.close()

    reopened = TradeLedger(database, Decimal("999"))
    recovered = reopened.get_open_trade("paper", "production")
    assert recovered is not None
    assert recovered.trade_id == trade.trade_id
    settled = reopened.settle_trade(
        trade.trade_id,
        market_result="yes",
        exchange_status="finalized",
    )
    summary = reopened.paper_summary("production")
    reopened.close()

    assert settled.status == "WON"
    assert settled.gross_payout == Decimal("10")
    assert settled.pnl == Decimal("4.82")
    assert summary.starting_balance == Decimal("100")
    assert summary.current_balance == Decimal("104.82")
    assert summary.available_balance == Decimal("104.82")
    assert summary.wins == 1
    assert summary.win_rate == Decimal("1")


def test_loss_and_ledger_aggregates(tmp_path: Path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    first = record_trade(ledger, ticker="KXBTC15M-FIRST", outcome="yes")
    ledger.settle_trade(first.trade_id, "no", "finalized")
    second = record_trade(ledger, ticker="KXBTC15M-SECOND", outcome="no")
    ledger.settle_trade(second.trade_id, "no", "finalized")

    summary = ledger.paper_summary("production")
    trades = ledger.list_trades("paper", "production")
    ledger.close()

    assert summary.total_trades == 2
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.win_rate == Decimal("0.5")
    assert summary.gross_profit == Decimal("4.82")
    assert summary.gross_loss == Decimal("5.18")
    assert summary.net_pnl == Decimal("-0.36")
    assert len(trades) == 2


def test_the_database_now_permits_several_open_trades(tmp_path: Path) -> None:
    """The one-open-position rule moved out of SQLite and into PositionBook.

    It used to be a partial unique index, which scalping cannot live with: that
    strategy holds several positions at once by design. The guarantee is not
    gone, it relocated - ``execution/position_book.py`` enforces it under an
    asyncio lock, and the predictor is still pinned to a single position. This
    test records the relocation so the change reads as deliberate rather than as
    a constraint somebody dropped by accident.
    """
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    record_trade(ledger, ticker="KXBTC15M-FIRST")
    record_trade(ledger, ticker="KXETH15M-SECOND")

    assert ledger.count_open_trades("paper", "production") == 2
    ledger.close()


def test_one_strategy_still_cannot_enter_the_same_ticker_twice(tmp_path: Path) -> None:
    """Duplicate protection survived; only its scope changed.

    Two *different* strategies reaching the same conclusion about one market is
    a coincidence, not a double entry. The same strategy doing it twice is the
    bug the original constraint existed to stop.
    """
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    record_trade(ledger, ticker="KXBTC15M-SAME")

    with pytest.raises(sqlite3.IntegrityError):
        record_trade(ledger, ticker="KXBTC15M-SAME")

    ledger.close()


def test_profitable_exit_permits_one_opposite_leg_but_not_same_side(
    tmp_path: Path,
) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    first = record_trade(ledger, ticker="KXBTC15M-SWITCH", outcome="yes")
    ledger.record_exit(
        first.trade_id,
        exit_price=Decimal("0.60"),
        exit_contracts=first.contracts,
        exit_fees=Decimal("0.01"),
        exchange_status="take_profit",
    )

    same, same_reason = ledger.can_enter_ticker(
        mode="paper",
        environment="production",
        ticker=first.ticker,
        strategy="predictor",
        outcome="yes",
        maximum_entries=2,
    )
    opposite, _ = ledger.can_enter_ticker(
        mode="paper",
        environment="production",
        ticker=first.ticker,
        strategy="predictor",
        outcome="no",
        maximum_entries=2,
    )
    assert not same and "same-side" in same_reason
    assert opposite

    second = record_trade(ledger, ticker=first.ticker, outcome="no")
    assert second.entry_sequence == 2
    exhausted, _ = ledger.can_enter_ticker(
        mode="paper",
        environment="production",
        ticker=first.ticker,
        strategy="predictor",
        outcome="yes",
        maximum_entries=2,
    )
    assert not exhausted
    ledger.close()


def test_partial_exit_cannot_falsely_close_the_unsold_remainder(tmp_path: Path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    trade = record_trade(ledger, ticker="KXBTC15M-ATOMIC")

    with pytest.raises(ValueError, match="complete open position"):
        ledger.record_exit(
            trade.trade_id,
            exit_price=Decimal("0.60"),
            exit_contracts=trade.contracts / 2,
            exit_fees=Decimal("0.01"),
            exchange_status="take_profit",
        )

    assert ledger.get_trade(trade.trade_id).status == "OPEN"
    ledger.close()


def test_ioc_partial_exit_is_idempotent_and_settles_only_the_residual(
    tmp_path: Path,
) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    trade = record_trade(ledger, ticker="KXBTC15M-PARTIAL", outcome="yes")
    first = ledger.record_exit_fill(
        trade.trade_id,
        client_order_id="exit-1",
        exit_price=Decimal("0.70"),
        exit_contracts=trade.contracts / 2,
        exit_fees=Decimal("0.01"),
        exchange_status="take_profit",
    )
    assert first.status == "OPEN"
    assert first.remaining_contracts == trade.contracts / 2
    duplicate = ledger.record_exit_fill(
        trade.trade_id,
        client_order_id="exit-1",
        exit_price=Decimal("0.70"),
        exit_contracts=trade.contracts / 2,
        exit_fees=Decimal("0.01"),
        exchange_status="take_profit",
    )
    assert duplicate.remaining_contracts == first.remaining_contracts
    settled = ledger.settle_trade(
        trade.trade_id, "yes", "settled", datetime.now(timezone.utc)
    )
    expected = (
        trade.contracts / 2 * Decimal("0.70") - Decimal("0.01")
    ) + trade.contracts / 2
    assert settled.gross_payout == expected
    assert settled.remaining_contracts == 0
    ledger.close()


def test_v4_schema_is_strict_and_records_normalized_runs(tmp_path: Path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    version = ledger.connection.execute(
        "SELECT value FROM ledger_metadata WHERE key='schema_version'"
    ).fetchone()[0]
    strict = {
        row["name"]: row["strict"]
        for row in ledger.connection.execute("PRAGMA table_list")
    }
    assert version == "4"
    for table in (
        "trades",
        "data_runs",
        "market_snapshots",
        "orderbook_levels",
        "portfolio_snapshots",
        "pair_arb_observations",
        "book_events",
        "public_trades",
        "signal_evaluations",
        "orders",
        "order_transitions",
        "fills",
        "position_evaluations",
        "data_quality_events",
        "persistent_risk_state",
        "exit_counterfactuals",
        "probability_calibrations",
    ):
        assert strict[table] == 1

    run_id = ledger.start_data_run(
        mode="paper",
        environment="production",
        strategy="predictor",
        assets=("BTC",),
        config_json='{"trade_budget_usd":"50"}',
    )
    ledger.finish_data_run(run_id, completed=True)
    status, contract = ledger.connection.execute(
        "SELECT status, contract_version FROM data_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert (status, contract) == ("COMPLETED", 4)
    config_json = ledger.connection.execute(
        "SELECT config_json FROM data_runs WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    assert config_json == '{"trade_budget_usd":"50"}'


def test_capture_provenance_columns_migrate_additively(tmp_path: Path) -> None:
    database = tmp_path / "legacy-v4.db"
    ledger = TradeLedger(database, Decimal("100"))
    ledger.close()
    connection = sqlite3.connect(database)
    connection.execute("ALTER TABLE data_runs DROP COLUMN config_json")
    connection.execute("ALTER TABLE market_snapshots DROP COLUMN recorded_at")
    connection.execute("ALTER TABLE market_snapshots DROP COLUMN book_source")
    connection.execute("ALTER TABLE market_snapshots DROP COLUMN book_sequence")
    connection.execute("ALTER TABLE market_snapshots DROP COLUMN book_age_ms")
    connection.commit()
    connection.close()

    migrated = TradeLedger(database, Decimal("100"))
    run_columns = {
        row["name"]
        for row in migrated.connection.execute("PRAGMA table_info(data_runs)")
    }
    snapshot_columns = {
        row["name"]
        for row in migrated.connection.execute("PRAGMA table_info(market_snapshots)")
    }
    assert "config_json" in run_columns
    assert {"recorded_at", "book_source", "book_sequence", "book_age_ms"} <= (
        snapshot_columns
    )
    assert migrated.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    migrated.close()


def test_exited_trade_records_hold_counterfactual_and_calibration(tmp_path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    trade = record_trade(ledger, ticker="KXBTC15M-COUNTERFACTUAL", outcome="yes")
    exited = ledger.record_exit_fill(
        trade.trade_id,
        client_order_id="counterfactual-exit",
        exit_price=Decimal("0.90"),
        exit_contracts=trade.contracts,
        exit_fees=Decimal("0.01"),
        exchange_status="take_profit",
    )
    ledger.record_exit_counterfactual(exited.trade_id, "yes")
    counterfactual = ledger.connection.execute(
        "SELECT * FROM exit_counterfactuals WHERE trade_id=?", (trade.trade_id,)
    ).fetchone()
    calibration = ledger.connection.execute(
        "SELECT * FROM probability_calibrations WHERE trade_id=?", (trade.trade_id,)
    ).fetchone()
    assert Decimal(counterfactual["hold_pnl"]) == Decimal("4.82")
    assert Decimal(counterfactual["delta_hold_minus_exit"]) > 0
    assert calibration["time_bucket"] == "181_plus"
    assert calibration["actual_yes"] == 1
    assert calibration["brier_model"] == pytest.approx(0.09)
    assert (
        ledger.get_unlabeled_exited_trades(
            "paper", "production", datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
        == []
    )
    ledger.close()
    ledger.close()


def test_market_and_depth_rows_share_one_strict_snapshot_key(tmp_path: Path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    run_id = ledger.start_data_run(
        mode="paper",
        environment="production",
        strategy="predictor",
        assets=("BTC",),
        started_at=now,
    )
    market = SimpleNamespace(
        asset="BTC",
        ticker="KXBTC15M-DATA",
        event_ticker="KXBTC15M-E",
        exchange_index=2,
        open_time=now,
        close_time=now + timedelta(minutes=15),
        target_price=Decimal("100000"),
        yes_bid=Decimal("0.60"),
        yes_ask=Decimal("0.62"),
        yes_bid_size=Decimal("20"),
        yes_ask_size=Decimal("30"),
        fee_multiplier=Decimal("1"),
    )
    analysis = SimpleNamespace(
        microprice=Decimal("0.611"),
        imbalance=Decimal("0.10"),
        toxicity=Decimal("0.10"),
    )
    snapshot_id = ledger.record_market_snapshot(
        run_id=run_id,
        cycle_id="cycle-1",
        mode="paper",
        environment="production",
        market=market,
        observed_at=now,
        yes_levels=[(Decimal("0.60"), Decimal("20"))],
        no_levels=[(Decimal("0.38"), Decimal("30"))],
        analysis=analysis,
        book_source="websocket",
        book_sequence=42,
        book_age_ms=125.0,
    )
    row = ledger.connection.execute(
        "SELECT exchange_index, levels_yes, levels_no, microprice, recorded_at, book_source, "
        "book_sequence, book_age_ms FROM market_snapshots "
        "WHERE snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    levels = ledger.connection.execute(
        "SELECT outcome_side, price, quantity FROM orderbook_levels "
        "WHERE snapshot_id=? ORDER BY outcome_side",
        (snapshot_id,),
    ).fetchall()
    assert (row["levels_yes"], row["levels_no"], row["microprice"]) == (1, 1, "0.611")
    assert row["exchange_index"] == 2
    assert row["recorded_at"] is not None
    assert (row["book_source"], row["book_sequence"], row["book_age_ms"]) == (
        "websocket",
        42,
        125.0,
    )
    assert [(level["outcome_side"], level["price"]) for level in levels] == [
        ("no", "0.38"),
        ("yes", "0.60"),
    ]
    ledger.close()


def test_v4_event_order_fill_and_risk_records_are_normalized(tmp_path: Path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    run_id = ledger.start_data_run(
        mode="paper",
        environment="production",
        strategy="scalper",
        assets=("BTC",),
        source_revision="unversioned:abc",
        config_hash="cfg",
        model_hash="model",
        started_at=now,
    )
    ledger.record_book_event(
        run_id=run_id,
        ticker="KXBTC15M-T",
        event_type="delta",
        sequence=11,
        outcome_side="yes",
        price=Decimal("0.61"),
        quantity_delta=Decimal("-2"),
        exchange_ts_ms=1234,
        received_at=now,
        received_monotonic_ns=99,
    )
    order_id = ledger.record_order(
        run_id=run_id,
        client_order_id="client-v4",
        ticker="KXBTC15M-T",
        strategy="scalper",
        intent="entry",
        book_side="bid",
        outcome="yes",
        order_type="ioc",
        limit_price=Decimal("0.62"),
        requested_contracts=Decimal("2"),
        reduce_only=False,
        post_only=False,
        submitted_at=now,
        submitted_monotonic_ns=100,
    )
    ledger.record_order_transition(
        order_record_id=order_id,
        state="filled",
        fill_count=Decimal("2"),
        remaining_count=Decimal("0"),
        exchange_ts_ms=1240,
        observed_at=now,
        observed_monotonic_ns=110,
    )
    ledger.record_fill(
        order_record_id=order_id,
        venue_fill_id="fill-v4",
        price=Decimal("0.62"),
        contracts=Decimal("2"),
        fee=Decimal("0.02"),
        liquidity_role="taker",
        exchange_ts_ms=1240,
        received_at=now,
    )
    ledger.upsert_risk_state(
        mode="paper",
        environment="production",
        risk_date="2026-01-01",
        starting_balance=Decimal("100"),
        peak_balance=Decimal("103"),
        current_balance=Decimal("101"),
        realized_pnl=Decimal("1"),
        consecutive_losses=0,
        halted=False,
        updated_at=now,
    )

    run = ledger.connection.execute(
        "SELECT contract_version, source_revision, config_hash FROM data_runs"
    ).fetchone()
    risk = ledger.get_risk_state(
        mode="paper", environment="production", risk_date="2026-01-01"
    )
    assert tuple(run) == (4, "unversioned:abc", "cfg")
    assert (
        ledger.connection.execute("SELECT COUNT(*) FROM book_events").fetchone()[0] == 1
    )
    assert ledger.connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    assert risk is not None and risk["peak_balance"] == "103"
    ledger.close()


def test_book_events_are_batched_and_flushed_on_close(tmp_path: Path) -> None:
    database = tmp_path / "ledger.db"
    ledger = TradeLedger(database, Decimal("100"))
    run_id = ledger.start_data_run(
        mode="paper",
        environment="production",
        strategy="predictor",
        assets=("BTC",),
    )
    ledger.record_book_event(
        run_id=run_id,
        ticker="KXBTC15M-BATCH",
        event_type="delta",
        sequence=1,
        outcome_side="yes",
        price=Decimal("0.50"),
        quantity_delta=Decimal("1"),
    )

    assert len(ledger._book_event_rows) == 1
    ledger.close()

    reopened = sqlite3.connect(database)
    assert reopened.execute("SELECT COUNT(*) FROM book_events").fetchone()[0] == 1
    reopened.close()


def test_public_trades_are_batched_and_flushed_on_close(tmp_path: Path) -> None:
    database = tmp_path / "ledger.db"
    ledger = TradeLedger(database, Decimal("100"))
    run_id = ledger.start_data_run(
        mode="paper",
        environment="production",
        strategy="predictor",
        assets=("BTC",),
    )
    ledger.record_public_trade(
        run_id=run_id,
        ticker="KXBTC15M-BATCH",
        trade_id="venue-1",
        yes_price=Decimal("0.55"),
        contracts=Decimal("2"),
        taker_side="yes",
    )

    assert len(ledger._public_trade_rows) == 1
    assert ledger.connection.execute("SELECT COUNT(*) FROM public_trades").fetchone()[0] == 0
    ledger.close()

    reopened = sqlite3.connect(database)
    assert reopened.execute("SELECT COUNT(*) FROM public_trades").fetchone()[0] == 1
    reopened.close()


def test_market_snapshot_batch_is_committed_together(tmp_path: Path) -> None:
    ledger = TradeLedger(tmp_path / "ledger.db", Decimal("100"))
    run_id = ledger.start_data_run(
        mode="paper",
        environment="production",
        strategy="predictor",
        assets=("BTC",),
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = SimpleNamespace(
        asset="BTC",
        ticker="KXBTC15M-BATCH",
        event_ticker="KXBTC15M",
        open_time=now,
        close_time=now + timedelta(minutes=15),
        target_price=Decimal("100000"),
        yes_bid=Decimal("0.49"),
        yes_ask=Decimal("0.51"),
        yes_bid_size=Decimal("10"),
        yes_ask_size=Decimal("12"),
        fee_multiplier=Decimal("1"),
    )
    second = SimpleNamespace(**{**vars(market), "ticker": "KXBTC15M-BATCH2"})

    ids = ledger.record_market_snapshots(
        [
            {
                "run_id": run_id,
                "cycle_id": "cycle-1",
                "mode": "paper",
                "environment": "production",
                "market": item,
                "observed_at": now,
            }
            for item in (market, second)
        ]
    )

    assert len(ids) == 2
    assert (
        ledger.connection.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE cycle_id='cycle-1'"
        ).fetchone()[0]
        == 2
    )
    ledger.close()
