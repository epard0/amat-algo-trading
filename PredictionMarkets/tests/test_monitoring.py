"""Monitoring metric tests.

Every number is asserted against a hand-built list of trades rather than the
real ledger, so the expected values can be computed by hand and the tests do
not change meaning when the bot trades again.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from monitoring.grafana_exporter import iter_metric_names, render_metrics
from monitoring.performance_tracker import (
    brier_score,
    calibration,
    current_streak,
    edge_realisation,
    equity_curve,
    gate_funnel,
    group_summaries,
    health,
    longest_losing_streak,
    max_drawdown,
    summarize,
)
from monitoring.sources import LedgerTrade, read_decisions, read_trades

BASE = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def make_trade(
    trade_id: str = "t1",
    *,
    status: str = "WON",
    pnl: str | None = "1.00",
    probability: str = "0.70",
    mode: str = "live",
    asset: str = "BTC",
    outcome: str = "yes",
    debit: str = "5.00",
    fees: str = "0.10",
    minutes: int = 0,
    regime: str = "TREND",
    edge: str = "0.10",
) -> LedgerTrade:
    settled = BASE + timedelta(minutes=minutes)
    return LedgerTrade(
        trade_id=trade_id,
        mode=mode,
        environment="production",
        asset=asset,
        ticker=f"KX{asset}15M-{trade_id}",
        outcome=outcome,
        status=status,
        market_result="yes" if status == "WON" else "no",
        placed_at=settled - timedelta(minutes=10),
        market_open_time=settled - timedelta(minutes=15),
        market_close_time=settled,
        settled_at=settled,
        contracts=Decimal("10"),
        entry_price=Decimal("0.60"),
        entry_cost=Decimal(debit),
        fees_paid=Decimal(fees),
        total_debit=Decimal(debit),
        gross_payout=Decimal("10") if status == "WON" else Decimal("0"),
        pnl=Decimal(pnl) if pnl is not None else None,
        target_price=Decimal("64000"),
        model_probability=Decimal(probability),
        model_edge=Decimal(edge),
        metadata={"regime": regime, "decision_id": f"d-{trade_id}"},
    )


# ------------------------------------------------------------------ summary --

def test_summary_counts_and_money():
    trades = [
        make_trade("a", status="WON", pnl="2.00"),
        make_trade("b", status="LOST", pnl="-5.00"),
        make_trade("c", status="VOID", pnl="0.00"),
        make_trade("d", status="OPEN", pnl=None),
    ]
    s = summarize(trades)
    assert (s.trades, s.settled, s.open_trades) == (4, 3, 1)
    assert (s.wins, s.losses, s.voids) == (1, 1, 1)
    assert s.net_pnl == Decimal("-3.00")
    # Void is excluded from the win rate: it returned stake, it decided nothing.
    assert s.win_rate == Decimal("0.5")
    assert s.total_staked == Decimal("20.00")


def test_summary_of_nothing_is_zero_not_error():
    s = summarize([])
    assert s.net_pnl == Decimal("0") and s.win_rate == Decimal("0")
    assert s.roi == Decimal("0") and s.fee_drag == Decimal("0")


def test_roi_and_fee_drag_are_fractions_of_stake():
    trades = [make_trade("a", pnl="1.00", debit="10.00", fees="0.50")]
    s = summarize(trades)
    assert s.roi == Decimal("0.1")
    assert s.fee_drag == Decimal("0.05")


# ------------------------------------------------------------------- equity --

def test_equity_curve_orders_by_settlement_and_tracks_drawdown():
    trades = [
        make_trade("a", pnl="10.00", minutes=0),
        make_trade("b", pnl="-4.00", minutes=10, status="LOST"),
        make_trade("c", pnl="-3.00", minutes=20, status="LOST"),
        make_trade("d", pnl="5.00", minutes=30),
    ]
    points = equity_curve(trades)
    assert [p.cumulative for p in points] == [
        Decimal("10.00"), Decimal("6.00"), Decimal("3.00"), Decimal("8.00")
    ]
    # Peak is 10 and the trough is 3, so the deepest decline is -7.
    assert max_drawdown(points) == Decimal("-7.00")


def test_equity_curve_ignores_open_trades():
    assert equity_curve([make_trade("a", status="OPEN", pnl=None)]) == []


def test_streaks():
    trades = [
        make_trade("a", status="LOST", pnl="-1", minutes=0),
        make_trade("b", status="LOST", pnl="-1", minutes=10),
        make_trade("c", status="WON", pnl="1", minutes=20),
        make_trade("d", status="LOST", pnl="-1", minutes=30),
    ]
    assert current_streak(trades) == ("LOST", 1)
    assert longest_losing_streak(trades) == 2


# -------------------------------------------------------------- calibration --

def test_calibration_buckets_and_gap_sign():
    # Three trades predicted ~70%, only one of which won: actual 33%, so the
    # model was overconfident and the gap must be negative.
    trades = [
        make_trade("a", probability="0.72", status="WON", pnl="1"),
        make_trade("b", probability="0.71", status="LOST", pnl="-1"),
        make_trade("c", probability="0.70", status="LOST", pnl="-1"),
    ]
    buckets = calibration(trades)
    assert len(buckets) == 1
    bucket = buckets[0]
    assert bucket.count == 3
    assert bucket.actual_rate == Decimal(1) / Decimal(3)
    assert bucket.gap < 0
    assert bucket.label == "70%-75%"


def test_calibration_excludes_voids():
    trades = [
        make_trade("a", probability="0.72", status="WON", pnl="1"),
        make_trade("v", probability="0.72", status="VOID", pnl="0"),
    ]
    assert calibration(trades)[0].count == 1


def test_brier_score_bounds():
    # A perfect, fully confident forecast scores 0.
    assert brier_score([make_trade("a", probability="1.0", status="WON")]) == Decimal("0")
    # A fully confident forecast that was wrong scores 1.
    assert brier_score([make_trade("a", probability="1.0", status="LOST")]) == Decimal("1")
    # Always saying 50% scores 0.25 either way — the "no information" baseline.
    assert brier_score([make_trade("a", probability="0.5", status="LOST")]) == Decimal("0.25")
    assert brier_score([]) is None


def test_edge_realisation_detects_overclaimed_edge():
    # Claims a 20% edge, actually loses money: shortfall must be negative.
    trades = [make_trade("a", status="LOST", pnl="-5.00", debit="5.00", edge="0.20")]
    result = edge_realisation(trades)
    assert result["claimed_mean_edge"] == Decimal("0.20")
    assert result["realised_roi"] == Decimal("-1")
    assert result["shortfall"] < 0


# ------------------------------------------------------------------ funnel ---

def test_gate_funnel_counts_blocking_gate():
    decisions = [
        {"record_type": "decision", "decision": "skipped", "blocked_by": "entry_window"},
        {"record_type": "decision", "decision": "skipped", "blocked_by": "entry_window"},
        {"record_type": "decision", "decision": "skipped", "blocked_by": "min_edge"},
        {"record_type": "decision", "decision": "traded"},
        {"record_type": "decision", "decision": "error", "blocked_by": "evaluation_completed"},
        {"record_type": "settlement"},  # not an evaluation
    ]
    funnel = gate_funnel(decisions)
    assert funnel.evaluations == 5
    assert funnel.traded == 1
    assert funnel.errors == 1
    assert funnel.blocked[0] == ("entry_window", 2)


def test_group_summaries_splits_by_attribute():
    trades = [
        make_trade("a", asset="BTC", pnl="5.00"),
        make_trade("b", asset="ETH", pnl="-2.00", status="LOST"),
    ]
    groups = group_summaries(trades, "asset")
    assert groups["BTC"].net_pnl == Decimal("5.00")
    assert groups["ETH"].net_pnl == Decimal("-2.00")


# ------------------------------------------------------------------ health ---

def test_health_flags_stale_decision_log():
    now = BASE + timedelta(hours=2)
    decisions = [{"record_type": "decision", "timestamp": BASE.isoformat()}]
    status = health([], decisions, now=now, stale_after_seconds=300)
    assert not status.ok
    assert any("stale" in issue for issue in status.issues)


def test_health_ok_when_recent():
    now = BASE + timedelta(seconds=30)
    decisions = [{"record_type": "decision", "timestamp": BASE.isoformat()}]
    assert health([], decisions, now=now).ok


def test_health_flags_position_stuck_past_close():
    now = BASE + timedelta(hours=3)
    stuck = make_trade("a", status="OPEN", pnl=None, minutes=0)
    decisions = [{"record_type": "decision", "timestamp": now.isoformat()}]
    status = health([stuck], decisions, now=now)
    assert any("OPEN" in issue for issue in status.issues)


def test_health_flags_discovery_outage():
    now = BASE
    decisions = [
        {"record_type": "decision", "timestamp": now.isoformat()},
        {"record_type": "discovery_empty", "consecutive_empty_cycles": 42},
    ]
    status = health([], decisions, now=now)
    assert status.consecutive_empty_discovery == 42
    assert not status.ok


# ---------------------------------------------------------------- exporter ---

def test_exporter_emits_valid_prometheus_format():
    import re

    trades = [make_trade("a", pnl="2.00"), make_trade("b", status="LOST", pnl="-1.00")]
    decisions = [{"record_type": "decision", "decision": "traded", "timestamp": BASE.isoformat()}]
    text = render_metrics(trades, decisions, now=BASE)

    declared = set()
    for line in text.splitlines():
        if line.startswith("# TYPE"):
            declared.add(line.split()[2])
        elif line.startswith("#"):
            continue
        elif line.strip():
            match = re.match(r"^([a-zA-Z_:][\w:]*)(\{.*\})? (-?[\d.eE+-]+)$", line)
            assert match, f"malformed exposition line: {line!r}"
            # Prometheus requires TYPE before the first sample of a metric.
            assert match.group(1) in declared, f"{match.group(1)} has no TYPE"

    assert "kalshi_bot_net_pnl_usd" in declared
    assert "kalshi_bot_brier_score" in declared
    assert 'kalshi_bot_net_pnl_usd{mode="live"} 1' in text


def test_exporter_escapes_label_values():
    # A ticker or regime containing a quote must not break the exposition.
    trade = make_trade("a", regime='we"ird')
    text = render_metrics([trade], [], now=BASE)
    assert r"regime=\"we\"ird\"" in text or r'we\"ird' in text


def test_exporter_survives_empty_state():
    text = render_metrics([], [], now=BASE)
    assert "kalshi_bot_health_ok 0" in text  # no decisions at all is not healthy
    assert list(iter_metric_names(text))


# ----------------------------------------------------------------- sources ---

def test_read_trades_missing_ledger_returns_empty(tmp_path):
    assert read_trades(tmp_path / "nope.db") == []


def test_read_decisions_skips_corrupt_lines(tmp_path):
    path = tmp_path / "decisions.jsonl"
    path.write_text(
        json.dumps({"record_type": "decision", "decision": "traded"}) + "\n"
        + "{ this is not json\n"
        + "\n"
        + json.dumps({"record_type": "settlement"}) + "\n"
        + '{"partial": ',  # a torn final line, as a live writer can leave
    )
    records = read_decisions(path)
    assert len(records) == 2


def test_read_trades_parses_real_schema(tmp_path):
    """Guards against the ledger schema drifting away from the reader."""
    db = tmp_path / "ledger.db"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE trades (
            trade_id TEXT PRIMARY KEY, mode TEXT, environment TEXT, asset TEXT,
            ticker TEXT, event_ticker TEXT, order_id TEXT, client_order_id TEXT,
            outcome TEXT, status TEXT, exchange_status TEXT, market_result TEXT,
            placed_at TEXT, market_open_time TEXT, market_close_time TEXT,
            status_check_time TEXT, last_status_check_at TEXT, settled_at TEXT,
            contracts TEXT, entry_price TEXT, entry_cost TEXT, fees_paid TEXT,
            total_debit TEXT, gross_payout TEXT, pnl TEXT, target_price TEXT,
            model_probability TEXT, model_edge TEXT, metadata_json TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO trades VALUES "
        "('t1','live','production','BTC','KXBTC15M-1','KXBTC15M-1','o','c','yes','WON','finalized','yes',"
        "'2026-08-01T12:00:00+00:00','2026-08-01T11:45:00+00:00','2026-08-01T12:00:00+00:00',"
        "'2026-08-01T11:59:55+00:00',NULL,'2026-08-01T12:00:10+00:00',"
        "'10','0.60','6.00','0.10','6.10','10','3.90','64000','0.70','0.10','{\"regime\":\"TREND\"}')"
    )
    connection.commit()
    connection.close()

    trades = read_trades(db)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.pnl == Decimal("3.90")
    assert trade.regime == "TREND"
    assert trade.is_settled
    # Money must survive as Decimal, never float.
    assert isinstance(trade.total_debit, Decimal)


def test_read_trades_tolerates_unparseable_money(tmp_path):
    """A corrupt value must become None, never a silent zero.

    A zero would be summed into net P&L as "this trade cost nothing".
    """
    db = tmp_path / "ledger.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE trades (trade_id TEXT, mode TEXT, environment TEXT, asset TEXT,"
        " ticker TEXT, event_ticker TEXT, order_id TEXT, client_order_id TEXT,"
        " outcome TEXT, status TEXT, exchange_status TEXT, market_result TEXT,"
        " placed_at TEXT, market_open_time TEXT, market_close_time TEXT,"
        " status_check_time TEXT, last_status_check_at TEXT, settled_at TEXT,"
        " contracts TEXT, entry_price TEXT, entry_cost TEXT, fees_paid TEXT,"
        " total_debit TEXT, gross_payout TEXT, pnl TEXT, target_price TEXT,"
        " model_probability TEXT, model_edge TEXT, metadata_json TEXT)"
    )
    connection.execute(
        "INSERT INTO trades VALUES ('t1','live','p','BTC','k','e','o','c','yes','WON','f','yes',"
        "NULL,NULL,NULL,NULL,NULL,NULL,'10','0.6','6','0.1','NOT_A_NUMBER','10','3.9','64000',"
        "'0.7','0.1','not json either')"
    )
    connection.commit()
    connection.close()

    trade = read_trades(db)[0]
    assert trade.total_debit is None
    assert trade.metadata == {}


# -------------------------------------------------------------------- chart --

def test_choose_interval_respects_kraken_row_cap():
    from monitoring.chart import MAX_KRAKEN_ROWS, choose_interval

    for hours in (6, 12, 24, 48, 168):
        interval = choose_interval(hours)
        assert hours * 60 / interval <= MAX_KRAKEN_ROWS


def test_market_intervals_land_on_quarter_hours():
    from monitoring.chart import market_intervals

    start = datetime(2026, 8, 1, 12, 7, tzinfo=timezone.utc)
    intervals = market_intervals(start, start + timedelta(hours=1))
    opens = [datetime.fromisoformat(i["open"]) for i in intervals]
    assert all(o.minute in {0, 15, 30, 45} for o in opens)
    assert all(o.second == 0 for o in opens)


def test_rolling_indicators_do_not_read_the_future():
    """The value at index i must not change when later candles are removed.

    A backtest or chart that leaks future bars makes every past decision look
    better informed than it was.
    """
    from core.strategy_brain.strategies.candles import PriceCandle
    from monitoring.chart import rolling_indicators

    candles = []
    price = Decimal("64000")
    for i in range(60):
        price += Decimal(1 if i % 3 else -2)
        candles.append(
            PriceCandle(
                timestamp=BASE + timedelta(minutes=i),
                open=price, high=price + 5, low=price - 5,
                close=price, volume=Decimal("1"),
            )
        )

    full = rolling_indicators(candles)
    truncated = rolling_indicators(candles[:40])
    for i in range(40):
        assert full[i] == truncated[i], f"indicator at {i} depends on a future candle"


def test_trade_markers_filter_by_asset_and_window():
    from monitoring.chart import trade_markers

    trades = [
        make_trade("a", asset="BTC", minutes=30),
        make_trade("b", asset="ETH", minutes=30),
    ]
    markers = trade_markers(trades, "BTC", BASE - timedelta(hours=1))
    assert len(markers) == 1 and markers[0]["trade_id"] == "a"
    # Anything before the window start is excluded.
    assert trade_markers(trades, "BTC", BASE + timedelta(hours=5)) == []


# ----------------------------------------------------- incremental tailing


def test_the_tail_parses_only_what_was_appended(tmp_path) -> None:
    """A full re-parse every few seconds was the dashboard's dominant cost."""
    from monitoring.sources import DecisionTail

    path = tmp_path / "decisions.jsonl"
    path.write_text('{"record_type":"decision","asset":"BTC"}\n')
    tail = DecisionTail(path)
    assert len(tail.load()) == 1

    with path.open("a") as handle:
        handle.write('{"record_type":"decision","asset":"ETH"}\n')

    records = tail.load()
    assert [r["asset"] for r in records] == ["BTC", "ETH"]
    assert tail.load() == records, "an unchanged file re-parses nothing"


def test_the_tail_rereads_from_zero_after_rotation(tmp_path) -> None:
    """Rotation truncates in place, so the offset stops meaning anything."""
    from monitoring.sources import DecisionTail

    path = tmp_path / "decisions.jsonl"
    path.write_text('{"record_type":"decision","asset":"BTC"}\n' * 3)
    tail = DecisionTail(path)
    assert len(tail.load()) == 3

    path.write_text('{"record_type":"decision","asset":"SOL"}\n')

    records = tail.load()
    assert [r["asset"] for r in records] == ["SOL"]


def test_a_half_written_line_is_read_once_it_is_whole(tmp_path) -> None:
    from monitoring.sources import DecisionTail

    path = tmp_path / "decisions.jsonl"
    path.write_text('{"record_type":"decision","asset":"BTC"}\n{"record_ty')
    tail = DecisionTail(path)
    assert len(tail.load()) == 1

    with path.open("a") as handle:
        handle.write('pe":"decision","asset":"ETH"}\n')

    assert [r["asset"] for r in tail.load()] == ["BTC", "ETH"]
